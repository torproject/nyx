# Copyright 2011-2016, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Menu for controlling nyx.
"""

import functools

import nyx.controller
import nyx.curses
import nyx.popups
import nyx.panel.config
import nyx.panel.connection
import nyx.panel.graph
import nyx.panel.log
import nyx.panel.torrc
import nyx.controller
import nyx.tracker

import stem
import stem.util.connection

from nyx import tor_controller
from nyx.curses import RED, WHITE, NORMAL, BOLD, UNDERLINE
from stem.util import str_tools


class MenuItem(object):
  """
  Drop-down menu item.

  :var str prefix: text before our label
  :var str label: text we display
  :var str suffix: text after our label

  :var Submenu parent: submenu we reside within
  :var Submenu submenu: top-level submenu we reside within
  """

  def __init__(self, label, callback):
    self.label = label
    self.suffix = ''

    self._callback = callback
    self._parent = None

  @property
  def prefix(self):
    return ''

  @property
  def parent(self):
    return self._parent

  @property
  def submenu(self):
    return self._parent.submenu if (self._parent and self._parent._parent) else self

  def select(self):
    """
    Performs the callback for the menu item.
    """

    if self._callback:
      self._callback()

  def next(self):
    """
    Provides the next option for the submenu we're in, raising a ValueError
    if we don't have a parent.
    """

    return self._get_sibling(1)

  def prev(self):
    """
    Provides the previous option for the submenu we're in, raising a ValueError
    if we don't have a parent.
    """

    return self._get_sibling(-1)

  def _get_sibling(self, offset):
    """
    Provides our sibling with a given index offset from us, raising a
    ValueError if we don't have a parent.

    Arguments:
      offset - index offset for the sibling to be returned
    """

    if self._parent:
      my_siblings = self._parent.get_children()

      try:
        my_index = my_siblings.index(self)
        return my_siblings[(my_index + offset) % len(my_siblings)]
      except ValueError:
        # We expect a bidirectional references between submenus and their
        # children. If we don't have this then our menu's screwed up.

        msg = "The '%s' submenu doesn't contain '%s' (children: '%s')" % (self, self._parent, "', '".join(my_siblings))
        raise ValueError(msg)
    else:
      raise ValueError("Menu option '%s' doesn't have a parent" % self)


class Submenu(MenuItem):
  """
  Menu item that lists other menu options.
  """

  def __init__(self, label):
    MenuItem.__init__(self, label, None)
    self.suffix = ' >'
    self._children = []

  def add(self, menu_item):
    """
    Adds the given menu item to our listing. This raises a ValueError if the
    item already has a parent.

    Arguments:
      menu_item - menu option to be added
    """

    if menu_item.parent:
      raise ValueError("Menu option '%s' already has a parent" % menu_item)
    else:
      menu_item._parent = self
      self._children.append(menu_item)

  def get_children(self):
    """
    Provides the menu and submenus we contain.
    """

    return list(self._children)

  def is_empty(self):
    """
    True if we have no children, false otherwise.
    """

    return not bool(self._children)


class RadioMenuItem(MenuItem):
  """
  Menu item with an associated group which determines the selection. This is
  for the common single argument getter/setter pattern.
  """

  def __init__(self, label, group, arg):
    MenuItem.__init__(self, label, None)
    self._group = group
    self._arg = arg

  @property
  def prefix(self):
    return '[X] ' if self.is_selected() else '[ ] '

  def is_selected(self):
    """
    True if we're the selected item, false otherwise.
    """

    return self._arg == self._group.selected_arg

  def select(self):
    """
    Performs the group's setter action with our argument.
    """

    if not self.is_selected():
      self._group.action(self._arg)


class RadioGroup(object):
  """
  Radio button groups that RadioMenuItems can belong to.
  """

  def __init__(self, action, selected_arg):
    self.action = action
    self.selected_arg = selected_arg


def make_menu():
  """
  Constructs the base menu and all of its contents.
  """

  base_menu = Submenu('')
  base_menu.add(make_actions_menu())
  base_menu.add(make_view_menu())

  control = nyx.controller.get_controller()

  for page_panel in control.get_display_panels():
    if isinstance(page_panel, nyx.panel.graph.GraphPanel):
      base_menu.add(make_graph_menu(page_panel))
    elif isinstance(page_panel, nyx.panel.log.LogPanel):
      base_menu.add(make_log_menu(page_panel))
    elif isinstance(page_panel, nyx.panel.connection.ConnectionPanel):
      base_menu.add(make_connections_menu(page_panel))
    elif isinstance(page_panel, nyx.panel.config.ConfigPanel):
      base_menu.add(make_configuration_menu(page_panel))
    elif isinstance(page_panel, nyx.panel.torrc.TorrcPanel):
      base_menu.add(make_torrc_menu(page_panel))

  base_menu.add(make_help_menu())

  return base_menu


def make_actions_menu():
  """
  Submenu consisting of...
    Close Menu
    New Identity
    Reset Tor
    Pause / Unpause
    Exit
  """

  control = nyx.controller.get_controller()
  controller = tor_controller()
  header_panel = control.header_panel()

  actions_menu = Submenu('Actions')
  actions_menu.add(MenuItem('Close Menu', None))
  actions_menu.add(MenuItem('New Identity', header_panel.send_newnym))
  actions_menu.add(MenuItem('Reset Tor', functools.partial(controller.signal, stem.Signal.RELOAD)))

  if control.is_paused():
    label, arg = 'Unpause', False
  else:
    label, arg = 'Pause', True

  actions_menu.add(MenuItem(label, functools.partial(control.set_paused, arg)))
  actions_menu.add(MenuItem('Exit', control.quit))

  return actions_menu


def make_view_menu():
  """
  Submenu consisting of...
    [X] <Page 1>
    [ ] <Page 2>
    [ ] etc...
        Color (Submenu)
  """

  view_menu = Submenu('View')
  control = nyx.controller.get_controller()

  if control.get_page_count() > 0:
    page_group = RadioGroup(control.set_page, control.get_page())

    for i in range(control.get_page_count()):
      page_panels = control.get_display_panels(page_number = i)
      label = ' / '.join([type(panel).__name__.replace('Panel', '') for panel in page_panels])

      view_menu.add(RadioMenuItem(label, page_group, i))

  if nyx.curses.is_color_supported():
    color_menu = Submenu('Color')
    color_group = RadioGroup(nyx.curses.set_color_override, nyx.curses.get_color_override())

    color_menu.add(RadioMenuItem('All', color_group, None))

    for color in nyx.curses.Color:
      color_menu.add(RadioMenuItem(str_tools._to_camel_case(color), color_group, color))

    view_menu.add(color_menu)

  return view_menu


def make_help_menu():
  """
  Submenu consisting of...
    Hotkeys
    About
  """

  help_menu = Submenu('Help')
  help_menu.add(MenuItem('Hotkeys', nyx.popups.show_help))
  help_menu.add(MenuItem('About', nyx.popups.show_about))
  return help_menu


def make_graph_menu(graph_panel):
  """
  Submenu for the graph panel, consisting of...
    [X] <Stat 1>
    [ ] <Stat 2>
    [ ] <Stat 2>
        Resize...
        Interval (Submenu)
        Bounds (Submenu)

  Arguments:
    graph_panel - instance of the graph panel
  """

  graph_menu = Submenu('Graph')

  # stats options

  stat_group = RadioGroup(functools.partial(setattr, graph_panel, 'displayed_stat'), graph_panel.displayed_stat)
  available_stats = graph_panel.stat_options()
  available_stats.sort()

  for stat_key in ['None'] + available_stats:
    label = str_tools._to_camel_case(stat_key, divider = ' ')
    stat_key = None if stat_key == 'None' else stat_key
    graph_menu.add(RadioMenuItem(label, stat_group, stat_key))

  # resizing option

  graph_menu.add(MenuItem('Resize...', graph_panel.resize_graph))

  # interval submenu

  interval_menu = Submenu('Interval')
  interval_group = RadioGroup(functools.partial(setattr, graph_panel, 'update_interval'), graph_panel.update_interval)

  for interval in nyx.panel.graph.Interval:
    interval_menu.add(RadioMenuItem(interval, interval_group, interval))

  graph_menu.add(interval_menu)

  # bounds submenu

  bounds_menu = Submenu('Bounds')
  bounds_group = RadioGroup(functools.partial(setattr, graph_panel, 'bounds_type'), graph_panel.bounds_type)

  for bounds_type in nyx.panel.graph.Bounds:
    bounds_menu.add(RadioMenuItem(bounds_type, bounds_group, bounds_type))

  graph_menu.add(bounds_menu)

  return graph_menu


def make_log_menu(log_panel):
  """
  Submenu for the log panel, consisting of...
    Events...
    Snapshot...
    Clear
    Show / Hide Duplicates
    Filter (Submenu)

  Arguments:
    log_panel - instance of the log panel
  """

  log_menu = Submenu('Log')

  log_menu.add(MenuItem('Events...', log_panel.show_event_selection_prompt))
  log_menu.add(MenuItem('Snapshot...', log_panel.show_snapshot_prompt))
  log_menu.add(MenuItem('Clear', log_panel.clear))

  if log_panel.is_duplicates_visible():
    label, arg = 'Hide', False
  else:
    label, arg = 'Show', True

  log_menu.add(MenuItem('%s Duplicates' % label, functools.partial(log_panel.set_duplicate_visability, arg)))

  # filter submenu

  log_filter = log_panel.get_filter()

  filter_menu = Submenu('Filter')
  filter_group = RadioGroup(log_filter.select, log_filter.selection())

  filter_menu.add(RadioMenuItem('None', filter_group, None))

  for option in log_filter.latest_selections():
    filter_menu.add(RadioMenuItem(option, filter_group, option))

  filter_menu.add(MenuItem('New...', log_panel.show_filter_prompt))
  log_menu.add(filter_menu)

  return log_menu


def make_connections_menu(conn_panel):
  """
  Submenu for the connections panel, consisting of...
        Sorting...
        Resolver (Submenu)

  Arguments:
    conn_panel - instance of the connections panel
  """

  connections_menu = Submenu('Connections')

  # sorting option

  connections_menu.add(MenuItem('Sorting...', conn_panel.show_sort_dialog))

  # resolver submenu

  conn_resolver = nyx.tracker.get_connection_tracker()
  resolver_menu = Submenu('Resolver')
  resolver_group = RadioGroup(conn_resolver.set_custom_resolver, conn_resolver.get_custom_resolver())

  resolver_menu.add(RadioMenuItem('auto', resolver_group, None))

  for option in stem.util.connection.Resolver:
    resolver_menu.add(RadioMenuItem(option, resolver_group, option))

  connections_menu.add(resolver_menu)

  return connections_menu


def make_configuration_menu(config_panel):
  """
  Submenu for the configuration panel, consisting of...
    Save Config...
    Sorting...
    Filter / Unfilter Options

  Arguments:
    config_panel - instance of the configuration panel
  """

  config_menu = Submenu('Configuration')
  config_menu.add(MenuItem('Save Config...', config_panel.show_write_dialog))
  config_menu.add(MenuItem('Sorting...', config_panel.show_sort_dialog))
  return config_menu


def make_torrc_menu(torrc_panel):
  """
  Submenu for the torrc panel, consisting of...
    Reload
    Show / Hide Comments
    Show / Hide Line Numbers

  Arguments:
    torrc_panel - instance of the torrc panel
  """

  torrc_menu = Submenu('Torrc')

  if torrc_panel._show_comments:
    label, arg = 'Hide', False
  else:
    label, arg = 'Show', True

  torrc_menu.add(MenuItem('%s Comments' % label, functools.partial(torrc_panel.set_comments_visible, arg)))

  if torrc_panel._show_line_numbers:
    label, arg = 'Hide', False
  else:
    label, arg = 'Show', True
  torrc_menu.add(MenuItem('%s Line Numbers' % label, functools.partial(torrc_panel.set_line_number_visible, arg)))

  return torrc_menu


class MenuCursor:
  """
  Tracks selection and key handling in the menu.
  """

  def __init__(self, initial_selection):
    self._selection = initial_selection
    self._is_done = False

  def is_done(self):
    """
    Provides true if a selection has indicated that we should close the menu.
    False otherwise.
    """

    return self._is_done

  def get_selection(self):
    """
    Provides the currently selected menu item.
    """

    return self._selection

  def handle_key(self, key):
    is_selection_submenu = isinstance(self._selection, Submenu)

    if key.is_selection():
      if is_selection_submenu:
        if not self._selection.is_empty():
          self._selection = self._selection.get_children()[0]
      else:
        self._selection.select()
        self._is_done = True
    elif key.match('up'):
      self._selection = self._selection.prev()
    elif key.match('down'):
      self._selection = self._selection.next()
    elif key.match('left'):
      if self._selection.parent == self._selection.submenu:
        # shift to the previous main submenu

        prev_submenu = self._selection.submenu.prev()
        self._selection = prev_submenu.get_children()[0]
      else:
        # go up a submenu level

        self._selection = self._selection.parent
    elif key.match('right'):
      if is_selection_submenu:
        # open submenu (same as making a selection)

        if not self._selection.is_empty():
          self._selection = self._selection.get_children()[0]
      else:
        # shift to the next main submenu

        next_submenu = self._selection.submenu.next()
        self._selection = next_submenu.get_children()[0]
    elif key.match('esc', 'm'):
      self._is_done = True


def show_menu():
  selection_left = [0]

  def _render(subwindow):
    x = 0

    for top_level_item in menu.get_children():
      if top_level_item == cursor.get_selection().submenu:
        selection_left[0] = x
        attr = UNDERLINE
      else:
        attr = NORMAL

      x = subwindow.addstr(x, 0, ' %s ' % top_level_item.label, BOLD, attr)
      subwindow.vline(x, 0, 1)
      x += 1

  with nyx.curses.CURSES_LOCK:
    # generates the menu and uses the initial selection of the first item in
    # the file menu

    menu = make_menu()
    cursor = MenuCursor(menu.get_children()[0].get_children()[0])

    while not cursor.is_done():
      # provide a message saying how to close the menu

      nyx.controller.show_message('Press m or esc to close the menu.', BOLD)
      nyx.curses.draw(_render, height = 1, background = RED)
      _draw_submenu(cursor, 1, 1, selection_left[0])
      cursor.handle_key(nyx.curses.key_input())

      # redraws the rest of the interface if we're rendering on it again

      if not cursor.is_done():
        nyx.controller.get_controller().redraw()

  nyx.controller.show_message()


def _draw_submenu(cursor, level, top, left):
  selection_hierarchy = [cursor.get_selection()]

  while selection_hierarchy[-1].parent:
    selection_hierarchy.append(selection_hierarchy[-1].parent)

  selection_hierarchy.reverse()

  # checks if there's nothing to display

  if len(selection_hierarchy) < level + 2:
    return

  # fetches the submenu and selection we're displaying

  submenu = selection_hierarchy[level]
  selection = selection_hierarchy[level + 1]

  # gets the size of the prefix, middle, and suffix columns

  all_label_sets = [(entry.prefix, entry.label, entry.suffix) for entry in submenu.get_children()]
  prefix_col_size = max([len(entry[0]) for entry in all_label_sets])
  middle_col_size = max([len(entry[1]) for entry in all_label_sets])
  suffix_col_size = max([len(entry[2]) for entry in all_label_sets])

  # formatted string so we can display aligned menu entries

  label_format = ' %%-%is%%-%is%%-%is ' % (prefix_col_size, middle_col_size, suffix_col_size)
  menu_width = len(label_format % ('', '', ''))
  selection_top = submenu.get_children().index(selection) if selection in submenu.get_children() else 0

  def _render(subwindow):
    for y, menu_item in enumerate(submenu.get_children()):
      if menu_item == selection:
        subwindow.addstr(0, y, label_format % (menu_item.prefix, menu_item.label, menu_item.suffix), WHITE, BOLD)
      else:
        subwindow.addstr(0, y, label_format % (menu_item.prefix, menu_item.label, menu_item.suffix))

  with nyx.curses.CURSES_LOCK:
    nyx.curses.draw(_render, top = top, left = left, width = menu_width, height = len(submenu.get_children()), background = RED)
    _draw_submenu(cursor, level + 1, top + selection_top, left + menu_width)
