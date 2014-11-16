"""
Graphs of tor related statistics. For example...

Downloaded (0.0 B/sec):           Uploaded (0.0 B/sec):
  34                                30
                            *                                 *
                    **  *   *                          *      **
      *   *  *      ** **   **          ***  **       ** **   **
     *********      ******  ******     *********      ******  ******
   0 ************ ****************   0 ************ ****************
         25s  50   1m   1.6  2.0           25s  50   1m   1.6  2.0
"""

import copy
import curses
import time

import arm.controller
import arm.popups
import arm.util.tracker

from arm.util import bandwidth_from_state, msg, panel, tor_controller

from stem.control import EventType, Listener
from stem.util import conf, enum, log, str_tools, system

GraphStat = enum.Enum(('BANDWIDTH', 'bandwidth'), ('CONNECTIONS', 'connections'), ('SYSTEM_RESOURCES', 'resources'))
Interval = enum.Enum(('EACH_SECOND', 'each second'), ('FIVE_SECONDS', '5 seconds'), ('THIRTY_SECONDS', '30 seconds'), ('MINUTELY', 'minutely'), ('FIFTEEN_MINUTE', '15 minute'), ('THIRTY_MINUTE', '30 minute'), ('HOURLY', 'hourly'), ('DAILY', 'daily'))
Bounds = enum.Enum(('GLOBAL_MAX', 'global_max'), ('LOCAL_MAX', 'local_max'), ('TIGHT', 'tight'))

INTERVAL_SECONDS = {
  Interval.EACH_SECOND: 1,
  Interval.FIVE_SECONDS: 5,
  Interval.THIRTY_SECONDS: 30,
  Interval.MINUTELY: 60,
  Interval.FIFTEEN_MINUTE: 900,
  Interval.THIRTY_MINUTE: 1800,
  Interval.HOURLY: 3600,
  Interval.DAILY: 86400,
}

PRIMARY_COLOR, SECONDARY_COLOR = 'green', 'cyan'

ACCOUNTING_RATE = 5
DEFAULT_CONTENT_HEIGHT = 4  # space needed for labeling above and below the graph
WIDE_LABELING_GRAPH_COL = 50  # minimum graph columns to use wide spacing for x-axis labels
COLLAPSE_WIDTH = 135  # width at which to move optional stats from the title to x-axis label


def conf_handler(key, value):
  if key == 'features.graph.height':
    return max(1, value)
  elif key == 'features.graph.max_width':
    return max(1, value)
  elif key == 'features.graph.type':
    if value != 'none' and value not in GraphStat:
      log.warn("'%s' isn't a valid graph type, options are: none, %s" % (CONFIG['features.graph.type'], ', '.join(GraphStat)))
      return CONFIG['features.graph.type']  # keep the default
  elif key == 'features.graph.interval':
    if value not in Interval:
      log.warn("'%s' isn't a valid graphing interval, options are: %s" % (value, ', '.join(Interval)))
      return CONFIG['features.graph.interval']  # keep the default
  elif key == 'features.graph.bound':
    if value not in Bounds:
      log.warn("'%s' isn't a valid graph bounds, options are: %s" % (value, ', '.join(Bounds)))
      return CONFIG['features.graph.bound']  # keep the default


CONFIG = conf.config_dict('arm', {
  'attr.hibernate_color': {},
  'attr.graph.title': {},
  'attr.graph.header.primary': {},
  'attr.graph.header.secondary': {},
  'features.graph.height': 7,
  'features.graph.type': GraphStat.BANDWIDTH,
  'features.graph.interval': Interval.EACH_SECOND,
  'features.graph.bound': Bounds.LOCAL_MAX,
  'features.graph.max_width': 150,
  'features.graph.showIntermediateBounds': True,
  'features.panels.show.connection': True,
  'features.graph.bw.prepopulate': True,
  'features.graph.bw.transferInBytes': False,
  'features.graph.bw.accounting.show': True,
  'tor.chroot': '',
}, conf_handler)


