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
  'features.graph.interval': 0,
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


class GraphStats:
  """
  Module that's expected to update dynamically and provide attributes to be
  graphed. Up to two graphs (a 'primary' and 'secondary') can be displayed at a
  time and timescale parameters use the labels defined in CONFIG['attr.graph.intervals'].
  """

  def __init__(self):
    """
    Initializes parameters needed to present a graph.
    """

    # panel to be redrawn when updated (set when added to GraphPanel)

    self._graph_panel = None
    self.is_selected = False
    self.is_pause_buffer = False

    # tracked stats

    self.tick = 0                                    # number of processed events
    self.last_primary, self.last_secondary = 0, 0    # most recent registered stats
    self.primary_total, self.secondary_total = 0, 0  # sum of all stats seen

    # timescale dependent stats

    self.max_column = CONFIG['features.graph.max_width']
    self.max_primary, self.max_secondary = {}, {}
    self.primary_counts, self.secondary_counts = {}, {}

    for i in range(len(CONFIG['attr.graph.intervals'])):
      # recent rates for graph

      self.max_primary[i] = 0
      self.max_secondary[i] = 0

      # historic stats for graph, first is accumulator
      # iterative insert needed to avoid making shallow copies (nasty, nasty gotcha)

      self.primary_counts[i] = (self.max_column + 1) * [0]
      self.secondary_counts[i] = (self.max_column + 1) * [0]

    # tracks BW events

    tor_controller().add_event_listener(self.bandwidth_event, stem.control.EventType.BW)

  def clone(self, new_copy=None):
    """
    Provides a deep copy of this instance.

    Arguments:
      new_copy - base instance to build copy off of
    """

    if not new_copy:
      new_copy = GraphStats()

    new_copy.tick = self.tick
    new_copy.last_primary = self.last_primary
    new_copy.last_secondary = self.last_secondary
    new_copy.primary_total = self.primary_total
    new_copy.secondary_total = self.secondary_total
    new_copy.max_primary = dict(self.max_primary)
    new_copy.max_secondary = dict(self.max_secondary)
    new_copy.primary_counts = copy.deepcopy(self.primary_counts)
    new_copy.secondary_counts = copy.deepcopy(self.secondary_counts)
    new_copy.is_pause_buffer = True
    return new_copy

  def event_tick(self):
    """
    Called when it's time to process another event. All graphs use tor BW
    events to keep in sync with each other (this happens once a second).
    """

    pass

  def is_next_tick_redraw(self):
    """
    Provides true if the following tick (call to _process_event) will result in
    being redrawn.
    """

    if self._graph_panel and self.is_selected and not self._graph_panel.is_paused():
      # use the minimum of the current refresh rate and the panel's
      update_rate = int(CONFIG['attr.graph.intervals'].values()[self._graph_panel.update_interval])
      return (self.tick + 1) % update_rate == 0
    else:
      return False

  def get_title(self, width):
    """
    Provides top label.
    """

    return ''

  def primary_header(self, width):
    return ''

  def secondary_header(self, width):
    return ''

  def get_content_height(self):
    """
    Provides the height content should take up (not including the graph).
    """

    return DEFAULT_CONTENT_HEIGHT

  def draw(self, panel, width, height):
    """
    Allows for any custom drawing monitor wishes to append.
    """

    pass

  def bandwidth_event(self, event):
    if not self.is_pause_buffer:
      self.event_tick()

  def _process_event(self, primary, secondary):
    """
    Includes new stats in graphs and notifies associated GraphPanel of changes.
    """

    is_redraw = self.is_next_tick_redraw()

    self.last_primary, self.last_secondary = primary, secondary
    self.primary_total += primary
    self.secondary_total += secondary

    # updates for all time intervals

    self.tick += 1

    for i in range(len(CONFIG['attr.graph.intervals'])):
      lable, timescale = CONFIG['attr.graph.intervals'].items()[i]
      timescale = int(timescale)

      self.primary_counts[i][0] += primary
      self.secondary_counts[i][0] += secondary

      if self.tick % timescale == 0:
        self.max_primary[i] = max(self.max_primary[i], self.primary_counts[i][0] / timescale)
        self.primary_counts[i][0] /= timescale
        self.primary_counts[i].insert(0, 0)
        del self.primary_counts[i][self.max_column + 1:]

        self.max_secondary[i] = max(self.max_secondary[i], self.secondary_counts[i][0] / timescale)
        self.secondary_counts[i][0] /= timescale
        self.secondary_counts[i].insert(0, 0)
        del self.secondary_counts[i][self.max_column + 1:]

    if is_redraw and self._graph_panel:
      self._graph_panel.redraw(True)


