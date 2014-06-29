"""
Tracks bandwidth usage of the tor process, expanding to include accounting
stats if they're set.
"""

import time
import curses

import arm.controller

from arm.graphing import graph_panel
from arm.util import tor_controller, ui_tools

from stem.control import State
from stem.util import conf, log, str_tools, system


def conf_handler(key, value):
  if key == "features.graph.bw.accounting.rate":
    return max(1, value)


CONFIG = conf.config_dict("arm", {
  "features.graph.bw.transferInBytes": False,
  "features.graph.bw.accounting.show": True,
  "features.graph.bw.accounting.rate": 10,
  "features.graph.bw.accounting.isTimeLong": False,
  "tor.chroot": "",
}, conf_handler)

DL_COLOR, UL_COLOR = "green", "cyan"

# width at which panel abandons placing optional stats (avg and total) with
# header in favor of replacing the x-axis label

COLLAPSE_WIDTH = 135

# valid keys for the accounting_info mapping

ACCOUNTING_ARGS = ("status", "reset_time", "read", "written", "read_limit", "writtenLimit")

PREPOPULATE_SUCCESS_MSG = "Read the last day of bandwidth history from the state file"
PREPOPULATE_FAILURE_MSG = "Unable to prepopulate bandwidth information (%s)"


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
    self.accounting_info = dict([(arg, "") for arg in ACCOUNTING_ARGS])

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

    read_total = controller.get_info("traffic/read", None)

    if read_total and read_total.isdigit():
      self.initial_primary_total = int(read_total) / 1024  # Bytes -> KB

    write_total = controller.get_info("traffic/written", None)

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

    if event_type in (State.INIT, State.RESET) and CONFIG["features.graph.bw.accounting.show"]:
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

    # checks that this is a relay (if ORPort is unset, then skip)

    controller = tor_controller()
    or_port = controller.get_conf("ORPort", None)

    if or_port == "0":
      return

    # gets the uptime (using the same parameters as the header panel to take
    # advantage of caching)
    # TODO: stem dropped system caching support so we'll need to think of
    # something else

    uptime = None
    query_pid = controller.get_pid(None)

    if query_pid:
      query_param = ["%cpu", "rss", "%mem", "etime"]
      query_cmd = "ps -p %s -o %s" % (query_pid, ",".join(query_param))
      ps_call = system.call(query_cmd, None)

      if ps_call and len(ps_call) == 2:
        stats = ps_call[1].strip().split()

        if len(stats) == 4:
          uptime = stats[3]

    # checks if tor has been running for at least a day, the reason being that
    # the state tracks a day's worth of data and this should only prepopulate
    # results associated with this tor instance

    if not uptime or not "-" in uptime:
      msg = PREPOPULATE_FAILURE_MSG % "insufficient uptime"
      log.notice(msg)
      return False

    # get the user's data directory (usually '~/.tor')

    data_dir = controller.get_conf("DataDirectory", None)

    if not data_dir:
      msg = PREPOPULATE_FAILURE_MSG % "data directory not found"
      log.notice(msg)
      return False

    # attempt to open the state file

    try:
      state_file = open("%s%s/state" % (CONFIG['tor.chroot'], data_dir), "r")
    except IOError:
      msg = PREPOPULATE_FAILURE_MSG % "unable to read the state file"
      log.notice(msg)
      return False

    # get the BWHistory entries (ordered oldest to newest) and number of
    # intervals since last recorded

    bw_read_entries, bw_write_entries = None, None
    missing_read_entries, missing_write_entries = None, None

    # converts from gmt to local with respect to DST

    tz_offset = time.altzone if time.localtime()[8] else time.timezone

    for line in state_file:
      line = line.strip()

      # According to the rep_hist_update_state() function the BWHistory*Ends
      # correspond to the start of the following sampling period. Also, the
      # most recent values of BWHistory*Values appear to be an incremental
      # counter for the current sampling period. Hence, offsets are added to
      # account for both.

      if line.startswith("BWHistoryReadValues"):
        bw_read_entries = line[20:].split(",")
        bw_read_entries = [int(entry) / 1024.0 / 900 for entry in bw_read_entries]
        bw_read_entries.pop()
      elif line.startswith("BWHistoryWriteValues"):
        bw_write_entries = line[21:].split(",")
        bw_write_entries = [int(entry) / 1024.0 / 900 for entry in bw_write_entries]
        bw_write_entries.pop()
      elif line.startswith("BWHistoryReadEnds"):
        last_read_time = time.mktime(time.strptime(line[18:], "%Y-%m-%d %H:%M:%S")) - tz_offset
        last_read_time -= 900
        missing_read_entries = int((time.time() - last_read_time) / 900)
      elif line.startswith("BWHistoryWriteEnds"):
        last_write_time = time.mktime(time.strptime(line[19:], "%Y-%m-%d %H:%M:%S")) - tz_offset
        last_write_time -= 900
        missing_write_entries = int((time.time() - last_write_time) / 900)

    if not bw_read_entries or not bw_write_entries or not last_read_time or not last_write_time:
      msg = PREPOPULATE_FAILURE_MSG % "bandwidth stats missing from state file"
      log.notice(msg)
      return False

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

    msg = PREPOPULATE_SUCCESS_MSG
    missing_sec = time.time() - min(last_read_time, last_write_time)

    if missing_sec:
      msg += " (%s is missing)" % str_tools.get_time_label(missing_sec, 0, True)

    log.notice(msg)

    return True

  def bandwidth_event(self, event):
    if self.is_accounting and self.is_next_tick_redraw():
      if time.time() - self.accounting_last_updated >= CONFIG["features.graph.bw.accounting.rate"]:
        self._update_accounting_info()

    # scales units from B to KB for graphing

    self._process_event(event.read / 1024.0, event.written / 1024.0)

  def draw(self, panel, width, height):
    # line of the graph's x-axis labeling

    labeling_line = graph_panel.GraphStats.get_content_height(self) + panel.graph_height - 2

    # if display is narrow, overwrites x-axis labels with avg / total stats

    if width <= COLLAPSE_WIDTH:
      # clears line

      panel.addstr(labeling_line, 0, " " * width)
      graph_column = min((width - 10) / 2, self.max_column)

      primary_footer = "%s, %s" % (self._get_avg_label(True), self._get_total_label(True))
      secondary_footer = "%s, %s" % (self._get_avg_label(False), self._get_total_label(False))

      panel.addstr(labeling_line, 1, primary_footer, ui_tools.get_color(self.get_color(True)))
      panel.addstr(labeling_line, graph_column + 6, secondary_footer, ui_tools.get_color(self.get_color(False)))

    # provides accounting stats if enabled

    if self.is_accounting:
      if tor_controller().is_alive():
        status = self.accounting_info["status"]

        hibernate_color = "green"

        if status == "soft":
          hibernate_color = "yellow"
        elif status == "hard":
          hibernate_color = "red"
        elif status == "":
          # failed to be queried
          status, hibernate_color = "unknown", "red"

        panel.addstr(labeling_line + 2, 0, "Accounting (", curses.A_BOLD)
        panel.addstr(labeling_line + 2, 12, status, curses.A_BOLD | ui_tools.get_color(hibernate_color))
        panel.addstr(labeling_line + 2, 12 + len(status), ")", curses.A_BOLD)

        reset_time = self.accounting_info["reset_time"]

        if not reset_time:
          reset_time = "unknown"

        panel.addstr(labeling_line + 2, 35, "Time to reset: %s" % reset_time)

        used, total = self.accounting_info["read"], self.accounting_info["read_limit"]

        if used and total:
          panel.addstr(labeling_line + 3, 2, "%s / %s" % (used, total), ui_tools.get_color(self.get_color(True)))

        used, total = self.accounting_info["written"], self.accounting_info["writtenLimit"]

        if used and total:
          panel.addstr(labeling_line + 3, 37, "%s / %s" % (used, total), ui_tools.get_color(self.get_color(False)))
      else:
        panel.addstr(labeling_line + 2, 0, "Accounting:", curses.A_BOLD)
        panel.addstr(labeling_line + 2, 12, "Connection Closed...")

  def get_title(self, width):
    stats = list(self._title_stats)

    while True:
      if not stats:
        return "Bandwidth:"
      else:
        label = "Bandwidth (%s):" % ", ".join(stats)

        if len(label) > width:
          del stats[-1]
        else:
          return label

  def get_header_label(self, width, is_primary):
    graph_type = "Download" if is_primary else "Upload"
    stats = [""]

    # if wide then avg and total are part of the header, otherwise they're on
    # the x-axis

    if width * 2 > COLLAPSE_WIDTH:
      stats = [""] * 3
      stats[1] = "- %s" % self._get_avg_label(is_primary)
      stats[2] = ", %s" % self._get_total_label(is_primary)

    stats[0] = "%-14s" % ("%s/sec" % str_tools.get_size_label((self.last_primary if is_primary else self.last_secondary) * 1024, 1, False, CONFIG["features.graph.bw.transferInBytes"]))

    # drops label's components if there's not enough space

    labeling = graph_type + " (" + "".join(stats).strip() + "):"

    while len(labeling) >= width:
      if len(stats) > 1:
        del stats[-1]
        labeling = graph_type + " (" + "".join(stats).strip() + "):"
      else:
        labeling = graph_type + ":"
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

    my_fingerprint = controller.get_info("fingerprint", None)

    if not self._title_stats or not my_fingerprint or (event and my_fingerprint in event.idlist):
      stats = []
      bw_rate = get_my_bandwidth_rate(controller)
      bw_burst = get_my_bandwidth_burst(controller)
      bw_observed = get_my_bandwidth_observed(controller)
      bw_measured = get_my_bandwidth_measured(controller)
      label_in_bytes = CONFIG["features.graph.bw.transferInBytes"]

      if bw_rate and bw_burst:
        bw_rate_label = str_tools.get_size_label(bw_rate, 1, False, label_in_bytes)
        bw_burst_label = str_tools.get_size_label(bw_burst, 1, False, label_in_bytes)

        # if both are using rounded values then strip off the ".0" decimal

        if ".0" in bw_rate_label and ".0" in bw_burst_label:
          bw_rate_label = bw_rate_label.replace(".0", "")
          bw_burst_label = bw_burst_label.replace(".0", "")

        stats.append("limit: %s/s" % bw_rate_label)
        stats.append("burst: %s/s" % bw_burst_label)

      # Provide the observed bandwidth either if the measured bandwidth isn't
      # available or if the measured bandwidth is the observed (this happens
      # if there isn't yet enough bandwidth measurements).

      if bw_observed and (not bw_measured or bw_measured == bw_observed):
        stats.append("observed: %s/s" % str_tools.get_size_label(bw_observed, 1, False, label_in_bytes))
      elif bw_measured:
        stats.append("measured: %s/s" % str_tools.get_size_label(bw_measured, 1, False, label_in_bytes))

      self._title_stats = stats

  def _get_avg_label(self, is_primary):
    total = self.primary_total if is_primary else self.secondary_total
    total += self.prepopulate_primary_total if is_primary else self.prepopulate_secondary_total

    return "avg: %s/sec" % str_tools.get_size_label((total / max(1, self.tick + self.prepopulate_ticks)) * 1024, 1, False, CONFIG["features.graph.bw.transferInBytes"])

  def _get_total_label(self, is_primary):
    total = self.primary_total if is_primary else self.secondary_total
    total += self.initial_primary_total if is_primary else self.initial_secondary_total
    return "total: %s" % str_tools.get_size_label(total * 1024, 1)

  def _update_accounting_info(self):
    """
    Updates mapping used for accounting info. This includes the following keys:
    status, reset_time, read, written, read_limit, writtenLimit

    Any failed lookups result in a mapping to an empty string.
    """

    controller = tor_controller()
    queried = dict([(arg, "") for arg in ACCOUNTING_ARGS])
    queried["status"] = controller.get_info("accounting/hibernating", None)

    # provides a nicely formatted reset time

    end_interval = controller.get_info("accounting/interval-end", None)

    if end_interval:
      # converts from gmt to local with respect to DST

      if time.localtime()[8]:
        tz_offset = time.altzone
      else:
        tz_offset = time.timezone

      sec = time.mktime(time.strptime(end_interval, "%Y-%m-%d %H:%M:%S")) - time.time() - tz_offset

      if CONFIG["features.graph.bw.accounting.isTimeLong"]:
        queried["reset_time"] = ", ".join(str_tools.get_time_labels(sec, True))
      else:
        days = sec / 86400
        sec %= 86400
        hours = sec / 3600
        sec %= 3600
        minutes = sec / 60
        sec %= 60
        queried["reset_time"] = "%i:%02i:%02i:%02i" % (days, hours, minutes, sec)

    # number of bytes used and in total for the accounting period

    used = controller.get_info("accounting/bytes", None)
    left = controller.get_info("accounting/bytes-left", None)

    if used and left:
      used_comp, left_comp = used.split(" "), left.split(" ")
      read, written = int(used_comp[0]), int(used_comp[1])
      read_left, written_left = int(left_comp[0]), int(left_comp[1])

      queried["read"] = str_tools.get_size_label(read)
      queried["written"] = str_tools.get_size_label(written)
      queried["read_limit"] = str_tools.get_size_label(read + read_left)
      queried["writtenLimit"] = str_tools.get_size_label(written + written_left)

    self.accounting_info = queried
    self.accounting_last_updated = time.time()