class Stat(object):
  """
  Graphable statistical information.

  :var int latest_value: last value we recorded
  :var int total: sum of all values we've recorded
  :var int tick: number of events we've processed
  :var float start_time: unix timestamp for when we started
  :var dict values: mapping of intervals to an array of samplings from newest to oldest
  :var dict max_value: mapping of intervals to the maximum value it has had
  """

  def __init__(self, clone = None):
    if clone:
      self.latest_value = clone.latest_value
      self.total = clone.total
      self.tick = clone.tick
      self.start_time = clone.start_time
      self.values = copy.deepcopy(clone.values)
      self.max_value = dict(clone.max_value)
      self._in_process_value = dict(clone._in_process_value)
    else:
      self.latest_value = 0
      self.total = 0
      self.tick = 0
      self.start_time = time.time()
      self.values = dict([(i, CONFIG['features.graph.max_width'] * [0]) for i in Interval])
      self.max_value = dict([(i, 0) for i in Interval])
      self._in_process_value = dict([(i, 0) for i in Interval])

  def average(self, by_time = False):
    return self.total / (time.time() - self.start_time) if by_time else self.total / max(1, self.tick)

  def update(self, new_value):
    self.latest_value = new_value
    self.total += new_value
    self.tick += 1

    for interval in Interval:
      interval_seconds = INTERVAL_SECONDS[interval]
      self._in_process_value[interval] += new_value

      if self.tick % interval_seconds == 0:
        new_entry = self._in_process_value[interval] / interval_seconds
        self.values[interval] = [new_entry] + self.values[interval][:-1]
        self.max_value[interval] = max(self.max_value[interval], new_entry)
        self._in_process_value[interval] = 0


class GraphCategory(object):
  """
  Category for the graph. This maintains two subgraphs, updating them each
  second with updated stats.

  :var Stat primary: first subgraph
  :var Stat secondary: second subgraph
  :var list title_stats: additional information to include in the graph title
  :var list primary_header_stats: additional information for the primary header
  :var list secondary_header_stats: additional information for the secondary header
  """

  def __init__(self, clone = None):
    if clone:
      self.primary = Stat(clone.primary)
      self.secondary = Stat(clone.secondary)
      self.title_stats = list(clone.title_stats)
      self.primary_header_stats = list(clone.primary_header_stats)
      self.secondary_header_stats = list(clone.secondary_header_stats)
    else:
      self.primary = Stat()
      self.secondary = Stat()
      self.title_stats = []
      self.primary_header_stats = []
      self.secondary_header_stats = []

  def y_axis_label(self, value, is_primary):
    """
    Provides the label we should display on our y-axis.

    :param int value: value being shown on the y-axis
    :param bool is_primary: True if this is the primary attribute, False if
      it's the secondary

    :returns: **str** with our y-axis label
    """

    return str(value)

  def bandwidth_event(self, event):
    """
    Called when it's time to process another event. All graphs use tor BW
    events to keep in sync with each other (this happens once per second).
    """

    pass


