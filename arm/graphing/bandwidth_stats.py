"""
Tracks bandwidth usage of the tor process, expanding to include accounting
stats if they're set.
"""

import time
import curses

import arm.controller

from arm.graphing import graph_panel
from arm.util import bandwidth_from_state, tor_controller

from stem.control import State
from stem.util import conf, str_tools, system


def conf_handler(key, value):
  if key == 'features.graph.bw.accounting.rate':
    return max(1, value)


CONFIG = conf.config_dict('arm', {
  'attr.hibernate_color': {},
  'features.graph.bw.transferInBytes': False,
  'features.graph.bw.accounting.show': True,
  'features.graph.bw.accounting.rate': 10,
  'tor.chroot': '',
}, conf_handler)

DL_COLOR, UL_COLOR = 'green', 'cyan'

# width at which panel abandons placing optional stats (avg and total) with
# header in favor of replacing the x-axis label

COLLAPSE_WIDTH = 135


class BandwidthStats(graph_panel.GraphStats):
  """
  Uses tor BW events to generate bandwidth usage graph.
  """

  def __init__(self, is_pause_buffer = False):
    graph_panel.GraphStats.__init__(self)

    self.accounting_stats = None

    # listens for tor reload (sighup) events which can reset the bandwidth
    # rate/burst and if tor's using accounting

    controller = tor_controller()
    self._title_stats = []

    if not is_pause_buffer:
      self.reset_listener(controller, State.INIT, None)  # initializes values

    controller.add_status_listener(self.reset_listener)

    # We both show our 'total' attributes and use it to determine our average.
    #
    # If we can get *both* our start time and the totals from tor (via 'GETINFO
    # traffic/*') then that's ideal, but if not then just track the total for
    # the time arm is run.

    read_total = controller.get_info('traffic/read', None)
    write_total = controller.get_info('traffic/written', None)
    start_time = system.start_time(controller.get_pid(None))

    if read_total and write_total and start_time:
      self.primary_total = int(read_total) / 1024  # Bytes -> KB
      self.secondary_total = int(write_total) / 1024  # Bytes -> KB
      self.start_time = start_time
    else:
      self.start_time = time.time()

  def clone(self, new_copy = None):
    if not new_copy:
      new_copy = BandwidthStats(True)

    new_copy.accounting_stats = self.accounting_stats

    # attributes that would have been initialized from calling the reset_listener

    new_copy._title_stats = self._title_stats

    return graph_panel.GraphStats.clone(self, new_copy)

  def reset_listener(self, controller, event_type, _):
    # updates title parameters and accounting status if they changed

    self._title_stats = []     # force reset of title
    self.new_desc_event(None)  # updates title params

    if event_type in (State.INIT, State.RESET) and CONFIG['features.graph.bw.accounting.show']:
      is_accounting_enabled = controller.get_info('accounting/enabled', None) == '1'

      if is_accounting_enabled != bool(self.accounting_stats):
        self.accounting_stats = tor_controller().get_accounting_stats(None)

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

    stats = bandwidth_from_state()

    missing_read_entries = int((time.time() - stats.last_read_time) / 900)
    missing_write_entries = int((time.time() - stats.last_write_time) / 900)

    # fills missing entries with the last value

    bw_read_entries = stats.read_entries + [stats.read_entries[-1]] * missing_read_entries
    bw_write_entries = stats.write_entries + [stats.write_entries[-1]] * missing_write_entries

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

      self.primary_counts[interval_index].insert(0, read_value)
      self.secondary_counts[interval_index].insert(0, write_value)

    self.max_primary[interval_index] = max(self.primary_counts)
    self.max_secondary[interval_index] = max(self.secondary_counts)

    del self.primary_counts[interval_index][self.max_column + 1:]
    del self.secondary_counts[interval_index][self.max_column + 1:]

    return time.time() - min(stats.last_read_time, stats.last_write_time)

  def bandwidth_event(self, event):
    if self.accounting_stats and self.is_next_tick_redraw():
      if time.time() - self.accounting_stats.retrieved >= CONFIG['features.graph.bw.accounting.rate']:
        self.accounting_stats = tor_controller().get_accounting_stats(None)

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

      primary_total = 'total: %s' % _size_label(self.primary_total * 1024)
      primary_average = 'avg: %s/sec' % _size_label(self.primary_total / (time.time() - self.start_time) * 1024)
      primary_footer = '%s, %s' % (primary_average, primary_total)

      secondary_total = 'total: %s' % _size_label(self.secondary_total * 1024)
      secondary_average = 'avg: %s/sec' % _size_label(self.primary_total / (time.time() - self.start_time) * 1024)
      secondary_footer = '%s, %s' % (secondary_average, secondary_total)

      panel.addstr(labeling_line, 1, primary_footer, self.get_color(True))
      panel.addstr(labeling_line, graph_column + 6, secondary_footer, self.get_color(False))

    # provides accounting stats if enabled

    if self.accounting_stats:
      if tor_controller().is_alive():
        hibernate_color = CONFIG['attr.hibernate_color'].get(self.accounting_stats.status, 'red')

        x, y = 0, labeling_line + 2
        x = panel.addstr(y, x, 'Accounting (', curses.A_BOLD)
        x = panel.addstr(y, x, self.accounting_stats.status, curses.A_BOLD, hibernate_color)
        x = panel.addstr(y, x, ')', curses.A_BOLD)

        panel.addstr(y, 35, 'Time to reset: %s' % str_tools.short_time_label(self.accounting_stats.time_until_reset))

        panel.addstr(y + 1, 2, '%s / %s' % (self.accounting_stats.read_bytes, self.accounting_stats.read_limit), self.get_color(True))
        panel.addstr(y + 1, 37, '%s / %s' % (self.accounting_stats.written_bytes, self.accounting_stats.write_limit), self.get_color(True))
      else:
        panel.addstr(labeling_line + 2, 0, 'Accounting:', curses.A_BOLD)
        panel.addstr(labeling_line + 2, 12, 'Connection Closed...')

  def get_title(self, width):
    stats_label = str_tools.join(self._title_stats, ', ', width - 13)

    if stats_label:
      return 'Bandwidth (%s):' % stats_label
    else:
      return 'Bandwidth:'

  def primary_header(self, width):
    stats = ['%-14s' % ('%s/sec' % _size_label(self.last_primary * 1024))]

    # if wide then avg and total are part of the header, otherwise they're on
    # the x-axis

    if width * 2 > COLLAPSE_WIDTH:
      stats.append('- avg: %s/sec' % _size_label(self.primary_total / (time.time() - self.start_time) * 1024))
      stats.append(', total: %s' % _size_label(self.primary_total * 1024))

    stats_label = str_tools.join(stats, '', width - 12)

    if stats_label:
      return 'Download (%s):' % stats_label
    else:
      return 'Download:'

  def secondary_header(self, width):
    stats = ['%-14s' % ('%s/sec' % _size_label(self.last_secondary * 1024))]

    # if wide then avg and total are part of the header, otherwise they're on
    # the x-axis

    if width * 2 > COLLAPSE_WIDTH:
      stats.append('- avg: %s/sec' % _size_label(self.secondary_total / (time.time() - self.start_time) * 1024))
      stats.append(', total: %s' % _size_label(self.secondary_total * 1024))

    stats_label = str_tools.join(stats, '', width - 10)

    if stats_label:
      return 'Upload (%s):' % stats_label
    else:
      return 'Upload:'

  def get_color(self, is_primary):
    return DL_COLOR if is_primary else UL_COLOR

  def get_content_height(self):
    base_height = graph_panel.GraphStats.get_content_height(self)
    return base_height + 3 if self.accounting_stats else base_height

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

      if bw_rate and bw_burst:
        bw_rate_label = _size_label(bw_rate)
        bw_burst_label = _size_label(bw_burst)

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
        stats.append('observed: %s/s' % _size_label(bw_observed))
      elif bw_measured:
        stats.append('measured: %s/s' % _size_label(bw_measured))

      self._title_stats = stats


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


def _size_label(byte_count):
  return str_tools.size_label(byte_count, 1, is_bytes = CONFIG['features.graph.bw.transferInBytes'])