class BandwidthStats(GraphStats):
  """
  Uses tor BW events to generate bandwidth usage graph.
  """

  def __init__(self, is_pause_buffer = False):
    GraphStats.__init__(self)

    # listens for tor reload (sighup) events which can reset the bandwidth
    # rate/burst and if tor's using accounting

    controller = tor_controller()
    self._title_stats = []
    self._accounting_stats = None

    if not is_pause_buffer:
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
      self.primary_total = int(read_total) / 1024  # Bytes -> KB
      self.secondary_total = int(write_total) / 1024  # Bytes -> KB
      self.start_time = start_time
    else:
      self.start_time = time.time()

  def clone(self, new_copy = None):
    if not new_copy:
      new_copy = BandwidthStats(True)

    new_copy._accounting_stats = self._accounting_stats
    new_copy._title_stats = self._title_stats

    return GraphStats.clone(self, new_copy)

  def reset_listener(self, controller, event_type, _):
    # updates title parameters and accounting status if they changed

    self.new_desc_event(None)  # updates title params

    if event_type in (State.INIT, State.RESET) and CONFIG['features.graph.bw.accounting.show']:
      is_accounting_enabled = controller.get_info('accounting/enabled', None) == '1'

      if is_accounting_enabled != bool(self._accounting_stats):
        self._accounting_stats = tor_controller().get_accounting_stats(None)

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

    for interval_rate in CONFIG['attr.graph.intervals'].values():
      if int(interval_rate) == 900:
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
    if self._accounting_stats and self.is_next_tick_redraw():
      if time.time() - self._accounting_stats.retrieved >= ACCOUNTING_RATE:
        self._accounting_stats = tor_controller().get_accounting_stats(None)

    # scales units from B to KB for graphing

    self._process_event(event.read / 1024.0, event.written / 1024.0)

  def draw(self, panel, width, height):
    # line of the graph's x-axis labeling

    labeling_line = GraphStats.get_content_height(self) + panel.graph_height - 2

    # if display is narrow, overwrites x-axis labels with avg / total stats

    if width <= COLLAPSE_WIDTH:
      # clears line

      panel.addstr(labeling_line, 0, ' ' * width)
      graph_column = min((width - 10) / 2, self.max_column)

      runtime = time.time() - self.start_time
      primary_footer = 'total: %s, avg: %s/sec' % (_size_label(self.primary_total * 1024), _size_label(self.primary_total / runtime * 1024))
      secondary_footer = 'total: %s, avg: %s/sec' % (_size_label(self.secondary_total * 1024), _size_label(self.secondary_total / runtime * 1024))

      panel.addstr(labeling_line, 1, primary_footer, PRIMARY_COLOR)
      panel.addstr(labeling_line, graph_column + 6, secondary_footer, SECONDARY_COLOR)

    # provides accounting stats if enabled

    if self._accounting_stats:
      if tor_controller().is_alive():
        hibernate_color = CONFIG['attr.hibernate_color'].get(self._accounting_stats.status, 'red')

        x, y = 0, labeling_line + 2
        x = panel.addstr(y, x, 'Accounting (', curses.A_BOLD)
        x = panel.addstr(y, x, self._accounting_stats.status, curses.A_BOLD, hibernate_color)
        x = panel.addstr(y, x, ')', curses.A_BOLD)

        panel.addstr(y, 35, 'Time to reset: %s' % str_tools.short_time_label(self._accounting_stats.time_until_reset))

        panel.addstr(y + 1, 2, '%s / %s' % (self._accounting_stats.read_bytes, self._accounting_stats.read_limit), PRIMARY_COLOR)
        panel.addstr(y + 1, 37, '%s / %s' % (self._accounting_stats.written_bytes, self._accounting_stats.write_limit), SECONDARY_COLOR)
      else:
        panel.addstr(labeling_line + 2, 0, 'Accounting:', curses.A_BOLD)
        panel.addstr(labeling_line + 2, 12, 'Connection Closed...')

  def get_title(self, width):
    stats_label = str_tools.join(self._title_stats, ', ', width - 13)
    return 'Bandwidth (%s):' % stats_label if stats_label else 'Bandwidth:'

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

  def get_content_height(self):
    base_height = GraphStats.get_content_height(self)
    return base_height + 3 if self._accounting_stats else base_height

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

      self._title_stats = stats