class BandwidthStats(GraphCategory):
  """
  Tracks tor's bandwidth usage.
  """

  def __init__(self, clone = None):
    GraphCategory.__init__(self, clone)

    if not clone:
      # We both show our 'total' attributes and use it to determine our average.
      #
      # If we can get *both* our start time and the totals from tor (via 'GETINFO
      # traffic/*') then that's ideal, but if not then just track the total for
      # the time arm is run.

      controller = tor_controller()

      read_total = controller.get_info('traffic/read', None)
      write_total = controller.get_info('traffic/written', None)
      start_time = system.start_time(controller.get_pid(None))

      if read_total and write_total and start_time:
        self.primary.total = int(read_total)
        self.secondary.total = int(write_total)
        self.primary.start_time = self.secondary.start_time = start_time

  def y_axis_label(self, value, is_primary):
    return self._size_label(value, 0)

  def bandwidth_event(self, event):
    self.primary.update(event.read)
    self.secondary.update(event.written)

    self.primary_header_stats = [
      '%-14s' % ('%s/sec' % self._size_label(self.primary.latest_value)),
      '- avg: %s/sec' % self._size_label(self.primary.average(by_time = True)),
      ', total: %s' % self._size_label(self.primary.total),
    ]

    self.secondary_header_stats = [
      '%-14s' % ('%s/sec' % self._size_label(self.secondary.latest_value)),
      '- avg: %s/sec' % self._size_label(self.secondary.average(by_time = True)),
      ', total: %s' % self._size_label(self.secondary.total),
    ]

    controller = tor_controller()

    stats = []
    bw_rate = controller.get_effective_rate(None)
    bw_burst = controller.get_effective_rate(None, burst = True)

    if bw_rate and bw_burst:
      bw_rate_label = self._size_label(bw_rate)
      bw_burst_label = self._size_label(bw_burst)

      # if both are using rounded values then strip off the '.0' decimal

      if '.0' in bw_rate_label and '.0' in bw_burst_label:
        bw_rate_label = bw_rate_label.split('.', 1)[0]
        bw_burst_label = bw_burst_label.split('.', 1)[0]

      stats.append('limit: %s/s' % bw_rate_label)
      stats.append('burst: %s/s' % bw_burst_label)

    my_router_status_entry = controller.get_network_status(default = None)
    measured_bw = getattr(my_router_status_entry, 'bandwidth', None)

    if measured_bw:
      stats.append('measured: %s/s' % self._size_label(measured_bw))
    else:
      my_server_descriptor = controller.get_server_descriptor(default = None)
      observed_bw = getattr(my_server_descriptor, 'observed_bandwidth', None)

      if observed_bw:
        stats.append('observed: %s/s' % self._size_label(observed_bw))

    self.title_stats = stats

  def prepopulate_from_state(self):
    """
    Attempts to use tor's state file to prepopulate values for the 15 minute
    interval via the BWHistoryReadValues/BWHistoryWriteValues values.

    :returns: **float** for the number of seconds of data missing

    :raises: **ValueError** if unable to get the bandwidth information from our
      state file
    """

    def update_values(stat, entries, latest_time):
      # fill missing entries with the last value

      missing_entries = int((time.time() - latest_time) / 900)
      entries = entries + [entries[-1]] * missing_entries

      # pad if too short and truncate if too long

      entry_count = CONFIG['features.graph.max_width']
      entries = [0] * (entry_count - len(entries)) + entries[-entry_count:]

      stat.values[Interval.FIFTEEN_MINUTE] = entries
      stat.max_value[Interval.FIFTEEN_MINUTE] = max(entries)
      stat.latest_value = entries[-1] * 900

    stats = bandwidth_from_state()

    update_values(self.primary, stats.read_entries, stats.last_read_time)
    update_values(self.secondary, stats.write_entries, stats.last_write_time)

    return time.time() - min(stats.last_read_time, stats.last_write_time)

  def _size_label(self, byte_count, decimal = 1):
    """
    Alias for str_tools.size_label() that accounts for if the user prefers bits
    or bytes.
    """

    return str_tools.size_label(byte_count, decimal, is_bytes = CONFIG['features.graph.bw.transferInBytes'])


class ConnectionStats(GraphCategory):
  """
  Tracks number of inbound and outbound connections.
  """

  def bandwidth_event(self, event):
    inbound_count, outbound_count = 0, 0

    controller = tor_controller()
    or_ports = controller.get_ports(Listener.OR, [])
    dir_ports = controller.get_ports(Listener.DIR, [])
    control_ports = controller.get_ports(Listener.CONTROL, [])

    for entry in arm.util.tracker.get_connection_tracker().get_value():
      if entry.local_port in or_ports or entry.local_port in dir_ports:
        inbound_count += 1
      elif entry.local_port in control_ports:
        pass  # control connection
      else:
        outbound_count += 1

    self.primary.update(inbound_count)
    self.secondary.update(outbound_count)

    self.primary_header_stats = [str(self.primary.latest_value), ', avg: %s' % self.primary.average()]
    self.secondary_header_stats = [str(self.secondary.latest_value), ', avg: %s' % self.secondary.average()]


