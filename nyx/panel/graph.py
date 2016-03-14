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

import collections
import copy
import curses
import time

import nyx.controller
import nyx.panel
import nyx.popups
import nyx.tracker

from nyx import join, msg, tor_controller
from nyx.curses import RED, GREEN, CYAN, BOLD, HIGHLIGHT
from stem.control import EventType, Listener
from stem.util import conf, enum, log, str_tools, system

GraphStat = enum.Enum(('BANDWIDTH', 'bandwidth'), ('CONNECTIONS', 'connections'), ('SYSTEM_RESOURCES', 'resources'))
Interval = enum.Enum(('EACH_SECOND', 'each second'), ('FIVE_SECONDS', '5 seconds'), ('THIRTY_SECONDS', '30 seconds'), ('MINUTELY', 'minutely'), ('FIFTEEN_MINUTE', '15 minute'), ('THIRTY_MINUTE', '30 minute'), ('HOURLY', 'hourly'), ('DAILY', 'daily'))
Bounds = enum.Enum(('GLOBAL_MAX', 'global_max'), ('LOCAL_MAX', 'local_max'), ('TIGHT', 'tight'))

DrawAttributes = collections.namedtuple('DrawAttributes', ('stat', 'subgraph_height', 'subgraph_width', 'interval', 'bounds_type', 'accounting', 'right_to_left'))

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

PRIMARY_COLOR, SECONDARY_COLOR = GREEN, CYAN

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


CONFIG = conf.config_dict('nyx', {
  'attr.hibernate_color': {},
  'attr.graph.title': {},
  'attr.graph.header.primary': {},
  'attr.graph.header.secondary': {},
  'features.graph.height': 7,
  'features.graph.type': GraphStat.BANDWIDTH,
  'features.graph.interval': Interval.EACH_SECOND,
  'features.graph.bound': Bounds.LOCAL_MAX,
  'features.graph.max_width': 300,  # we need some sort of max size so we know how much graph data to retain
  'features.graph.right_to_left': False,
  'features.panels.show.connection': True,
  'features.graph.bw.transferInBytes': False,
  'features.graph.bw.accounting.show': True,
}, conf_handler)


class GraphData(object):
  """
  Graphable statistical information.

  :var int latest_value: last value we recorded
  :var int total: sum of all values we've recorded
  :var int tick: number of events we've processed
  :var dict values: mapping of intervals to an array of samplings from newest to oldest
  :var dict max_value: mapping of intervals to the maximum value it has had
  """

  def __init__(self, clone = None, category = None, is_primary = True):
    if clone:
      self.latest_value = clone.latest_value
      self.total = clone.total
      self.tick = clone.tick
      self.values = copy.deepcopy(clone.values)
      self.max_value = dict(clone.max_value)

      self._category = clone._category
      self._is_primary = clone._is_primary
      self._in_process_value = dict(clone._in_process_value)
    else:
      self.latest_value = 0
      self.total = 0
      self.tick = 0
      self.values = dict([(i, CONFIG['features.graph.max_width'] * [0]) for i in Interval])
      self.max_value = dict([(i, 0) for i in Interval])

      self._category = category
      self._is_primary = is_primary
      self._in_process_value = dict([(i, 0) for i in Interval])

  def average(self):
    return self.total / max(1, self.tick)

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

  def header(self, width):
    """
    Provides the description above a subgraph.

    :param int width: maximum length of the header

    :returns: **str** with our graph header
    """

    return self._category._header(width, self._is_primary)

  def y_axis_label(self, value):
    """
    Provides the label we should display on our y-axis.

    :param int value: value being shown on the y-axis

    :returns: **str** with our y-axis label
    """

    return self._category._y_axis_label(value, self._is_primary)