class ConnStats(GraphStats):
  """
  Tracks number of connections, counting client and directory connections as
  outbound. Control connections are excluded from counts.
  """

  def clone(self, new_copy=None):
    if not new_copy:
      new_copy = ConnStats()

    return GraphStats.clone(self, new_copy)

  def event_tick(self):
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

    self._process_event(inbound_count, outbound_count)

  def get_title(self, width):
    return 'Connection Count:'

  def primary_header(self, width):
    avg = self.primary_total / max(1, self.tick)
    return 'Inbound (%s, avg: %s):' % (self.last_primary, avg)

  def secondary_header(self, width):
    avg = self.secondary_total / max(1, self.tick)
    return 'Outbound (%s, avg: %s):' % (self.last_secondary, avg)


class ResourceStats(GraphStats):
  """
  System resource usage tracker.
  """

  def __init__(self):
    GraphStats.__init__(self)
    self._last_counter = None

  def clone(self, new_copy=None):
    if not new_copy:
      new_copy = ResourceStats()

    return GraphStats.clone(self, new_copy)

  def get_title(self, width):
    return 'System Resources:'

  def primary_header(self, width):
    avg = self.primary_total / max(1, self.tick)
    return 'CPU (%0.1f%%, avg: %0.1f%%):' % (self.last_primary, avg)

  def secondary_header(self, width):
    # memory sizes are converted from MB to B before generating labels

    usage_label = str_tools.size_label(self.last_secondary * 1048576, 1)

    avg = self.secondary_total / max(1, self.tick)
    avg_label = str_tools.size_label(avg * 1048576, 1)

    return 'Memory (%s, avg: %s):' % (usage_label, avg_label)

  def event_tick(self):
    """
    Fetch the cached measurement of resource usage from the ResourceTracker.
    """

    resource_tracker = arm.util.tracker.get_resource_tracker()

    if resource_tracker and resource_tracker.run_counter() != self._last_counter:
      resources = resource_tracker.get_value()
      primary = resources.cpu_sample * 100  # decimal percentage to whole numbers
      secondary = resources.memory_bytes / 1048576  # translate size to MB so axis labels are short

      self._last_counter = resource_tracker.run_counter()
      self._process_event(primary, secondary)


