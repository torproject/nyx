# Copyright 2011-2016, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Menu for controlling nyx.
"""

import functools

import nyx.controller
import nyx.curses
import nyx.popups

import stem

from nyx import nyx_interface, tor_controller
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


class MenuCursor(object):
  """
  Tracks selection and movement through the menu.

  :var MenuItem selection: presently selected menu item
  :var bool is_done: **True** if a selection indicates we should close the
    menu, **False** otherwise
  """

  def __init__(self, initial_selection):
    self.selection = initial_selection
    self.is_done = False

  def handle_key(self, key):
    if key.is_selection():
      if isinstance(self.selection, Submenu):
        if self.selection.children:
          self.selection = self.selection.children[0]
      else:
        self.selection.select()
        self.is_done = True
    elif key.match('up'):
      self.selection = self.selection.prev
    elif key.match('down'):
      self.selection = self.selection.next
    elif key.match('left'):
      if self.selection.parent == self.selection.submenu:
        # shift to the previous main submenu

        prev_submenu = self.selection.submenu.prev
        self.selection = prev_submenu.children[0]
      else:
        # go up a submenu level

        self.selection = self.selection.parent
    elif key.match('right'):
      if isinstance(self.selection, Submenu):
        # open submenu (same as making a selection)

        if self.selection.children:
          self.selection = self.selection.children[0]
      else:
        # shift to the next main submenu

        next_submenu = self.selection.submenu.next
        self.selection = next_submenu.children[0]
    elif key.match('esc', 'm'):
      self.is_done = True


def show_menu():
  menu = _make_menu()
  cursor = MenuCursor(menu.children[0].children[0])

  with nyx.curses.CURSES_LOCK:
    nyx.controller.show_message('Press m or esc to close the menu.', BOLD)

    while not cursor.is_done:
      selection_x = _draw_top_menubar(menu, cursor.selection)
      _draw_submenu(cursor.selection, cursor.selection.submenu, 1, selection_x)
      cursor.handle_key(nyx.curses.key_input())
      nyx_interface().redraw()

    nyx.controller.show_message()


def _make_menu():
  """
  Constructs the base menu and all of its contents.
  """

  interface = nyx_interface()

  if not interface.is_paused():
    pause_item = MenuItem('Pause', interface.set_paused, True)
  else:
    pause_item = MenuItem('Unpause', interface.set_paused, False)

  root_menu = Submenu('')

  root_menu.add(Submenu('Actions', [
    MenuItem('Close Menu', None),
    MenuItem('New Identity', interface.header_panel().send_newnym),
    MenuItem('Reset Tor', tor_controller().signal, stem.Signal.RELOAD),
    pause_item,
    MenuItem('Exit', interface.quit),
  ]))

  root_menu.add(_view_menu())

  for panel in interface.get_page_panels():
    submenu = panel.submenu()

    if submenu:
      root_menu.add(submenu)

  root_menu.add(Submenu('Help', [
    MenuItem('Hotkeys', nyx.popups.show_help),
    MenuItem('About', nyx.popups.show_about),
  ]))

  return root_menu


def _view_menu():
  """
  Submenu consisting of...

    [X] <Page 1>
    [ ] <Page 2>
    [ ] etc...
        Color (Submenu)
  """

  interface = nyx_interface()

  view_menu = Submenu('View')
  page_group = RadioGroup(interface.set_page, interface.get_page())

  for i in range(interface.page_count()):
    page_panels = interface.get_page_panels(page_number = i)
    label = ' / '.join([type(panel).__name__.replace('Panel', '') for panel in page_panels])
    view_menu.add(RadioMenuItem(label, page_group, i))

  if nyx.curses.is_color_supported():
    color_group = RadioGroup(nyx.curses.set_color_override, nyx.curses.get_color_override())

    view_menu.add(Submenu('Color', [
      RadioMenuItem('All', color_group, None),
      [RadioMenuItem(str_tools._to_camel_case(opt), color_group, opt) for opt in nyx.curses.Color],
    ]))

  return view_menu


def _draw_top_menubar(menu, selection):
  def _render(subwindow):
    x = 0

    for submenu in menu.children:
      x = subwindow.addstr(x, 0, ' %s ' % submenu.label, BOLD, UNDERLINE if submenu == selection.submenu else NORMAL)
      subwindow.vline(x, 0, 1)
      x += 1

  nyx.curses.draw(_render, height = 1, background = RED)

  selection_index = menu.children.index(selection.submenu)
  return 3 * selection_index + sum([len(entry.label) for entry in menu.children[:selection_index]])


def _draw_submenu(selection, submenu, top, left):
  # find the item from within this submenu that's selected

  submenu_selection = selection

  while submenu_selection.parent != submenu:
    submenu_selection = submenu_selection.parent

  prefix_size = max([len(entry.prefix) for entry in submenu.children])
  middle_size = max([len(entry.label) for entry in submenu.children])
  suffix_size = max([len(entry.suffix) for entry in submenu.children])

  menu_width = prefix_size + middle_size + suffix_size + 2
  label_format = ' %%-%is%%-%is%%-%is ' % (prefix_size, middle_size, suffix_size)

  def _render(subwindow):
    for y, menu_item in enumerate(submenu.children):
      label = label_format % (menu_item.prefix, menu_item.label, menu_item.suffix)
      attr = (WHITE, BOLD) if menu_item == submenu_selection else (NORMAL,)
      subwindow.addstr(0, y, label, *attr)

  nyx.curses.draw(_render, top = top, left = left, width = menu_width, height = len(submenu.children), background = RED)

  if submenu != selection.parent:
    _draw_submenu(selection, submenu_selection, top + submenu.children.index(submenu_selection), left + menu_width)