class GraphCategory(object):
  """
  Category for the graph. This maintains two subgraphs, updating them each
  second with updated stats.

  :var GraphData primary: first subgraph
  :var GraphData secondary: second subgraph
  :var float start_time: unix timestamp for when we started
  """

  def __init__(self, clone = None):
    if clone:
      self.primary = GraphData(clone.primary)
      self.secondary = GraphData(clone.secondary)
      self.start_time = clone.start_time
      self._title_stats = list(clone._title_stats)
      self._primary_header_stats = list(clone._primary_header_stats)
      self._secondary_header_stats = list(clone._secondary_header_stats)
    else:
      self.primary = GraphData(category = self, is_primary = True)
      self.secondary = GraphData(category = self, is_primary = False)
      self.start_time = time.time()
      self._title_stats = []
      self._primary_header_stats = []
      self._secondary_header_stats = []

  def stat_type(self):
    """
    Provides the GraphStat this graph is for.

    :returns: **GraphStat** of this graph
    """

    raise NotImplementedError('Should be implemented by subclasses')

  def title(self, width):
    """
    Provides a graph title that fits in the given width.

    :param int width: maximum length of the title

    :returns: **str** with our title
    """

    title = CONFIG['attr.graph.title'].get(self.stat_type(), '')
    title_stats = join(self._title_stats, ', ', width - len(title) - 4)
    return '%s (%s):' % (title, title_stats) if title_stats else title + ':'

  def bandwidth_event(self, event):
    """
    Called when it's time to process another event. All graphs use tor BW
    events to keep in sync with each other (this happens once per second).
    """

    pass

  def _header(self, width, is_primary):
    if is_primary:
      header = CONFIG['attr.graph.header.primary'].get(self.stat_type(), '')
      header_stats = self._primary_header_stats
    else:
      header = CONFIG['attr.graph.header.secondary'].get(self.stat_type(), '')
      header_stats = self._secondary_header_stats

    header_stats = join(header_stats, '', width - len(header) - 4)
    return '%s (%s):' % (header, header_stats) if header_stats else '%s:' % header

  def _y_axis_label(self, value, is_primary):
    return str(value)


class BandwidthStats(GraphCategory):
  """
  Tracks tor's bandwidth usage.
  """

  def __init__(self, clone = None):
    GraphCategory.__init__(self, clone)

    if not clone:
      # fill in past bandwidth information

      controller = tor_controller()
      bw_entries, is_successful = controller.get_info('bw-event-cache', None), True

      if bw_entries:
        for entry in bw_entries.split():
          entry_comp = entry.split(',')

          if len(entry_comp) != 2 or not entry_comp[0].isdigit() or not entry_comp[1].isdigit():
            log.warn(msg('panel.graphing.bw_event_cache_malformed', response = bw_entries))
            is_successful = False
            break

          self.primary.update(int(entry_comp[0]))
          self.secondary.update(int(entry_comp[1]))

        if is_successful:
          log.info(msg('panel.graphing.prepopulation_successful', duration = str_tools.time_label(len(bw_entries.split()), is_long = True)))

      read_total = controller.get_info('traffic/read', None)
      write_total = controller.get_info('traffic/written', None)
      start_time = system.start_time(controller.get_pid(None))

      if read_total and write_total and start_time:
        self.primary.total = int(read_total)
        self.secondary.total = int(write_total)
        self.start_time = start_time

  def stat_type(self):
    return GraphStat.BANDWIDTH

  def _y_axis_label(self, value, is_primary):
    return _size_label(value, 0)

  def bandwidth_event(self, event):
    self.primary.update(event.read)
    self.secondary.update(event.written)

    self._primary_header_stats = [
      '%-14s' % ('%s/sec' % _size_label(self.primary.latest_value)),
      '- avg: %s/sec' % _size_label(self.primary.total / (time.time() - self.start_time)),
      ', total: %s' % _size_label(self.primary.total),
    ]

    self._secondary_header_stats = [
      '%-14s' % ('%s/sec' % _size_label(self.secondary.latest_value)),
      '- avg: %s/sec' % _size_label(self.secondary.total / (time.time() - self.start_time)),
      ', total: %s' % _size_label(self.secondary.total),
    ]

    controller = tor_controller()

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


class ConnectionStats(GraphCategory):
  """
  Tracks number of inbound and outbound connections.
  """

  def stat_type(self):
    return GraphStat.CONNECTIONS

  def bandwidth_event(self, event):
    inbound_count, outbound_count = 0, 0

    controller = tor_controller()
    or_ports = controller.get_ports(Listener.OR, [])
    dir_ports = controller.get_ports(Listener.DIR, [])
    control_ports = controller.get_ports(Listener.CONTROL, [])

    for entry in nyx.tracker.get_connection_tracker().get_value():
      if entry.local_port in or_ports or entry.local_port in dir_ports:
        inbound_count += 1
      elif entry.local_port in control_ports:
        pass  # control connection
      else:
        outbound_count += 1

    self.primary.update(inbound_count)
    self.secondary.update(outbound_count)

    self._primary_header_stats = [str(self.primary.latest_value), ', avg: %s' % self.primary.average()]
    self._secondary_header_stats = [str(self.secondary.latest_value), ', avg: %s' % self.secondary.average()]