class GraphPanel(panel.Panel):
  """
  Panel displaying a graph, drawing statistics from custom GraphStats
  implementations.
  """

  def __init__(self, stdscr):
    panel.Panel.__init__(self, stdscr, 'graph', 0)
    self.update_interval = CONFIG['features.graph.interval']

    if self.update_interval < 0 or self.update_interval > len(CONFIG['attr.graph.intervals']) - 1:
      self.update_interval = 0  # user configured it with a value that's out of bounds

    self.bounds = list(Bounds)[CONFIG['features.graph.bound']]
    self.graph_height = CONFIG['features.graph.height']
    self.current_display = None    # label of the stats currently being displayed

    self.stats = {
      GraphStat.BANDWIDTH: BandwidthStats(),
      GraphStat.SYSTEM_RESOURCES: ResourceStats(),
    }

    if CONFIG['features.panels.show.connection']:
      self.stats[GraphStat.CONNECTIONS] = ConnStats()

    for stat in self.stats.values():
      stat._graph_panel = self

    self.set_pause_attr('stats')

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

        self.update_interval = 4
      except ValueError as exc:
        log.info(msg('panel.graphing.prepopulation_failure', error = str(exc)))

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
      return self.stats[self.current_display].get_content_height() + self.graph_height
    else:
      return 0

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
      selection = arm.popups.show_menu('Update Interval:', options, self.update_interval)

      if selection != -1:
        self.update_interval = selection
    else:
      return False

    return True

  def get_help(self):
    return [
      ('r', 'resize graph', None),
      ('s', 'graphed stats', self.current_display if self.current_display else 'none'),
      ('b', 'graph bounds', self.bounds.lower()),
      ('i', 'graph update interval', CONFIG['attr.graph.intervals'].keys()[self.update_interval]),
    ]

  def draw(self, width, height):
    if not self.current_display:
      return

    param = self.get_attr('stats')[self.current_display]
    graph_column = min((width - 10) / 2, param.max_column)

    if self.is_title_visible():
      self.addstr(0, 0, param.get_title(width), curses.A_STANDOUT)

    # top labels

    left, right = param.primary_header(width / 2), param.secondary_header(width / 2)

    if left:
      self.addstr(1, 0, left, curses.A_BOLD, PRIMARY_COLOR)

    if right:
      self.addstr(1, graph_column + 5, right, curses.A_BOLD, SECONDARY_COLOR)

    # determines max/min value on the graph

    if self.bounds == Bounds.GLOBAL_MAX:
      primary_max_bound = int(param.max_primary[self.update_interval])
      secondary_max_bound = int(param.max_secondary[self.update_interval])
    else:
      # both Bounds.LOCAL_MAX and Bounds.TIGHT use local maxima
      if graph_column < 2:
        # nothing being displayed
        primary_max_bound, secondary_max_bound = 0, 0
      else:
        primary_max_bound = int(max(param.primary_counts[self.update_interval][1:graph_column + 1]))
        secondary_max_bound = int(max(param.secondary_counts[self.update_interval][1:graph_column + 1]))

    primary_min_bound = secondary_min_bound = 0

    if self.bounds == Bounds.TIGHT:
      primary_min_bound = int(min(param.primary_counts[self.update_interval][1:graph_column + 1]))
      secondary_min_bound = int(min(param.secondary_counts[self.update_interval][1:graph_column + 1]))

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
      column_count = int(param.primary_counts[self.update_interval][col + 1]) - primary_min_bound
      column_height = min(self.graph_height, self.graph_height * column_count / (max(1, primary_max_bound) - primary_min_bound))

      for row in range(column_height):
        self.addstr(self.graph_height + 1 - row, col + 5, ' ', curses.A_STANDOUT, PRIMARY_COLOR)

      column_count = int(param.secondary_counts[self.update_interval][col + 1]) - secondary_min_bound
      column_height = min(self.graph_height, self.graph_height * column_count / (max(1, secondary_max_bound) - secondary_min_bound))

      for row in range(column_height):
        self.addstr(self.graph_height + 1 - row, col + graph_column + 10, ' ', curses.A_STANDOUT, SECONDARY_COLOR)

    # bottom labeling of x-axis

    interval_sec = int(CONFIG['attr.graph.intervals'].values()[self.update_interval])  # seconds per labeling

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

    param.draw(self, width, height)  # allows current stats to modify the display

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
      if self.current_display:
        self.stats[self.current_display].is_selected = False

      if not label:
        self.current_display = None
      elif label in self.stats.keys():
        self.current_display = label
        self.stats[self.current_display].is_selected = True
      else:
        raise ValueError('Unrecognized stats label: %s' % label)

  def copy_attr(self, attr):
    if attr == 'stats':
      # uses custom clone method to copy GraphStats instances
      return dict([(key, self.stats[key].clone()) for key in self.stats])
    else:
      return panel.Panel.copy_attr(self, attr)


def _size_label(byte_count):
  return str_tools.size_label(byte_count, 1, is_bytes = CONFIG['features.graph.bw.transferInBytes'])
