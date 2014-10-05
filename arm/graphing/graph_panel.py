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

import arm.popups
import arm.controller

import stem.control

from arm.util import panel, tor_controller, ui_tools

from stem.util import conf, enum, str_tools

# time intervals at which graphs can be updated

UPDATE_INTERVALS = [
  ('each second', 1),
  ('5 seconds', 5),
  ('30 seconds', 30),
  ('minutely', 60),
  ('15 minute', 900),
  ('30 minute', 1800),
  ('hourly', 3600),
  ('daily', 86400),
]

DEFAULT_CONTENT_HEIGHT = 4  # space needed for labeling above and below the graph
PRIMARY_COLOR, SECONDARY_COLOR = 'green', 'cyan'
MIN_GRAPH_HEIGHT = 1

# enums for graph bounds:
#   Bounds.GLOBAL_MAX - global maximum (highest value ever seen)
#   Bounds.LOCAL_MAX - local maximum (highest value currently on the graph)
#   Bounds.TIGHT - local maximum and minimum

Bounds = enum.Enum('GLOBAL_MAX', 'LOCAL_MAX', 'TIGHT')

WIDE_LABELING_GRAPH_COL = 50  # minimum graph columns to use wide spacing for x-axis labels


def conf_handler(key, value):
  if key == 'features.graph.height':
    return max(MIN_GRAPH_HEIGHT, value)
  elif key == 'features.graph.max_width':
    return max(1, value)
  elif key == 'features.graph.interval':
    return max(0, min(len(UPDATE_INTERVALS) - 1, value))
  elif key == 'features.graph.bound':
    return max(0, min(2, value))


# used for setting defaults when initializing GraphStats and GraphPanel instances

CONFIG = conf.config_dict('arm', {
  'features.graph.height': 7,
  'features.graph.interval': 0,
  'features.graph.bound': 1,
  'features.graph.max_width': 150,
  'features.graph.showIntermediateBounds': True,
}, conf_handler)


class GraphStats:
  """
  Module that's expected to update dynamically and provide attributes to be
  graphed. Up to two graphs (a 'primary' and 'secondary') can be displayed at a
  time and timescale parameters use the labels defined in UPDATE_INTERVALS.
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

    for i in range(len(UPDATE_INTERVALS)):
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
      update_rate = UPDATE_INTERVALS[self._graph_panel.update_interval][1]
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

    for i in range(len(UPDATE_INTERVALS)):
      lable, timescale = UPDATE_INTERVALS[i]

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


class GraphPanel(panel.Panel):
  """
  Panel displaying a graph, drawing statistics from custom GraphStats
  implementations.
  """

  def __init__(self, stdscr):
    panel.Panel.__init__(self, stdscr, 'graph', 0)
    self.update_interval = CONFIG['features.graph.interval']
    self.bounds = list(Bounds)[CONFIG['features.graph.bound']]
    self.graph_height = CONFIG['features.graph.height']
    self.current_display = None    # label of the stats currently being displayed
    self.stats = {}                # available stats (mappings of label -> instance)
    self.set_pause_attr('stats')

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

    panel.CURSES_LOCK.acquire()

    try:
      while True:
        msg = 'press the down/up to resize the graph, and enter when done'
        control.set_msg(msg, curses.A_BOLD, True)
        curses.cbreak()
        key = control.get_screen().getch()

        if key == curses.KEY_DOWN:
          # don't grow the graph if it's already consuming the whole display
          # (plus an extra line for the graph/log gap)

          max_height = self.parent.getmaxyx()[0] - self.top
          current_height = self.get_height()

          if current_height < max_height + 1:
            self.set_graph_height(self.graph_height + 1)
        elif key == curses.KEY_UP:
          self.set_graph_height(self.graph_height - 1)
        elif ui_tools.is_selection_key(key):
          break

        control.redraw()
    finally:
      control.set_msg()
      panel.CURSES_LOCK.release()

  def handle_key(self, key):
    is_keystroke_consumed = True

    if key == ord('r') or key == ord('R'):
      self.resize_graph()
    elif key == ord('b') or key == ord('B'):
      # uses the next boundary type
      self.bounds = Bounds.next(self.bounds)
      self.redraw(True)
    elif key == ord('s') or key == ord('S'):
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
    elif key == ord('i') or key == ord('I'):
      # provides menu to pick graph panel update interval

      options = [label for (label, _) in UPDATE_INTERVALS]
      selection = arm.popups.show_menu('Update Interval:', options, self.update_interval)

      if selection != -1:
        self.update_interval = selection
    else:
      is_keystroke_consumed = False

    return is_keystroke_consumed

  def get_help(self):
    if self.current_display:
      graphed_stats = self.current_display
    else:
      graphed_stats = 'none'

    options = []
    options.append(('r', 'resize graph', None))
    options.append(('s', 'graphed stats', graphed_stats))
    options.append(('b', 'graph bounds', self.bounds.lower()))
    options.append(('i', 'graph update interval', UPDATE_INTERVALS[self.update_interval][0]))
    return options

  def draw(self, width, height):
    """ Redraws graph panel """

    if self.current_display:
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

      interval_sec = 1  # seconds per labeling

      for i in range(len(UPDATE_INTERVALS)):
        if i == self.update_interval:
          interval_sec = UPDATE_INTERVALS[i][1]

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

  def add_stats(self, label, stats):
    """
    Makes GraphStats instance available in the panel.
    """

    stats._graph_panel = self
    self.stats[label] = stats

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
