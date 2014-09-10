"""
Tracks bandwidth usage of the tor process, expanding to include accounting
stats if they're set.
"""

import calendar
import os
import time
import curses

import arm.controller

from arm.graphing import graph_panel
from arm.util import tor_controller

from stem.control import State
from stem.util import conf, log, str_tools, system

from arm.util import msg


def conf_handler(key, value):
  if key == 'features.graph.bw.accounting.rate':
    return max(1, value)


CONFIG = conf.config_dict('arm', {
  'features.graph.bw.transferInBytes': False,
  'features.graph.bw.accounting.show': True,
  'features.graph.bw.accounting.rate': 10,
  'features.graph.bw.accounting.isTimeLong': False,
  'tor.chroot': '',
}, conf_handler)

DL_COLOR, UL_COLOR = 'green', 'cyan'

# width at which panel abandons placing optional stats (avg and total) with
# header in favor of replacing the x-axis label

COLLAPSE_WIDTH = 135

# valid keys for the accounting_info mapping

ACCOUNTING_ARGS = ('status', 'reset_time', 'read', 'written', 'read_limit', 'writtenLimit')


class BandwidthStats(graph_panel.GraphStats):
  """
  Uses tor BW events to generate bandwidth usage graph.
  """

  def __init__(self, is_pause_buffer = False):
    graph_panel.GraphStats.__init__(self)

    # stats prepopulated from tor's state file

    self.prepopulate_primary_total = 0
    self.prepopulate_secondary_total = 0
    self.prepopulate_ticks = 0

    # accounting data (set by _update_accounting_info method)

    self.accounting_last_updated = 0
    self.accounting_info = dict([(arg, '') for arg in ACCOUNTING_ARGS])

    # listens for tor reload (sighup) events which can reset the bandwidth
    # rate/burst and if tor's using accounting

    controller = tor_controller()
    self._title_stats, self.is_accounting = [], False

    if not is_pause_buffer:
      self.reset_listener(controller, State.INIT, None)  # initializes values

    controller.add_status_listener(self.reset_listener)

    # Initialized the bandwidth totals to the values reported by Tor. This
    # uses a controller options introduced in ticket 2345:
    # https://trac.torproject.org/projects/tor/ticket/2345
    #
    # further updates are still handled via BW events to avoid unnecessary
    # GETINFO requests.

    self.initial_primary_total = 0
    self.initial_secondary_total = 0

    read_total = controller.get_info('traffic/read', None)

    if read_total and read_total.isdigit():
      self.initial_primary_total = int(read_total) / 1024  # Bytes -> KB

    write_total = controller.get_info('traffic/written', None)

    if write_total and write_total.isdigit():
      self.initial_secondary_total = int(write_total) / 1024  # Bytes -> KB

  def clone(self, new_copy = None):
    if not new_copy:
      new_copy = BandwidthStats(True)

    new_copy.accounting_last_updated = self.accounting_last_updated
    new_copy.accounting_info = self.accounting_info

    # attributes that would have been initialized from calling the reset_listener

    new_copy.is_accounting = self.is_accounting
    new_copy._title_stats = self._title_stats

    return graph_panel.GraphStats.clone(self, new_copy)

  def reset_listener(self, controller, event_type, _):
    # updates title parameters and accounting status if they changed

    self._title_stats = []     # force reset of title
    self.new_desc_event(None)  # updates title params

    if event_type in (State.INIT, State.RESET) and CONFIG['features.graph.bw.accounting.show']:
      is_accounting_enabled = controller.get_info('accounting/enabled', None) == '1'

      if is_accounting_enabled != self.is_accounting:
        self.is_accounting = is_accounting_enabled

        # redraws the whole screen since our height changed

        arm.controller.get_controller().redraw()

    # redraws to reflect changes (this especially noticeable when we have
    # accounting and shut down since it then gives notice of the shutdown)

    if self._graph_panel and self.is_selected:
      self._graph_panel.redraw(True)

  def prepopulate_from_state(self):
    """
    Attempts to use tor's state file to prepopulate values for the 15 minute
    interval via the BWHistoryReadValues/BWHistoryWriteValues values. This
    returns True if successful and False otherwise.
    """

    controller = tor_controller()

    if not controller.is_localhost():
      raise ValueError('we can only prepopulate bandwidth information for a local tor instance')

    start_time = system.start_time(controller.get_pid(None))
    uptime = time.time() - start_time if start_time else None

    # Only attempt to prepopulate information if we've been running for a day.
    # Reason is that the state file stores a day's worth of data, and we don't
    # want to prepopulate with information from a prior tor instance.

    if not uptime:
      raise ValueError("unable to determine tor's uptime")
    elif uptime < (24 * 60 * 60):
      raise ValueError("insufficient uptime, tor must've been running for at least a day")

    # read the user's state file in their data directory (usually '~/.tor')

    data_dir = controller.get_conf('DataDirectory', None)

    if not data_dir:
      raise ValueError("unable to determine tor's data directory")

    state_path = os.path.join(CONFIG['tor.chroot'] + data_dir, 'state')

    try:
      with open(state_path) as state_file:
        state_content = state_file.readlines()
    except IOError as exc:
      raise ValueError('unable to read the state file at %s, %s' % (state_path, exc))

    # We're interested in two types of entries from our state file...
    #
    # * BWHistory*Values - Comma separated list of bytes we read or wrote
    #   during each fifteen minute period. The last value is an incremental
    #   counter for our current period, so ignoring that.
    #
    # * BWHistory*Ends - When our last sampling was recorded, in UTC.

    bw_read_entries, bw_write_entries = None, None
    missing_read_entries, missing_write_entries = None, None

    for line in state_content:
      line = line.strip()

      if line.startswith('BWHistoryReadValues '):
        bw_read_entries = [int(entry) / 1024.0 / 900 for entry in line[20:].split(',')[:-1]]
      elif line.startswith('BWHistoryWriteValues '):
        bw_write_entries = [int(entry) / 1024.0 / 900 for entry in line[21:].split(',')[:-1]]
      elif line.startswith('BWHistoryReadEnds '):
        last_read_time = calendar.timegm(time.strptime(line[18:], '%Y-%m-%d %H:%M:%S')) - 900
        missing_read_entries = int((time.time() - last_read_time) / 900)
      elif line.startswith('BWHistoryWriteEnds '):
        last_write_time = calendar.timegm(time.strptime(line[19:], '%Y-%m-%d %H:%M:%S')) - 900
        missing_write_entries = int((time.time() - last_write_time) / 900)

    if not bw_read_entries or not bw_write_entries or not last_read_time or not last_write_time:
      raise ValueError('bandwidth stats missing from state file')

    # fills missing entries with the last value

    bw_read_entries += [bw_read_entries[-1]] * missing_read_entries
    bw_write_entries += [bw_write_entries[-1]] * missing_write_entries

    # crops starting entries so they're the same size

    entry_count = min(len(bw_read_entries), len(bw_write_entries), self.max_column)
    bw_read_entries = bw_read_entries[len(bw_read_entries) - entry_count:]
    bw_write_entries = bw_write_entries[len(bw_write_entries) - entry_count:]

    # gets index for 15-minute interval

    interval_index = 0

    for index_entry in graph_panel.UPDATE_INTERVALS:
      if index_entry[1] == 900:
        break
      else:
        interval_index += 1

    # fills the graphing parameters with state information

    for i in range(entry_count):
      read_value, write_value = bw_read_entries[i], bw_write_entries[i]

      self.last_primary, self.last_secondary = read_value, write_value

      self.prepopulate_primary_total += read_value * 900
      self.prepopulate_secondary_total += write_value * 900
      self.prepopulate_ticks += 900

      self.primary_counts[interval_index].insert(0, read_value)
      self.secondary_counts[interval_index].insert(0, write_value)

    self.max_primary[interval_index] = max(self.primary_counts)
    self.max_secondary[interval_index] = max(self.secondary_counts)

    del self.primary_counts[interval_index][self.max_column + 1:]
    del self.secondary_counts[interval_index][self.max_column + 1:]

    return time.time() - min(last_read_time, last_write_time)

  def bandwidth_event(self, event):
    if self.is_accounting and self.is_next_tick_redraw():
      if time.time() - self.accounting_last_updated >= CONFIG['features.graph.bw.accounting.rate']:
        self._update_accounting_info()

    # scales units from B to KB for graphing

    self._process_event(event.read / 1024.0, event.written / 1024.0)

  def draw(self, panel, width, height):
    # line of the graph's x-axis labeling

    labeling_line = graph_panel.GraphStats.get_content_height(self) + panel.graph_height - 2

    # if display is narrow, overwrites x-axis labels with avg / total stats

    if width <= COLLAPSE_WIDTH:
      # clears line

      panel.addstr(labeling_line, 0, ' ' * width)
      graph_column = min((width - 10) / 2, self.max_column)

      primary_footer = '%s, %s' % (self._get_avg_label(True), self._get_total_label(True))
      secondary_footer = '%s, %s' % (self._get_avg_label(False), self._get_total_label(False))

      panel.addstr(labeling_line, 1, primary_footer, self.get_color(True))
      panel.addstr(labeling_line, graph_column + 6, secondary_footer, self.get_color(False))

    # provides accounting stats if enabled

    if self.is_accounting:
      if tor_controller().is_alive():
        status = self.accounting_info['status']

        hibernate_color = 'green'

        if status == 'soft':
          hibernate_color = 'yellow'
        elif status == 'hard':
          hibernate_color = 'red'
        elif status == '':
          # failed to be queried
          status, hibernate_color = 'unknown', 'red'

        panel.addstr(labeling_line + 2, 0, 'Accounting (', curses.A_BOLD)
        panel.addstr(labeling_line + 2, 12, status, curses.A_BOLD, hibernate_color)
        panel.addstr(labeling_line + 2, 12 + len(status), ')', curses.A_BOLD)

        reset_time = self.accounting_info['reset_time']

        if not reset_time:
          reset_time = 'unknown'

        panel.addstr(labeling_line + 2, 35, 'Time to reset: %s' % reset_time)

        used, total = self.accounting_info['read'], self.accounting_info['read_limit']

        if used and total:
          panel.addstr(labeling_line + 3, 2, '%s / %s' % (used, total), self.get_color(True))

        used, total = self.accounting_info['written'], self.accounting_info['writtenLimit']

        if used and total:
          panel.addstr(labeling_line + 3, 37, '%s / %s' % (used, total), self.get_color(False))
      else:
        panel.addstr(labeling_line + 2, 0, 'Accounting:', curses.A_BOLD)
        panel.addstr(labeling_line + 2, 12, 'Connection Closed...')

  def get_title(self, width):
    stats = list(self._title_stats)

    while True:
      if not stats:
        return 'Bandwidth:'
      else:
        label = 'Bandwidth (%s):' % ', '.join(stats)

        if len(label) > width:
          del stats[-1]
        else:
          return label

  def get_header_label(self, width, is_primary):
    graph_type = 'Download' if is_primary else 'Upload'
    stats = ['']

    # if wide then avg and total are part of the header, otherwise they're on
    # the x-axis

    if width * 2 > COLLAPSE_WIDTH:
      stats = [''] * 3
      stats[1] = '- %s' % self._get_avg_label(is_primary)
      stats[2] = ', %s' % self._get_total_label(is_primary)

    stats[0] = '%-14s' % ('%s/sec' % str_tools.size_label((self.last_primary if is_primary else self.last_secondary) * 1024, 1, False, CONFIG['features.graph.bw.transferInBytes']))

    # drops label's components if there's not enough space

    labeling = graph_type + ' (' + ''.join(stats).strip() + '):'

    while len(labeling) >= width:
      if len(stats) > 1:
        del stats[-1]
        labeling = graph_type + ' (' + ''.join(stats).strip() + '):'
      else:
        labeling = graph_type + ':'
        break

    return labeling

  def get_color(self, is_primary):
    return DL_COLOR if is_primary else UL_COLOR

  def get_content_height(self):
    base_height = graph_panel.GraphStats.get_content_height(self)
    return base_height + 3 if self.is_accounting else base_height

  def new_desc_event(self, event):
    # updates self._title_stats with updated values

    controller = tor_controller()

    if not controller.is_alive():
      return  # keep old values

    my_fingerprint = controller.get_info('fingerprint', None)

    if not self._title_stats or not my_fingerprint or (event and my_fingerprint in event.idlist):
      stats = []
      bw_rate = _min_config(controller, 'BandwidthRate', 'RelayBandwidthRate', 'MaxAdvertisedBandwidth')
      bw_burst = _min_config(controller, 'BandwidthBurst', 'RelayBandwidthBurst')

      my_server_descriptor = controller.get_server_descriptor(default = None)
      bw_observed = getattr(my_server_descriptor, 'observed_bandwidth', None)

      my_router_status_entry = controller.get_network_status(default = None)
      bw_measured = getattr(my_router_status_entry, 'bandwidth', None)

      label_in_bytes = CONFIG['features.graph.bw.transferInBytes']

      if bw_rate and bw_burst:
        bw_rate_label = str_tools.size_label(bw_rate, 1, False, label_in_bytes)
        bw_burst_label = str_tools.size_label(bw_burst, 1, False, label_in_bytes)

        # if both are using rounded values then strip off the '.0' decimal

        if '.0' in bw_rate_label and '.0' in bw_burst_label:
          bw_rate_label = bw_rate_label.replace('.0', '')
          bw_burst_label = bw_burst_label.replace('.0', '')

        stats.append('limit: %s/s' % bw_rate_label)
        stats.append('burst: %s/s' % bw_burst_label)

      # Provide the observed bandwidth either if the measured bandwidth isn't
      # available or if the measured bandwidth is the observed (this happens
      # if there isn't yet enough bandwidth measurements).

      if bw_observed and (not bw_measured or bw_measured == bw_observed):
        stats.append('observed: %s/s' % str_tools.size_label(bw_observed, 1, False, label_in_bytes))
      elif bw_measured:
        stats.append('measured: %s/s' % str_tools.size_label(bw_measured, 1, False, label_in_bytes))

      self._title_stats = stats

  def _get_avg_label(self, is_primary):
    total = self.primary_total if is_primary else self.secondary_total
    total += self.prepopulate_primary_total if is_primary else self.prepopulate_secondary_total

    return 'avg: %s/sec' % str_tools.size_label((total / max(1, self.tick + self.prepopulate_ticks)) * 1024, 1, False, CONFIG['features.graph.bw.transferInBytes'])

  def _get_total_label(self, is_primary):
    total = self.primary_total if is_primary else self.secondary_total
    total += self.initial_primary_total if is_primary else self.initial_secondary_total
    return 'total: %s' % str_tools.size_label(total * 1024, 1)

  def _update_accounting_info(self):
    """
    Updates mapping used for accounting info. This includes the following keys:
    status, reset_time, read, written, read_limit, writtenLimit

    Any failed lookups result in a mapping to an empty string.
    """

    controller = tor_controller()
    queried = dict([(arg, '') for arg in ACCOUNTING_ARGS])
    queried['status'] = controller.get_info('accounting/hibernating', None)

    # provides a nicely formatted reset time

    end_interval = controller.get_info('accounting/interval-end', None)

    if end_interval:
      # converts from gmt to local with respect to DST

      sec = calendar.timegm(time.strptime(end_interval, '%Y-%m-%d %H:%M:%S')) - time.time()

      if CONFIG['features.graph.bw.accounting.isTimeLong']:
        queried['reset_time'] = ', '.join(str_tools.time_labels(sec, True))
      else:
        days = sec / 86400
        sec %= 86400
        hours = sec / 3600
        sec %= 3600
        minutes = sec / 60
        sec %= 60
        queried['reset_time'] = '%i:%02i:%02i:%02i' % (days, hours, minutes, sec)

    # number of bytes used and in total for the accounting period

    used = controller.get_info('accounting/bytes', None)
    left = controller.get_info('accounting/bytes-left', None)

    if used and left:
      used_comp, left_comp = used.split(' '), left.split(' ')
      read, written = int(used_comp[0]), int(used_comp[1])
      read_left, written_left = int(left_comp[0]), int(left_comp[1])

      queried['read'] = str_tools.size_label(read)
      queried['written'] = str_tools.size_label(written)
      queried['read_limit'] = str_tools.size_label(read + read_left)
      queried['writtenLimit'] = str_tools.size_label(written + written_left)

    self.accounting_info = queried
    self.accounting_last_updated = time.time()


def _min_config(controller, *attributes):
  """
  Provides the minimum of the given numeric bandwidth rate or burst config
  options.
  """

  value = None

  for attr in attributes:
    try:
      attr_value = int(controller.get_conf(attr))

      if attr_value == 0 and attr.startswith('Relay'):
        continue  # RelayBandwidthRate and RelayBandwidthBurst default to zero

      value = min(value, attr_value) if value else attr_value
    except:
      pass

  return value