class ResourceStats(GraphCategory):
  """
  Tracks cpu and memory usage of the tor process.
  """

  def y_axis_label(self, value, is_primary):
    return '%i%%' % value if is_primary else str_tools.size_label(value)

  def bandwidth_event(self, event):
    resources = arm.util.tracker.get_resource_tracker().get_value()
    self.primary.update(resources.cpu_sample * 100)  # decimal percentage to whole numbers
    self.secondary.update(resources.memory_bytes)

    self.primary_header_stats = ['%0.1f%%' % self.primary.latest_value, ', avg: %0.1f%%' % self.primary.average()]
    self.secondary_header_stats = [str_tools.size_label(self.secondary.latest_value, 1), ', avg: %s' % str_tools.size_label(self.secondary.average(), 1)]


class GraphPanel(panel.Panel):
  """
  Panel displaying a graph, drawing statistics from custom GraphCategory
  implementations.
  """

  def __init__(self, stdscr):
    panel.Panel.__init__(self, stdscr, 'graph', 0)

    self._current_display = None if CONFIG['features.graph.type'] == 'none' else CONFIG['features.graph.type']
    self._update_interval = CONFIG['features.graph.interval']
    self._bounds = CONFIG['features.graph.bound']

    self._graph_height = max(1, CONFIG['features.graph.height'])
    self._accounting_stats = None

    self._stats = {
      GraphStat.BANDWIDTH: BandwidthStats(),
      GraphStat.SYSTEM_RESOURCES: ResourceStats(),
    }

    if CONFIG['features.panels.show.connection']:
      self._stats[GraphStat.CONNECTIONS] = ConnectionStats()

    self.set_pause_attr('_stats')
    self.set_pause_attr('_accounting_stats')

    # prepopulates bandwidth values from state file

    controller = tor_controller()

    if controller.is_alive() and CONFIG['features.graph.bw.prepopulate']:
      try:
        missing_seconds = self._stats[GraphStat.BANDWIDTH].prepopulate_from_state()

        if missing_seconds:
          log.notice(msg('panel.graphing.prepopulation_successful', duration = str_tools.time_label(missing_seconds, 0, True)))
        else:
          log.notice(msg('panel.graphing.prepopulation_all_successful'))

        self._update_interval = Interval.FIFTEEN_MINUTE
      except ValueError as exc:
        log.info(msg('panel.graphing.prepopulation_failure', error = exc))

    controller.add_event_listener(self.bandwidth_event, EventType.BW)
    controller.add_status_listener(self.reset_listener)

  def bandwidth_event(self, event):
    for stat in self._stats.values():
      stat.bandwidth_event(event)

    if not CONFIG['features.graph.bw.accounting.show']:
      self._accounting_stats = None
    elif not self._accounting_stats or time.time() - self._accounting_stats.retrieved >= ACCOUNTING_RATE:
      old_accounting_stats = self._accounting_stats
      self._accounting_stats = tor_controller().get_accounting_stats(None)

      if bool(old_accounting_stats) != bool(self._accounting_stats):
        # we either added or removed accounting info, redraw the whole screen since this changes our height

        arm.controller.get_controller().redraw()

    update_rate = INTERVAL_SECONDS[self._update_interval]
    param = self.get_attr('_stats')[self._current_display]

    if param.primary.tick % update_rate == 0:
      self.redraw(True)

  def reset_listener(self, controller, event_type, _):
    self.redraw(True)

  def get_update_interval(self):
    """
    Provides the rate that we update the graph at.
    """

    return self._update_interval

  def set_update_interval(self, update_interval):
    """
    Sets the rate that we update the graph at.

    Arguments:
      update_interval - update time enum
    """

    self._update_interval = update_interval

  def get_bounds_type(self):
    """
    Provides the type of graph bounds used.
    """

    return self._bounds

  def set_bounds_type(self, bounds_type):
    """
    Sets the type of graph boundaries we use.

    Arguments:
      bounds_type - graph bounds enum
    """

    self._bounds = bounds_type

  def get_height(self):
    """
    Provides the height requested by the currently displayed GraphCategory
    (zero if hidden).
    """

    if self._current_display:
      height = DEFAULT_CONTENT_HEIGHT + self._graph_height
    else:
      height = 0

    if self._current_display == GraphStat.BANDWIDTH and self._accounting_stats:
      height += 3

    return height

  def set_graph_height(self, new_graph_height):
    """
    Sets the preferred height used for the graph.

    Arguments:
      new_graph_height - new height for the graph
    """

    self._graph_height = max(1, new_graph_height)

  def resize_graph(self):
    """
    Prompts for user input to resize the graph panel. Options include...
      down arrow - grow graph
      up arrow - shrink graph
      enter / space - set size
    """

    control = arm.controller.get_controller()

    with panel.CURSES_LOCK:
      try:
        while True:
          msg = 'press the down/up to resize the graph, and enter when done'
          control.set_msg(msg, curses.A_BOLD, True)
          curses.cbreak()
          key = control.key_input()

          if key.match('down'):
            # don't grow the graph if it's already consuming the whole display
            # (plus an extra line for the graph/log gap)

            max_height = self.parent.getmaxyx()[0] - self.top
            current_height = self.get_height()

            if current_height < max_height + 1:
              self.set_graph_height(self._graph_height + 1)
          elif key.match('up'):
            self.set_graph_height(self._graph_height - 1)
          elif key.is_selection():
            break

          control.redraw()
      finally:
        control.set_msg()

  def handle_key(self, key):
    if key.match('r'):
      self.resize_graph()
    elif key.match('b'):
      # uses the next boundary type
      self._bounds = Bounds.next(self._bounds)
      self.redraw(True)
    elif key.match('s'):
      # provides a menu to pick the graphed stats

      available_stats = self._stats.keys()
      available_stats.sort()

      # uses sorted, camel cased labels for the options

      options = ['None']

      for label in available_stats:
        words = label.split()
        options.append(' '.join(word[0].upper() + word[1:] for word in words))

      if self._current_display:
        initial_selection = available_stats.index(self._current_display) + 1
      else:
        initial_selection = 0

      selection = arm.popups.show_menu('Graphed Stats:', options, initial_selection)

      # applies new setting

      if selection == 0:
        self.set_stats(None)
      elif selection != -1:
        self.set_stats(available_stats[selection - 1])
    elif key.match('i'):
      # provides menu to pick graph panel update interval

      selection = arm.popups.show_menu('Update Interval:', list(Interval), list(Interval).index(self._update_interval))

      if selection != -1:
        self._update_interval = list(Interval)[selection]
    else:
      return False

    return True

  def get_help(self):
    return [
      ('r', 'resize graph', None),
      ('s', 'graphed stats', self._current_display if self._current_display else 'none'),
      ('b', 'graph bounds', self._bounds.lower()),
      ('i', 'graph update interval', self._update_interval),
    ]

  def draw(self, width, height):
    if not self._current_display:
      return

    param = self.get_attr('_stats')[self._current_display]
    graph_column = min((width - 10) / 2, CONFIG['features.graph.max_width'])

    if self.is_title_visible():
      title = CONFIG['attr.graph.title'].get(self._current_display, '')
      title_stats = str_tools.join(param.title_stats, ', ', width - len(title) - 4)
      title = '%s (%s):' % (title, title_stats) if title_stats else '%s:' % title
      self.addstr(0, 0, title, curses.A_STANDOUT)

    # top labels

    primary_header = CONFIG['attr.graph.header.primary'].get(self._current_display, '')
    primary_header_stats = str_tools.join(param.primary_header_stats, '', (width / 2) - len(primary_header) - 4)
    left = '%s (%s):' % (primary_header, primary_header_stats) if primary_header_stats else '%s:' % primary_header
    self.addstr(1, 0, left, curses.A_BOLD, PRIMARY_COLOR)

    secondary_header = CONFIG['attr.graph.header.secondary'].get(self._current_display, '')
    secondary_header_stats = str_tools.join(param.secondary_header_stats, '', (width / 2) - len(secondary_header) - 4)
    right = '%s (%s):' % (secondary_header, secondary_header_stats) if secondary_header_stats else '%s:' % secondary_header
    self.addstr(1, graph_column + 5, right, curses.A_BOLD, SECONDARY_COLOR)

    # determines max/min value on the graph

    if self._bounds == Bounds.GLOBAL_MAX:
      primary_max_bound = param.primary.max_value[self._update_interval]
      secondary_max_bound = param.secondary.max_value[self._update_interval]
    else:
      # both Bounds.LOCAL_MAX and Bounds.TIGHT use local maxima
      if graph_column < 2:
        # nothing being displayed
        primary_max_bound, secondary_max_bound = 0, 0
      else:
        primary_max_bound = max(param.primary.values[self._update_interval][:graph_column])
        secondary_max_bound = max(param.secondary.values[self._update_interval][:graph_column])

    primary_min_bound = secondary_min_bound = 0

    if self._bounds == Bounds.TIGHT:
      primary_min_bound = min(param.primary.values[self._update_interval][:graph_column])
      secondary_min_bound = min(param.secondary.values[self._update_interval][:graph_column])

      # if the max = min (ie, all values are the same) then use zero lower
      # bound so a graph is still displayed

      if primary_min_bound == primary_max_bound:
        primary_min_bound = 0

      if secondary_min_bound == secondary_max_bound:
        secondary_min_bound = 0

    # displays upper and lower bounds

    self.addstr(2, 0, param.y_axis_label(primary_max_bound, True), PRIMARY_COLOR)
    self.addstr(self._graph_height + 1, 0, param.y_axis_label(primary_min_bound, True), PRIMARY_COLOR)

    self.addstr(2, graph_column + 5, param.y_axis_label(secondary_max_bound, False), SECONDARY_COLOR)
    self.addstr(self._graph_height + 1, graph_column + 5, param.y_axis_label(secondary_min_bound, False), SECONDARY_COLOR)

    # displays intermediate bounds on every other row

    if CONFIG['features.graph.showIntermediateBounds']:
      ticks = (self._graph_height - 3) / 2

      for i in range(ticks):
        row = self._graph_height - (2 * i) - 3

        if self._graph_height % 2 == 0 and i >= (ticks / 2):
          row -= 1

        if primary_min_bound != primary_max_bound:
          primary_val = (primary_max_bound - primary_min_bound) * (self._graph_height - row - 1) / (self._graph_height - 1)

          if primary_val not in (primary_min_bound, primary_max_bound):
            self.addstr(row + 2, 0, param.y_axis_label(primary_val, True), PRIMARY_COLOR)

        if secondary_min_bound != secondary_max_bound:
          secondary_val = (secondary_max_bound - secondary_min_bound) * (self._graph_height - row - 1) / (self._graph_height - 1)

          if secondary_val not in (secondary_min_bound, secondary_max_bound):
            self.addstr(row + 2, graph_column + 5, param.y_axis_label(secondary_val, False), SECONDARY_COLOR)

    # creates bar graph (both primary and secondary)

    for col in range(graph_column):
      column_count = int(param.primary.values[self._update_interval][col]) - primary_min_bound
      column_height = int(min(self._graph_height, self._graph_height * column_count / (max(1, primary_max_bound) - primary_min_bound)))

      for row in range(column_height):
        self.addstr(self._graph_height + 1 - row, col + 5, ' ', curses.A_STANDOUT, PRIMARY_COLOR)

      column_count = int(param.secondary.values[self._update_interval][col]) - secondary_min_bound
      column_height = int(min(self._graph_height, self._graph_height * column_count / (max(1, secondary_max_bound) - secondary_min_bound)))

      for row in range(column_height):
        self.addstr(self._graph_height + 1 - row, col + graph_column + 10, ' ', curses.A_STANDOUT, SECONDARY_COLOR)

    # bottom labeling of x-axis

    interval_sec = INTERVAL_SECONDS[self._update_interval]

    interval_spacing = 10 if graph_column >= WIDE_LABELING_GRAPH_COL else 5
    units_label, decimal_precision = None, 0

    for i in range((graph_column - 4) / interval_spacing):
      loc = (i + 1) * interval_spacing
      time_label = str_tools.time_label(loc * interval_sec, decimal_precision)

      if not units_label:
        units_label = time_label[-1]
      elif units_label != time_label[-1]:
        # upped scale so also up precision of future measurements
        units_label = time_label[-1]
        decimal_precision += 1
      else:
        # if constrained on space then strips labeling since already provided
        time_label = time_label[:-1]

      self.addstr(self._graph_height + 2, 4 + loc, time_label, PRIMARY_COLOR)
      self.addstr(self._graph_height + 2, graph_column + 10 + loc, time_label, SECONDARY_COLOR)

    # if display is narrow, overwrites x-axis labels with avg / total stats

    labeling_line = DEFAULT_CONTENT_HEIGHT + self._graph_height - 2

    if self._current_display == GraphStat.BANDWIDTH and width <= COLLAPSE_WIDTH:
      # clears line

      self.addstr(labeling_line, 0, ' ' * width)
      graph_column = min((width - 10) / 2, CONFIG['features.graph.max_width'])

      runtime = time.time() - param.start_time
      primary_footer = 'total: %s, avg: %s/sec' % (param._size_label(param.primary.total), param._size_label(param.primary.total / runtime))
      secondary_footer = 'total: %s, avg: %s/sec' % (param._size_label(param.secondary.total), param._size_label(param.secondary.total / runtime))

      self.addstr(labeling_line, 1, primary_footer, PRIMARY_COLOR)
      self.addstr(labeling_line, graph_column + 6, secondary_footer, SECONDARY_COLOR)

    # provides accounting stats if enabled

    accounting_stats = self.get_attr('_accounting_stats')

    if self._current_display == GraphStat.BANDWIDTH and accounting_stats:
      if tor_controller().is_alive():
        hibernate_color = CONFIG['attr.hibernate_color'].get(accounting_stats.status, 'red')

        x, y = 0, labeling_line + 2
        x = self.addstr(y, x, 'Accounting (', curses.A_BOLD)
        x = self.addstr(y, x, accounting_stats.status, curses.A_BOLD, hibernate_color)
        x = self.addstr(y, x, ')', curses.A_BOLD)

        self.addstr(y, 35, 'Time to reset: %s' % str_tools.short_time_label(accounting_stats.time_until_reset))

        self.addstr(y + 1, 2, '%s / %s' % (accounting_stats.read_bytes, accounting_stats.read_limit), PRIMARY_COLOR)
        self.addstr(y + 1, 37, '%s / %s' % (accounting_stats.written_bytes, accounting_stats.write_limit), SECONDARY_COLOR)
      else:
        self.addstr(labeling_line + 2, 0, 'Accounting:', curses.A_BOLD)
        self.addstr(labeling_line + 2, 12, 'Connection Closed...')

  def get_stats(self):
    """
    Provides the currently selected stats label.
    """

    return self._current_display

  def set_stats(self, label):
    """
    Sets the currently displayed stats instance, hiding panel if None.
    """

    if label != self._current_display:
      if not label:
        self._current_display = None
      elif label in self._stats.keys():
        self._current_display = label
      else:
        raise ValueError('Unrecognized stats label: %s' % label)

  def get_all_stats(self):
    return self._stats

  def copy_attr(self, attr):
    if attr == '_stats':
      return dict([(key, type(self._stats[key])(self._stats[key])) for key in self._stats])
    else:
      return panel.Panel.copy_attr(self, attr)