class ResourceStats(GraphCategory):
  """
  Tracks cpu and memory usage of the tor process.
  """

  def stat_type(self):
    return GraphStat.SYSTEM_RESOURCES

  def _y_axis_label(self, value, is_primary):
    return '%i%%' % value if is_primary else str_tools.size_label(value)

  def bandwidth_event(self, event):
    resources = nyx.tracker.get_resource_tracker().get_value()
    self.primary.update(resources.cpu_sample * 100)  # decimal percentage to whole numbers
    self.secondary.update(resources.memory_bytes)

    self._primary_header_stats = ['%0.1f%%' % self.primary.latest_value, ', avg: %0.1f%%' % self.primary.average()]
    self._secondary_header_stats = [str_tools.size_label(self.secondary.latest_value, 1), ', avg: %s' % str_tools.size_label(self.secondary.average(), 1)]


class GraphPanel(nyx.panel.Panel):
  """
  Panel displaying graphical information of GraphCategory instances.
  """

  def __init__(self, stdscr):
    nyx.panel.Panel.__init__(self, stdscr, 'graph', 0)

    self._displayed_stat = None if CONFIG['features.graph.type'] == 'none' else CONFIG['features.graph.type']
    self._update_interval = CONFIG['features.graph.interval']
    self._bounds = CONFIG['features.graph.bound']
    self._graph_height = CONFIG['features.graph.height']

    self._accounting_stats = None

    self._stats = {
      GraphStat.BANDWIDTH: BandwidthStats(),
      GraphStat.SYSTEM_RESOURCES: ResourceStats(),
    }

    if CONFIG['features.panels.show.connection']:
      self._stats[GraphStat.CONNECTIONS] = ConnectionStats()
    elif self._displayed_stat == GraphStat.CONNECTIONS:
      log.warn("The connection graph is unavailble when you set 'features.panels.show.connection false'.")
      self._displayed_stat = GraphStat.BANDWIDTH

    self.set_pause_attr('_stats')
    self.set_pause_attr('_accounting_stats')

    controller = tor_controller()
    controller.add_event_listener(self._update_accounting, EventType.BW)
    controller.add_event_listener(self._update_stats, EventType.BW)
    controller.add_status_listener(lambda *args: self.redraw(True))

  @property
  def displayed_stat(self):
    return self._displayed_stat

  @displayed_stat.setter
  def displayed_stat(self, value):
    if value is not None and value not in self._stats.keys():
      raise ValueError("%s isn't a graphed statistic" % value)

    self._displayed_stat = value

  def stat_options(self):
    return self._stats.keys()

  @property
  def update_interval(self):
    return self._update_interval

  @update_interval.setter
  def update_interval(self, value):
    if value not in Interval:
      raise ValueError("%s isn't a valid graphing update interval" % value)

    self._update_interval = value

  @property
  def bounds_type(self):
    return self._bounds

  @bounds_type.setter
  def bounds_type(self, value):
    if value not in Bounds:
      raise ValueError("%s isn't a valid type of bounds" % value)

    self._bounds = value

  def get_height(self):
    """
    Provides the height of the content.
    """

    if not self.displayed_stat:
      return 0

    height = DEFAULT_CONTENT_HEIGHT + self._graph_height

    if self.displayed_stat == GraphStat.BANDWIDTH and self._accounting_stats:
      height += 3

    return height

  def set_graph_height(self, new_graph_height):
    self._graph_height = max(1, new_graph_height)

  def resize_graph(self):
    """
    Prompts for user input to resize the graph panel. Options include...

      * down arrow - grow graph
      * up arrow - shrink graph
      * enter / space - set size
    """

    control = nyx.controller.get_controller()

    with nyx.panel.CURSES_LOCK:
      try:
        while True:
          msg = 'press the down/up to resize the graph, and enter when done'
          control.set_msg(msg, BOLD, True)
          curses.cbreak()  # TODO: can we drop this?
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
      self.bounds_type = Bounds.next(self.bounds_type)
      self.redraw(True)
    elif key.match('s'):
      # provides a menu to pick the graphed stats

      available_stats = sorted(self.stat_options())
      options = ['None'] + [stat.capitalize() for stat in available_stats]
      initial_selection = available_stats.index(self.displayed_stat) + 1 if self.displayed_stat else 0

      selection = nyx.popups.show_menu('Graphed Stats:', options, initial_selection)

      # applies new setting

      if selection == 0:
        self.displayed_stat = None
      elif selection != -1:
        self.displayed_stat = available_stats[selection - 1]
    elif key.match('i'):
      # provides menu to pick graph panel update interval

      selection = nyx.popups.show_menu('Update Interval:', list(Interval), list(Interval).index(self.update_interval))

      if selection != -1:
        self.update_interval = list(Interval)[selection]

      self.redraw(True)
    else:
      return False

    return True

  def get_help(self):
    return [
      ('r', 'resize graph', None),
      ('s', 'graphed stats', self.displayed_stat if self.displayed_stat else 'none'),
      ('b', 'graph bounds', self.bounds_type.replace('_', ' ')),
      ('i', 'graph update interval', self.update_interval),
    ]

  def draw(self, width, height):
    if not self.displayed_stat:
      return

    stat = self.get_attr('_stats')[self.displayed_stat]

    attr = DrawAttributes(
      stat = type(stat)(stat),  # clone the GraphCategory
      subgraph_height = self._graph_height + 2,  # graph rows + header + x-axis label
      subgraph_width = min(width / 2, CONFIG['features.graph.max_width']),
      interval = self.update_interval,
      bounds_type = self.bounds_type,
      accounting = self.get_attr('_accounting_stats'),
      right_to_left = CONFIG['features.graph.right_to_left'],
    )

    if self.is_title_visible():
      self.addstr(0, 0, attr.stat.title(width), HIGHLIGHT)

    self._draw_subgraph(attr, attr.stat.primary, 0, PRIMARY_COLOR)
    self._draw_subgraph(attr, attr.stat.secondary, attr.subgraph_width, SECONDARY_COLOR)

    if attr.stat.stat_type() == GraphStat.BANDWIDTH:
      if width <= COLLAPSE_WIDTH:
        self._draw_bandwidth_stats(attr, width)

      if attr.accounting:
        self._draw_accounting_stats(attr)

  def _draw_subgraph(self, attr, data, x, color):
    # Concering our subgraph colums, the y-axis label can be at most six
    # characters, with two spaces of padding on either side of the graph.
    # Starting with the smallest size, then possibly raise it after determing
    # the y_axis_labels.

    subgraph_columns = attr.subgraph_width - 8
    min_bound, max_bound = self._get_graph_bounds(attr, data, subgraph_columns)

    x_axis_labels = self._get_x_axis_labels(attr, subgraph_columns)
    y_axis_labels = self._get_y_axis_labels(attr, data, min_bound, max_bound)
    subgraph_columns = max(subgraph_columns, attr.subgraph_width - max([len(label) for label in y_axis_labels.values()]) - 2)
    axis_offset = max([len(label) for label in y_axis_labels.values()])

    self.addstr(1, x, data.header(attr.subgraph_width), color, BOLD)

    for x_offset, label in x_axis_labels.items():
      if attr.right_to_left:
        self.addstr(attr.subgraph_height, x + attr.subgraph_width - x_offset, label, color)
      else:
        self.addstr(attr.subgraph_height, x + x_offset + axis_offset, label, color)

    for y, label in y_axis_labels.items():
      self.addstr(y, x, label, color)

    for col in range(subgraph_columns):
      column_count = int(data.values[attr.interval][col]) - min_bound
      column_height = int(min(attr.subgraph_height - 2, (attr.subgraph_height - 2) * column_count / (max(1, max_bound) - min_bound)))

      for row in range(column_height):
        if attr.right_to_left:
          self.addstr(attr.subgraph_height - 1 - row, x + attr.subgraph_width - col - 1, ' ', color, HIGHLIGHT)
        else:
          self.addstr(attr.subgraph_height - 1 - row, x + col + axis_offset + 1, ' ', color, HIGHLIGHT)

  def _get_graph_bounds(self, attr, data, subgraph_columns):
    """
    Provides the range the graph shows (ie, its minimum and maximum value).
    """

    min_bound, max_bound = 0, 0
    values = data.values[attr.interval][:subgraph_columns]

    if attr.bounds_type == Bounds.GLOBAL_MAX:
      max_bound = data.max_value[attr.interval]
    elif subgraph_columns > 0:
      max_bound = max(values)  # local maxima

    if attr.bounds_type == Bounds.TIGHT and subgraph_columns > 0:
      min_bound = min(values)

      # if the max = min pick zero so we still display something

      if min_bound == max_bound:
        min_bound = 0

    return min_bound, max_bound

  def _get_y_axis_labels(self, attr, data, min_bound, max_bound):
    """
    Provides the labels for the y-axis. This is a mapping of the position it
    should be drawn at to its text.
    """

    y_axis_labels = {
      2: data.y_axis_label(max_bound),
      attr.subgraph_height - 1: data.y_axis_label(min_bound),
    }

    ticks = (attr.subgraph_height - 5) / 2

    for i in range(ticks):
      row = attr.subgraph_height - (2 * i) - 5

      if attr.subgraph_height % 2 == 0 and i >= (ticks / 2):
        row -= 1  # make extra gap be in the middle when we're an even size

      val = (max_bound - min_bound) * (attr.subgraph_height - row - 3) / (attr.subgraph_height - 3)

      if val not in (min_bound, max_bound):
        y_axis_labels[row + 2] = data.y_axis_label(val)

    return y_axis_labels

  def _get_x_axis_labels(self, attr, subgraph_columns):
    """
    Provides the labels for the x-axis. We include the units for only its first
    value, then bump the precision for subsequent units. For example...

      10s, 20, 30, 40, 50, 1m, 1.1, 1.3, 1.5
    """

    x_axis_labels = {}

    interval_sec = INTERVAL_SECONDS[attr.interval]
    interval_spacing = 10 if subgraph_columns >= WIDE_LABELING_GRAPH_COL else 5
    units_label, decimal_precision = None, 0

    for i in range((subgraph_columns - 4) / interval_spacing):
      x = (i + 1) * interval_spacing
      time_label = str_tools.time_label(x * interval_sec, decimal_precision)

      if not units_label:
        units_label = time_label[-1]
      elif units_label != time_label[-1]:
        # upped scale so also up precision of future measurements
        units_label = time_label[-1]
        decimal_precision += 1
      else:
        # if constrained on space then strips labeling since already provided
        time_label = time_label[:-1]

      x_axis_labels[x] = time_label

    return x_axis_labels

  def _draw_bandwidth_stats(self, attr, width):
    """
    Replaces the x-axis labeling with bandwidth stats. This is done on small
    screens since this information otherwise wouldn't fit.
    """

    labeling_line = DEFAULT_CONTENT_HEIGHT + attr.subgraph_height - 4
    self.addstr(labeling_line, 0, ' ' * width)  # clear line

    runtime = time.time() - attr.stat.start_time
    primary_footer = 'total: %s, avg: %s/sec' % (_size_label(attr.stat.primary.total), _size_label(attr.stat.primary.total / runtime))
    secondary_footer = 'total: %s, avg: %s/sec' % (_size_label(attr.stat.secondary.total), _size_label(attr.stat.secondary.total / runtime))

    self.addstr(labeling_line, 1, primary_footer, PRIMARY_COLOR)
    self.addstr(labeling_line, attr.subgraph_width + 1, secondary_footer, SECONDARY_COLOR)

  def _draw_accounting_stats(self, attr):
    y = DEFAULT_CONTENT_HEIGHT + attr.subgraph_height - 2

    if tor_controller().is_alive():
      hibernate_color = CONFIG['attr.hibernate_color'].get(attr.accounting.status, RED)

      x = self.addstr(y, 0, 'Accounting (', BOLD)
      x = self.addstr(y, x, attr.accounting.status, BOLD, hibernate_color)
      x = self.addstr(y, x, ')', BOLD)

      self.addstr(y, 35, 'Time to reset: %s' % str_tools.short_time_label(attr.accounting.time_until_reset))

      self.addstr(y + 1, 2, '%s / %s' % (attr.accounting.read_bytes, attr.accounting.read_limit), PRIMARY_COLOR)
      self.addstr(y + 1, 37, '%s / %s' % (attr.accounting.written_bytes, attr.accounting.write_limit), SECONDARY_COLOR)
    else:
      self.addstr(y, 0, 'Accounting:', BOLD)
      self.addstr(y, 12, 'Connection Closed...')

  def copy_attr(self, attr):
    if attr == '_stats':
      return dict([(key, type(self._stats[key])(self._stats[key])) for key in self._stats])
    else:
      return nyx.panel.Panel.copy_attr(self, attr)

  def _update_accounting(self, event):
    if not CONFIG['features.graph.bw.accounting.show']:
      self._accounting_stats = None
    elif not self._accounting_stats or time.time() - self._accounting_stats.retrieved >= ACCOUNTING_RATE:
      old_accounting_stats = self._accounting_stats
      self._accounting_stats = tor_controller().get_accounting_stats(None)

      # if we either added or removed accounting info then redraw the whole
      # screen to account for resizing

      if bool(old_accounting_stats) != bool(self._accounting_stats):
        nyx.controller.get_controller().redraw()

  def _update_stats(self, event):
    for stat in self._stats.values():
      stat.bandwidth_event(event)

    if self.displayed_stat:
      param = self.get_attr('_stats')[self.displayed_stat]
      update_rate = INTERVAL_SECONDS[self.update_interval]

      if param.primary.tick % update_rate == 0:
        self.redraw(True)


def _size_label(byte_count, decimal = 1):
  """
  Alias for str_tools.size_label() that accounts for if the user prefers bits
  or bytes.
  """

  return str_tools.size_label(byte_count, decimal, is_bytes = CONFIG['features.graph.bw.transferInBytes'])