def get_my_bandwidth_rate(controller):
  """
  Provides the effective relaying bandwidth rate of this relay. Currently
  this doesn't account for SETCONF events.
  """

  # effective relayed bandwidth is the minimum of BandwidthRate,
  # MaxAdvertisedBandwidth, and RelayBandwidthRate (if set)

  effective_rate = int(controller.get_conf("BandwidthRate", None))

  relay_rate = controller.get_conf("RelayBandwidthRate", None)

  if relay_rate and relay_rate != "0":
    effective_rate = min(effective_rate, int(relay_rate))

  max_advertised = controller.get_conf("MaxAdvertisedBandwidth", None)

  if max_advertised:
    effective_rate = min(effective_rate, int(max_advertised))

  if effective_rate is not None:
    return effective_rate
  else:
    return None


def get_my_bandwidth_burst(controller):
  """
  Provides the effective bandwidth burst rate of this relay. Currently this
  doesn't account for SETCONF events.
  """

  # effective burst (same for BandwidthBurst and RelayBandwidthBurst)
  effective_burst = int(controller.get_conf("BandwidthBurst", None))

  relay_burst = controller.get_conf("RelayBandwidthBurst", None)

  if relay_burst and relay_burst != "0":
    effective_burst = min(effective_burst, int(relay_burst))

  if effective_burst is not None:
    return effective_burst
  else:
    return None


