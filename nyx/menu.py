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

  :var MenuItem next: menu item after this one
  :var MenuItem prev: menu item before this one
  :var Submenu parent: submenu we reside within
  :var Submenu submenu: top-level submenu we reside within
  """

  def __init__(self, label, callback, *args):
    self.label = label
    self.suffix = ''
    self._parent = None

    if args:
      self._callback = functools.partial(callback, *args)
    else:
      self._callback = callback

  @property
  def prefix(self):
    return ''

  @property
  def next(self):
    return self._sibling(1)

  @property
  def prev(self):
    return self._sibling(-1)

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

  def _sibling(self, offset):
    """
    Provides sibling with a given offset from us.
    """

    if not self._parent:
      return None

    my_siblings = self._parent.children

    try:
      my_index = my_siblings.index(self)
      return my_siblings[(my_index + offset) % len(my_siblings)]
    except ValueError:
      # submenus and children should have bidirectional references

      raise ValueError("BUG: The '%s' submenu doesn't contain '%s' (children: '%s')" % (self._parent, self.label, "', '".join(my_siblings)))


class Submenu(MenuItem):
  """
  Menu item that lists other menu options.

  :var list children: menu items this contains
  """

  def __init__(self, label, children = None):
    MenuItem.__init__(self, label, None)
    self.suffix = ' >'
    self.children = []

    if children:
      for child in children:
        if isinstance(child, list):
          self.add(*child)
        else:
          self.add(child)

  def add(self, *menu_items):
    """
    Adds the given menu item to our listing.

    :param list menu_items: menu item to be added

    :raises: **ValueError** if the item is already in a submenu
    """

    for menu_item in menu_items:
      if menu_item.parent:
        raise ValueError("Menu option '%s' already has a parent" % menu_item)

      menu_item._parent = self
      self.children.append(menu_item)


class RadioMenuItem(MenuItem):
  """
  Menu item with an associated group which determines the selection.
  """

  def __init__(self, label, group, arg):
    MenuItem.__init__(self, label, lambda: group.action(arg))
    self._group = group
    self._arg = arg

  @property
  def prefix(self):
    return '[X] ' if self._arg == self._group.selected_arg else '[ ] '


class RadioGroup(object):
  """
  Radio button groups that RadioMenuItems can belong to.
  """

  def __init__(self, action, selected_arg):
    self.action = lambda arg: action(arg) if arg != self.selected_arg else None
    self.selected_arg = selected_arg


def make_menu():
  """
  Constructs the base menu and all of its contents.
  """

  nyx_controller = nyx.controller.get_controller()

  if not nyx_controller.is_paused():
    pause_item = MenuItem('Pause', nyx_controller.set_paused, True)
  else:
    pause_item = MenuItem('Unpause', nyx_controller.set_paused, False)

  root_menu = Submenu('')

  root_menu.add(Submenu('Actions', [
    MenuItem('Close Menu', None),
    MenuItem('New Identity', nyx_controller.header_panel().send_newnym),
    MenuItem('Reset Tor', tor_controller().signal, stem.Signal.RELOAD),
    pause_item,
    MenuItem('Exit', nyx_controller.quit),
  ]))

  root_menu.add(_view_menu())

  for page_panel in nyx_controller.get_display_panels():
    if isinstance(page_panel, nyx.panel.graph.GraphPanel):
      root_menu.add(_graph_menu(page_panel))
    elif isinstance(page_panel, nyx.panel.log.LogPanel):
      root_menu.add(_log_menu(page_panel))
    elif isinstance(page_panel, nyx.panel.connection.ConnectionPanel):
      root_menu.add(_connections_menu(page_panel))
    elif isinstance(page_panel, nyx.panel.config.ConfigPanel):
      root_menu.add(_configuration_menu(page_panel))
    elif isinstance(page_panel, nyx.panel.torrc.TorrcPanel):
      root_menu.add(_torrc_menu(page_panel))

  root_menu.add(Submenu('Help', [
    MenuItem('Hotkeys', nyx.popups.show_help),
    MenuItem('About', nyx.popups.show_about),
  ]))

  return root_menu


def _view_menu():
  """
  View submenu consisting of...

    [X] <Page 1>
    [ ] <Page 2>
    [ ] etc...
        Color (Submenu)
  """

  nyx_controller = nyx.controller.get_controller()

  view_menu = Submenu('View')
  page_group = RadioGroup(nyx_controller.set_page, nyx_controller.get_page())

  for i in range(nyx_controller.get_page_count()):
    page_panels = nyx_controller.get_display_panels(page_number = i)
    label = ' / '.join([type(panel).__name__.replace('Panel', '') for panel in page_panels])
    view_menu.add(RadioMenuItem(label, page_group, i))

  if nyx.curses.is_color_supported():
    color_group = RadioGroup(nyx.curses.set_color_override, nyx.curses.get_color_override())

    view_menu.add(Submenu('Color', [
      RadioMenuItem('All', color_group, None),
      [RadioMenuItem(str_tools._to_camel_case(opt), color_group, opt) for opt in nyx.curses.Color],
    ]))

  return view_menu


def _graph_menu(graph_panel):
  """
  Graph panel submenu consisting of...

    [X] <Stat 1>
    [ ] <Stat 2>
    [ ] <Stat 2>
        Resize...
        Interval (Submenu)
        Bounds (Submenu)
  """

  stat_group = RadioGroup(functools.partial(setattr, graph_panel, 'displayed_stat'), graph_panel.displayed_stat)
  interval_group = RadioGroup(functools.partial(setattr, graph_panel, 'update_interval'), graph_panel.update_interval)
  bounds_group = RadioGroup(functools.partial(setattr, graph_panel, 'bounds_type'), graph_panel.bounds_type)

  return Submenu('Graph', [
    RadioMenuItem('None', stat_group, None),
    [RadioMenuItem(str_tools._to_camel_case(opt, divider = ' '), stat_group, opt) for opt in sorted(graph_panel.stat_options())],
    MenuItem('Resize...', graph_panel.resize_graph),
    Submenu('Interval', [RadioMenuItem(opt, interval_group, opt) for opt in nyx.panel.graph.Interval]),
    Submenu('Bounds', [RadioMenuItem(opt, bounds_group, opt) for opt in nyx.panel.graph.Bounds]),
  ])


def _log_menu(log_panel):
  """
  Log panel submenu consisting of...

    Events...
    Snapshot...
    Clear
    Show / Hide Duplicates
    Filter (Submenu)
  """

  log_filter = log_panel.get_filter()
  filter_group = RadioGroup(log_filter.select, log_filter.selection())

  if not log_panel.is_duplicates_visible():
    duplicate_item = MenuItem('Show Duplicates', log_panel.set_duplicate_visability, True)
  else:
    duplicate_item = MenuItem('Hide Duplicates', log_panel.set_duplicate_visability, False)

  return Submenu('Log', [
    MenuItem('Events...', log_panel.show_event_selection_prompt),
    MenuItem('Snapshot...', log_panel.show_snapshot_prompt),
    MenuItem('Clear', log_panel.clear),
    duplicate_item,
    Submenu('Filter', [
      RadioMenuItem('None', filter_group, None),
      [RadioMenuItem(opt, filter_group, opt) for opt in log_filter.latest_selections()],
      MenuItem('New...', log_panel.show_filter_prompt),
    ]),
  ])


def _connections_menu(conn_panel):
  """
  Connection panel submenu consisting of...

    Sorting...
    Resolver (Submenu)
  """

  tracker = nyx.tracker.get_connection_tracker()
  resolver_group = RadioGroup(tracker.set_custom_resolver, tracker.get_custom_resolver())

  return Submenu('Connections', [
    MenuItem('Sorting...', conn_panel.show_sort_dialog),
    Submenu('Resolver', [
      RadioMenuItem('auto', resolver_group, None),
      [RadioMenuItem(opt, resolver_group, opt) for opt in stem.util.connection.Resolver],
    ]),
  ])


def _configuration_menu(config_panel):
  """
  Configuration panel submenu consisting of...

    Save Config...
    Sorting...
    Filter / Unfilter Options
  """

  return Submenu('Configuration', [
    MenuItem('Save Config...', config_panel.show_write_dialog),
    MenuItem('Sorting...', config_panel.show_sort_dialog),
  ])


def _torrc_menu(torrc_panel):
  """
  Torrc panel submenu consisting of...

    Reload
    Show / Hide Comments
    Show / Hide Line Numbers
  """

  if not torrc_panel._show_comments:
    comments_item = MenuItem('Show Comments', torrc_panel.set_comments_visible, True)
  else:
    comments_item = MenuItem('Hide Comments', torrc_panel.set_comments_visible, False)

  if not torrc_panel._show_line_numbers:
    line_number_item = MenuItem('Show Line Numbers', torrc_panel.set_line_number_visible, True)
  else:
    line_number_item = MenuItem('Hide Line Numbers', torrc_panel.set_line_number_visible, False)

  return Submenu('Torrc', [
    comments_item,
    line_number_item,
  ])


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
        if self._selection.children:
          self._selection = self._selection.children[0]
      else:
        self._selection.select()
        self._is_done = True
    elif key.match('up'):
      self._selection = self._selection.prev
    elif key.match('down'):
      self._selection = self._selection.next
    elif key.match('left'):
      if self._selection.parent == self._selection.submenu:
        # shift to the previous main submenu

        prev_submenu = self._selection.submenu.prev
        self._selection = prev_submenu.children[0]
      else:
        # go up a submenu level

        self._selection = self._selection.parent
    elif key.match('right'):
      if is_selection_submenu:
        # open submenu (same as making a selection)

        if self._selection.children:
          self._selection = self._selection.children[0]
      else:
        # shift to the next main submenu

        next_submenu = self._selection.submenu.next
        self._selection = next_submenu.children[0]
    elif key.match('esc', 'm'):
      self._is_done = True


def show_menu():
  selection_left = [0]

  def _render(subwindow):
    x = 0

    for top_level_item in menu.children:
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
    cursor = MenuCursor(menu.children[0].children[0])

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

  all_label_sets = [(entry.prefix, entry.label, entry.suffix) for entry in submenu.children]
  prefix_col_size = max([len(entry[0]) for entry in all_label_sets])
  middle_col_size = max([len(entry[1]) for entry in all_label_sets])
  suffix_col_size = max([len(entry[2]) for entry in all_label_sets])

  # formatted string so we can display aligned menu entries

  label_format = ' %%-%is%%-%is%%-%is ' % (prefix_col_size, middle_col_size, suffix_col_size)
  menu_width = len(label_format % ('', '', ''))
  selection_top = submenu.children.index(selection) if selection in submenu.children else 0

  def _render(subwindow):
    for y, menu_item in enumerate(submenu.children):
      if menu_item == selection:
        subwindow.addstr(0, y, label_format % (menu_item.prefix, menu_item.label, menu_item.suffix), WHITE, BOLD)
      else:
        subwindow.addstr(0, y, label_format % (menu_item.prefix, menu_item.label, menu_item.suffix))

  with nyx.curses.CURSES_LOCK:
    nyx.curses.draw(_render, top = top, left = left, width = menu_width, height = len(submenu.children), background = RED)
    _draw_submenu(cursor, level + 1, top + selection_top, left + menu_width)
