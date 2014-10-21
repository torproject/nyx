"""
Flexible panel for presenting bar graphs for a variety of stats. This panel is
just concerned with the rendering of information, which is actually collected
and stored by implementations of the GraphStats interface. Panels are made up
of a title, followed by headers and graphs for two sets of stats. For
instance...

Bandwidth (cap: 5 MB, burst: 10 MB):
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

import arm.popups
import arm.controller
import arm.util.tracker

import stem.control

from arm.util import bandwidth_from_state, msg, panel, tor_controller

from stem.control import Listener, State
from stem.util import conf, enum, log, str_tools, system

GraphStat = enum.Enum('BANDWIDTH', 'CONNECTIONS', 'SYSTEM_RESOURCES')

# maps 'features.graph.type' config values to the initial types

GRAPH_INIT_STATS = {1: GraphStat.BANDWIDTH, 2: GraphStat.CONNECTIONS, 3: GraphStat.SYSTEM_RESOURCES}

DEFAULT_CONTENT_HEIGHT = 4  # space needed for labeling above and below the graph
PRIMARY_COLOR, SECONDARY_COLOR = 'green', 'cyan'
MIN_GRAPH_HEIGHT = 1

# enums for graph bounds:
#   Bounds.GLOBAL_MAX - global maximum (highest value ever seen)
#   Bounds.LOCAL_MAX - local maximum (highest value currently on the graph)
#   Bounds.TIGHT - local maximum and minimum

Bounds = enum.Enum('GLOBAL_MAX', 'LOCAL_MAX', 'TIGHT')

WIDE_LABELING_GRAPH_COL = 50  # minimum graph columns to use wide spacing for x-axis labels

ACCOUNTING_RATE = 5


def conf_handler(key, value):
  if key == 'features.graph.height':
    return max(MIN_GRAPH_HEIGHT, value)
  elif key == 'features.graph.max_width':
    return max(1, value)
  elif key == 'features.graph.bound':
    return max(0, min(2, value))


# used for setting defaults when initializing GraphStats and GraphPanel instances

CONFIG = conf.config_dict('arm', {
  'attr.hibernate_color': {},
  'attr.graph.intervals': {},
  'features.graph.height': 7,
  'features.graph.interval': 'each second',
  'features.graph.bound': 1,
  'features.graph.max_width': 150,
  'features.graph.showIntermediateBounds': True,
  'features.graph.type': 1,
  'features.panels.show.connection': True,
  'features.graph.bw.prepopulate': True,
  'features.graph.bw.transferInBytes': False,
  'features.graph.bw.accounting.show': True,
  'tor.chroot': '',
}, conf_handler)

# width at which panel abandons placing optional stats (avg and total) with
# header in favor of replacing the x-axis label

COLLAPSE_WIDTH = 135


class Stat(object):
  """
  Graphable statistical information.

  :var int latest_value: last value we recorded
  :var int total: sum of all values we've recorded
  :var dict values: mapping of intervals to an array of samplings from newest to oldest
  :var dict max_value: mapping of intervals to the maximum value it has had
  """

  def __init__(self, clone = None):
    if clone:
      self.latest_value = clone.latest_value
      self.total = clone.total
      self.tick = clone.tick
      self.values = copy.deepcopy(clone.values)
      self.max_value = dict(clone.max_value)
      self._in_process_value = dict(clone._in_process_value)
    else:
      self.latest_value = 0
      self.total = 0
      self.tick = 0
      self.values = dict([(i, CONFIG['features.graph.max_width'] * [0]) for i in CONFIG['attr.graph.intervals']])
      self.max_value = dict([(i, 0) for i in CONFIG['attr.graph.intervals']])
      self._in_process_value = dict([(i, 0) for i in CONFIG['attr.graph.intervals']])

  def update(self, new_value):
    self.latest_value = new_value
    self.total += new_value
    self.tick += 1

    for interval in CONFIG['attr.graph.intervals']:
      interval_seconds = int(CONFIG['attr.graph.intervals'][interval])
      self._in_process_value[interval] += new_value

      if self.tick % interval_seconds == 0:
        new_entry = self._in_process_value[interval] / interval_seconds
        self.values[interval] = [new_entry] + self.values[interval][:-1]
        self.max_value[interval] = max(self.max_value[interval], new_entry)
        self._in_process_value[interval] = 0


class GraphStats(object):
  """
  Module that's expected to update dynamically and provide attributes to be
  graphed. Up to two graphs (a 'primary' and 'secondary') can be displayed at a
  time and timescale parameters use the labels defined in CONFIG['attr.graph.intervals'].
  """

  def __init__(self, clone = None):
    """
    Initializes parameters needed to present a graph.
    """

    if clone:
      self.title = clone.title
      self.title_stats = list(clone.title_stats)

      self.primary = Stat(clone.primary)
      self.secondary = Stat(clone.secondary)
    else:
      self.title = ''
      self.title_stats = []

      self.primary = Stat()
      self.secondary = Stat()

      tor_controller().add_event_listener(self.bandwidth_event, stem.control.EventType.BW)

  def primary_header(self, width):
    return ''

  def secondary_header(self, width):
    return ''

  def bandwidth_event(self, event):
    """
    Called when it's time to process another event. All graphs use tor BW
    events to keep in sync with each other (this happens once a second).
    """

    pass


class BandwidthStats(GraphStats):
  """
  Uses tor BW events to generate bandwidth usage graph.
  """

  def __init__(self, clone = None):
    GraphStats.__init__(self, clone)

    if clone:
      self.start_time = clone.start_time
    else:
      self.title = 'Bandwidth'

      # listens for tor reload (sighup) events which can reset the bandwidth
      # rate/burst

      controller = tor_controller()

      self.reset_listener(controller, State.INIT, None)  # initializes values

      controller.add_status_listener(self.reset_listener)
      self.new_desc_event(None)  # updates title params

      # We both show our 'total' attributes and use it to determine our average.
      #
      # If we can get *both* our start time and the totals from tor (via 'GETINFO
      # traffic/*') then that's ideal, but if not then just track the total for
      # the time arm is run.

      read_total = controller.get_info('traffic/read', None)
      write_total = controller.get_info('traffic/written', None)
      start_time = system.start_time(controller.get_pid(None))

      if read_total and write_total and start_time:
        self.primary.total = int(read_total) / 1024  # Bytes -> KB
        self.secondary.total = int(write_total) / 1024  # Bytes -> KB
        self.start_time = start_time
      else:
        self.start_time = time.time()

  def reset_listener(self, controller, event_type, _):
    self.new_desc_event(None)  # updates title params

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

    entry_count = min(len(bw_read_entries), len(bw_write_entries), CONFIG['features.graph.max_width'])
    bw_read_entries = bw_read_entries[len(bw_read_entries) - entry_count:]
    bw_write_entries = bw_write_entries[len(bw_write_entries) - entry_count:]

    # gets index for 15-minute interval

    interval_index = 0

    for interval_rate in CONFIG['attr.graph.intervals'].values():
      if int(interval_rate) == 900:
        break
      else:
        interval_index += 1

    # fills the graphing parameters with state information

    for i in range(entry_count):
      read_value, write_value = bw_read_entries[i], bw_write_entries[i]

      self.primary.latest_value, self.secondary.latest_value = read_value / 900, write_value / 900

      self.primary.values['15 minute'] = [read_value] + self.primary.values['15 minute'][:-1]
      self.secondary.values['15 minute'] = [write_value] + self.secondary.values['15 minute'][:-1]

    self.primary.max_value['15 minute'] = max(self.primary.values)
    self.secondary.max_value['15 minute'] = max(self.secondary.values)

    return time.time() - min(stats.last_read_time, stats.last_write_time)

  def bandwidth_event(self, event):
    # scales units from B to KB for graphing

    self.primary.update(event.read / 1024.0)
    self.secondary.update(event.written / 1024.0)

  def primary_header(self, width):
    stats = ['%-14s' % ('%s/sec' % _size_label(self.primary.latest_value * 1024))]

    # if wide then avg and total are part of the header, otherwise they're on
    # the x-axis

    if width * 2 > COLLAPSE_WIDTH:
      stats.append('- avg: %s/sec' % _size_label(self.primary.total / (time.time() - self.start_time) * 1024))
      stats.append(', total: %s' % _size_label(self.primary.total * 1024))

    stats_label = str_tools.join(stats, '', width - 12)

    if stats_label:
      return 'Download (%s):' % stats_label
    else:
      return 'Download:'

  def secondary_header(self, width):
    stats = ['%-14s' % ('%s/sec' % _size_label(self.secondary.latest_value * 1024))]

    # if wide then avg and total are part of the header, otherwise they're on
    # the x-axis

    if width * 2 > COLLAPSE_WIDTH:
      stats.append('- avg: %s/sec' % _size_label(self.secondary.total / (time.time() - self.start_time) * 1024))
      stats.append(', total: %s' % _size_label(self.secondary.total * 1024))

    stats_label = str_tools.join(stats, '', width - 10)

    if stats_label:
      return 'Upload (%s):' % stats_label
    else:
      return 'Upload:'

  def new_desc_event(self, event):
    controller = tor_controller()

    if not controller.is_alive():
      return  # keep old values

    my_fingerprint = controller.get_info('fingerprint', None)

    if not event or (my_fingerprint and my_fingerprint in [fp for fp, _ in event.relays]):
      stats = []

      bw_rate = controller.get_effective_rate(None)
      bw_burst = controller.get_effective_rate(None, burst = True)

      if bw_rate and bw_burst:
        bw_rate_label = _size_label(bw_rate)
        bw_burst_label = _size_label(bw_burst)

        # if both are using rounded values then strip off the '.0' decimal

        if '.0' in bw_rate_label and '.0' in bw_burst_label:
          bw_rate_label = bw_rate_label.split('.', 1)[0]
          bw_burst_label = bw_burst_label.split('.', 1)[0]

        stats.append('limit: %s/s' % bw_rate_label)
        stats.append('burst: %s/s' % bw_burst_label)

      my_router_status_entry = controller.get_network_status(default = None)
      measured_bw = getattr(my_router_status_entry, 'bandwidth', None)

      if measured_bw:
        stats.append('measured: %s/s' % _size_label(measured_bw))
      else:
        my_server_descriptor = controller.get_server_descriptor(default = None)
        observed_bw = getattr(my_server_descriptor, 'observed_bandwidth', None)

        if observed_bw:
          stats.append('observed: %s/s' % _size_label(observed_bw))

      self.title_stats = stats


class ConnStats(GraphStats):
  """
  Tracks number of connections, counting client and directory connections as
  outbound. Control connections are excluded from counts.
  """

  def __init__(self, clone = None):
    GraphStats.__init__(self, clone)

    if not clone:
      self.title = 'Connection Count'

  def bandwidth_event(self, event):
    """
    Fetches connection stats from cached information.
    """

    inbound_count, outbound_count = 0, 0

    controller = tor_controller()

    or_ports = controller.get_ports(Listener.OR)
    dir_ports = controller.get_ports(Listener.DIR)
    control_ports = controller.get_ports(Listener.CONTROL)

    for entry in arm.util.tracker.get_connection_tracker().get_value():
      local_port = entry.local_port

      if local_port in or_ports or local_port in dir_ports:
        inbound_count += 1
      elif local_port in control_ports:
        pass  # control connection
      else:
        outbound_count += 1

    self.primary.update(inbound_count)
    self.secondary.update(outbound_count)

  def primary_header(self, width):
    avg = self.primary.total / max(1, self.primary.tick)
    return 'Inbound (%s, avg: %s):' % (self.primary.latest_value, avg)

  def secondary_header(self, width):
    avg = self.secondary.total / max(1, self.secondary.tick)
    return 'Outbound (%s, avg: %s):' % (self.secondary.latest_value, avg)


class ResourceStats(GraphStats):
  """
  System resource usage tracker.
  """

  def __init__(self, clone = None):
    GraphStats.__init__(self)

    if clone:
      self._last_counter = clone._last_counter
    else:
      self.title = 'System Resources'
      self._last_counter = None

  def primary_header(self, width):
    avg = self.primary.total / max(1, self.primary.tick)
    return 'CPU (%0.1f%%, avg: %0.1f%%):' % (self.primary.latest_value, avg)

  def secondary_header(self, width):
    # memory sizes are converted from MB to B before generating labels

    usage_label = str_tools.size_label(self.secondary.latest_value * 1048576, 1)

    avg = self.secondary.total / max(1, self.secondary.tick)
    avg_label = str_tools.size_label(avg * 1048576, 1)

    return 'Memory (%s, avg: %s):' % (usage_label, avg_label)

  def bandwidth_event(self, event):
    """
    Fetch the cached measurement of resource usage from the ResourceTracker.
    """

    resource_tracker = arm.util.tracker.get_resource_tracker()

    if resource_tracker and resource_tracker.run_counter() != self._last_counter:
      resources = resource_tracker.get_value()
      self.primary.update(resources.cpu_sample * 100)  # decimal percentage to whole numbers
      self.secondary.update(resources.memory_bytes / 1048576)  # translate size to MB so axis labels are short
      self._last_counter = resource_tracker.run_counter()


class GraphPanel(panel.Panel):
  """
  Panel displaying a graph, drawing statistics from custom GraphStats
  implementations.
  """

  def __init__(self, stdscr):
    panel.Panel.__init__(self, stdscr, 'graph', 0)
    self.update_interval = CONFIG['features.graph.interval']

    if self.update_interval not in CONFIG['attr.graph.intervals']:
      self.update_interval = 'each second'
      log.warn("'%s' isn't a valid graphing interval, options are: %s" % (CONFIG['features.graph.interval'], ', '.join(CONFIG['attr.graph.intervals'])))

    self.bounds = list(Bounds)[CONFIG['features.graph.bound']]
    self.graph_height = CONFIG['features.graph.height']
    self.current_display = None    # label of the stats currently being displayed
    self._accounting_stats = None
    self._last_redraw = 0

    self.stats = {
      GraphStat.BANDWIDTH: BandwidthStats(),
      GraphStat.SYSTEM_RESOURCES: ResourceStats(),
    }

    if CONFIG['features.panels.show.connection']:
      self.stats[GraphStat.CONNECTIONS] = ConnStats()

    self.set_pause_attr('stats')
    self.set_pause_attr('_accounting_stats')

    try:
      initial_stats = GRAPH_INIT_STATS.get(CONFIG['features.graph.type'])
      self.set_stats(initial_stats)
    except ValueError:
      pass  # invalid stats, maybe connections when lookups are disabled

    # prepopulates bandwidth values from state file

    if CONFIG["features.graph.bw.prepopulate"] and tor_controller().is_alive():
      try:
        missing_seconds = self.stats[GraphStat.BANDWIDTH].prepopulate_from_state()

        if missing_seconds:
          log.notice(msg('panel.graphing.prepopulation_successful', duration = str_tools.time_label(missing_seconds, 0, True)))
        else:
          log.notice(msg('panel.graphing.prepopulation_all_successful'))

        self.update_interval = '15 minute'
      except ValueError as exc:
        log.info(msg('panel.graphing.prepopulation_failure', error = str(exc)))

    controller = tor_controller()
    controller.add_event_listener(self.bandwidth_event, stem.control.EventType.BW)
    controller.add_status_listener(self.reset_listener)

  def bandwidth_event(self, event):
    if not CONFIG['features.graph.bw.accounting.show']:
      self._accounting_stats = None
    elif not self._accounting_stats or time.time() - self._accounting_stats.retrieved >= ACCOUNTING_RATE:
      old_accounting_stats = self._accounting_stats
      self._accounting_stats = tor_controller().get_accounting_stats(None)

      if bool(old_accounting_stats) != bool(self._accounting_stats):
        # we either added or removed accounting info, redraw the whole screen since this changes our height

        arm.controller.get_controller().redraw()

    update_rate = int(CONFIG['attr.graph.intervals'][self.update_interval])

    if time.time() - self._last_redraw > update_rate:
      self.redraw(True)

  def reset_listener(self, controller, event_type, _):
    self.redraw(True)

  def get_update_interval(self):
    """
    Provides the rate that we update the graph at.
    """

    return self.update_interval

  def set_update_interval(self, update_interval):
    """
    Sets the rate that we update the graph at.

    Arguments:
      update_interval - update time enum
    """

    self.update_interval = update_interval

  def get_bounds_type(self):
    """
    Provides the type of graph bounds used.
    """

    return self.bounds

  def set_bounds_type(self, bounds_type):
    """
    Sets the type of graph boundaries we use.

    Arguments:
      bounds_type - graph bounds enum
    """

    self.bounds = bounds_type

  def get_height(self):
    """
    Provides the height requested by the currently displayed GraphStats (zero
    if hidden).
    """

    if self.current_display:
      height = DEFAULT_CONTENT_HEIGHT + self.graph_height
    else:
      height = 0

    if self.current_display == GraphStat.BANDWIDTH and self._accounting_stats:
      height += 3

    return height

  def set_graph_height(self, new_graph_height):
    """
    Sets the preferred height used for the graph (restricted to the
    MIN_GRAPH_HEIGHT minimum).

    Arguments:
      new_graph_height - new height for the graph
    """

    self.graph_height = max(MIN_GRAPH_HEIGHT, new_graph_height)

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
              self.set_graph_height(self.graph_height + 1)
          elif key.match('up'):
            self.set_graph_height(self.graph_height - 1)
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
      self.bounds = Bounds.next(self.bounds)
      self.redraw(True)
    elif key.match('s'):
      # provides a menu to pick the graphed stats

      available_stats = self.stats.keys()
      available_stats.sort()

      # uses sorted, camel cased labels for the options

      options = ['None']

      for label in available_stats:
        words = label.split()
        options.append(' '.join(word[0].upper() + word[1:] for word in words))

      if self.current_display:
        initial_selection = available_stats.index(self.current_display) + 1
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

      options = CONFIG['attr.graph.intervals'].keys()
      selection = arm.popups.show_menu('Update Interval:', options, CONFIG['attr.graph.intervals'].keys().index(self.update_interval))

      if selection != -1:
        self.update_interval = CONFIG['attr.graph.intervals'].keys()[selection]
    else:
      return False

    return True

  def get_help(self):
    return [
      ('r', 'resize graph', None),
      ('s', 'graphed stats', self.current_display if self.current_display else 'none'),
      ('b', 'graph bounds', self.bounds.lower()),
      ('i', 'graph update interval', self.update_interval),
    ]

  def draw(self, width, height):
    if not self.current_display:
      return

    self._last_redraw = time.time()

    param = self.get_attr('stats')[self.current_display]
    graph_column = min((width - 10) / 2, CONFIG['features.graph.max_width'])

    if self.is_title_visible():
      title_stats = str_tools.join(param.title_stats, ', ', width - len(param.title) - 4)
      title = '%s (%s):' % (param.title, title_stats) if title_stats else '%s:' % param.title
      self.addstr(0, 0, title, curses.A_STANDOUT)

    # top labels

    left, right = param.primary_header(width / 2), param.secondary_header(width / 2)

    if left:
      self.addstr(1, 0, left, curses.A_BOLD, PRIMARY_COLOR)

    if right:
      self.addstr(1, graph_column + 5, right, curses.A_BOLD, SECONDARY_COLOR)

    # determines max/min value on the graph

    if self.bounds == Bounds.GLOBAL_MAX:
      primary_max_bound = param.primary.max_value[self.update_interval]
      secondary_max_bound = param.secondary.max_value[self.update_interval]
    else:
      # both Bounds.LOCAL_MAX and Bounds.TIGHT use local maxima
      if graph_column < 2:
        # nothing being displayed
        primary_max_bound, secondary_max_bound = 0, 0
      else:
        primary_max_bound = max(param.primary.values[self.update_interval][:graph_column])
        secondary_max_bound = max(param.secondary.values[self.update_interval][:graph_column])

    primary_min_bound = secondary_min_bound = 0

    if self.bounds == Bounds.TIGHT:
      primary_min_bound = min(param.primary.values[self.update_interval][:graph_column])
      secondary_min_bound = min(param.secondary.values[self.update_interval][:graph_column])

      # if the max = min (ie, all values are the same) then use zero lower
      # bound so a graph is still displayed

      if primary_min_bound == primary_max_bound:
        primary_min_bound = 0

      if secondary_min_bound == secondary_max_bound:
        secondary_min_bound = 0

    # displays upper and lower bounds

    self.addstr(2, 0, '%4i' % primary_max_bound, PRIMARY_COLOR)
    self.addstr(self.graph_height + 1, 0, '%4i' % primary_min_bound, PRIMARY_COLOR)

    self.addstr(2, graph_column + 5, '%4i' % secondary_max_bound, SECONDARY_COLOR)
    self.addstr(self.graph_height + 1, graph_column + 5, '%4i' % secondary_min_bound, SECONDARY_COLOR)

    # displays intermediate bounds on every other row

    if CONFIG['features.graph.showIntermediateBounds']:
      ticks = (self.graph_height - 3) / 2

      for i in range(ticks):
        row = self.graph_height - (2 * i) - 3

        if self.graph_height % 2 == 0 and i >= (ticks / 2):
          row -= 1

        if primary_min_bound != primary_max_bound:
          primary_val = (primary_max_bound - primary_min_bound) * (self.graph_height - row - 1) / (self.graph_height - 1)

          if primary_val not in (primary_min_bound, primary_max_bound):
            self.addstr(row + 2, 0, '%4i' % primary_val, PRIMARY_COLOR)

        if secondary_min_bound != secondary_max_bound:
          secondary_val = (secondary_max_bound - secondary_min_bound) * (self.graph_height - row - 1) / (self.graph_height - 1)

          if secondary_val not in (secondary_min_bound, secondary_max_bound):
            self.addstr(row + 2, graph_column + 5, '%4i' % secondary_val, SECONDARY_COLOR)

    # creates bar graph (both primary and secondary)

    for col in range(graph_column):
      column_count = int(param.primary.values[self.update_interval][col]) - primary_min_bound
      column_height = int(min(self.graph_height, self.graph_height * column_count / (max(1, primary_max_bound) - primary_min_bound)))

      for row in range(column_height):
        self.addstr(self.graph_height + 1 - row, col + 5, ' ', curses.A_STANDOUT, PRIMARY_COLOR)

      column_count = int(param.secondary.values[self.update_interval][col]) - secondary_min_bound
      column_height = int(min(self.graph_height, self.graph_height * column_count / (max(1, secondary_max_bound) - secondary_min_bound)))

      for row in range(column_height):
        self.addstr(self.graph_height + 1 - row, col + graph_column + 10, ' ', curses.A_STANDOUT, SECONDARY_COLOR)

    # bottom labeling of x-axis

    interval_sec = int(CONFIG['attr.graph.intervals'][self.update_interval])  # seconds per labeling

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

      self.addstr(self.graph_height + 2, 4 + loc, time_label, PRIMARY_COLOR)
      self.addstr(self.graph_height + 2, graph_column + 10 + loc, time_label, SECONDARY_COLOR)

    # if display is narrow, overwrites x-axis labels with avg / total stats

    labeling_line = DEFAULT_CONTENT_HEIGHT + self.graph_height - 2

    if self.current_display == GraphStat.BANDWIDTH and width <= COLLAPSE_WIDTH:
      # clears line

      self.addstr(labeling_line, 0, ' ' * width)
      graph_column = min((width - 10) / 2, CONFIG['features.graph.max_width'])

      runtime = time.time() - param.start_time
      primary_footer = 'total: %s, avg: %s/sec' % (_size_label(param.primary.total * 1024), _size_label(param.primary.total / runtime * 1024))
      secondary_footer = 'total: %s, avg: %s/sec' % (_size_label(param.secondary.total * 1024), _size_label(param.secondary.total / runtime * 1024))

      self.addstr(labeling_line, 1, primary_footer, PRIMARY_COLOR)
      self.addstr(labeling_line, graph_column + 6, secondary_footer, SECONDARY_COLOR)

    # provides accounting stats if enabled

    accounting_stats = self.get_attr('_accounting_stats')

    if self.current_display == GraphStat.BANDWIDTH and accounting_stats:
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

    return self.current_display

  def set_stats(self, label):
    """
    Sets the currently displayed stats instance, hiding panel if None.
    """

    if label != self.current_display:
      if not label:
        self.current_display = None
      elif label in self.stats.keys():
        self.current_display = label
      else:
        raise ValueError('Unrecognized stats label: %s' % label)

  def copy_attr(self, attr):
    if attr == 'stats':
      return dict([(key, type(self.stats[key])(self.stats[key])) for key in self.stats])
    else:
      return panel.Panel.copy_attr(self, attr)


def _size_label(byte_count):
  return str_tools.size_label(byte_count, 1, is_bytes = CONFIG['features.graph.bw.transferInBytes'])