def get_my_bandwidth_observed(controller):
  """
  Provides the relay's current observed bandwidth (the throughput determined
  from historical measurements on the client side). This is used in the
  heuristic used for path selection if the measured bandwidth is undefined.
  This is fetched from the descriptors and hence will get stale if
  descriptors aren't periodically updated.
  """

  my_fingerprint = controller.get_info("fingerprint", None)

  if my_fingerprint:
    my_descriptor = controller.get_server_descriptor(my_fingerprint, None)

    if my_descriptor:
      return my_descriptor.observed_bandwidth

  return None


def get_my_bandwidth_measured(controller):
  """
  Provides the relay's current measured bandwidth (the throughput as noted by
  the directory authorities and used by clients for relay selection). This is
  undefined if not in the consensus or with older versions of Tor. Depending
  on the circumstances this can be from a variety of things (observed,
  measured, weighted measured, etc) as described by:
  https://trac.torproject.org/projects/tor/ticket/1566
  """

  # TODO: Tor is documented as providing v2 router status entries but
  # actually looks to be v3. This needs to be sorted out between stem
  # and tor.

  my_fingerprint = controller.get_info("fingerprint", None)

  if my_fingerprint:
    my_status_entry = controller.get_network_status(my_fingerprint, None)

    if my_status_entry and hasattr(my_status_entry, 'bandwidth'):
      return my_status_entry.bandwidth

  return None
