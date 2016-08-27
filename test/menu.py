"""
Unit tests for nyx.menu.
"""

import curses
import unittest

import nyx.curses

from nyx.menu import MenuItem, Submenu, RadioMenuItem, RadioGroup, MenuCursor


class Container(object):
  value = False

  def __nonzero__(self):
    return bool(self.value)  # for python 2.x

  def __bool__(self):
    return bool(self.value)  # for python 3.x


def action(*args):
  IS_CALLED.value = args if args else True


def menu_cursor(*key_inputs):
  cursor = MenuCursor(INITIAL_SELECTION)

  for key in key_inputs:
    cursor.handle_key(nyx.curses.KeyInput(key))

  return cursor


NO_OP = lambda: None
IS_CALLED = Container()

TEST_MENU = Submenu('Root Submenu', [
  Submenu('Submenu 1', [
    MenuItem('Item 1', action, 'selected 1'),
    MenuItem('Item 2', action, 'selected 2'),
    Submenu('Inner Submenu', [
      MenuItem('Item 3', action, 'selected 3'),
    ]),
    Submenu('Empty Submenu', []),
  ]),
  Submenu('Submenu 2', [
    MenuItem('Item 4', action, 'selected 1'),
    MenuItem('Item 5', action, 'selected 2'),
  ])
])

INITIAL_SELECTION = TEST_MENU.children[0].children[0]


class TestMenuItem(unittest.TestCase):
  def setUp(self):
    IS_CALLED.value = False

  def test_parameters(self):
    menu_item = MenuItem('Test Item', NO_OP)

    self.assertEqual('', menu_item.prefix)
    self.assertEqual('Test Item', menu_item.label)
    self.assertEqual('', menu_item.suffix)

    self.assertEqual(None, menu_item.next)
    self.assertEqual(None, menu_item.prev)
    self.assertEqual(None, menu_item.parent)
    self.assertEqual(menu_item, menu_item.submenu)

  def test_selection(self):
    menu_item = MenuItem('Test Item', action)
    menu_item.select()
    self.assertTrue(IS_CALLED)

  def test_selection_with_value(self):
    menu_item = MenuItem('Test Item', action, 'hi')
    menu_item.select()
    self.assertEqual(('hi',), IS_CALLED.value)

  def test_menu_item_hierarchy(self):
    root_submenu = Submenu('Root Submenu')
    middle_submenu = Submenu('Middle Submenu')

    root_submenu.add(MenuItem('Middle Item 1', NO_OP))
    root_submenu.add(MenuItem('Middle Item 2', NO_OP))
    root_submenu.add(middle_submenu)

    bottom_item = MenuItem('Bottom Item', NO_OP)
    middle_submenu.add(bottom_item)

    self.assertEqual(middle_submenu, bottom_item.parent)
    self.assertEqual(middle_submenu, bottom_item.submenu)
    self.assertEqual(bottom_item, bottom_item.next)
    self.assertEqual(bottom_item, bottom_item.prev)

    self.assertEqual(root_submenu, middle_submenu.parent)
    self.assertEqual(middle_submenu, middle_submenu.submenu)
    self.assertEqual('Middle Item 1', middle_submenu.next.label)
    self.assertEqual('Middle Item 2', middle_submenu.prev.label)


class TestSubmenu(unittest.TestCase):
  def test_parameters(self):
    menu_item = Submenu('Test Item')

    self.assertEqual('', menu_item.prefix)
    self.assertEqual('Test Item', menu_item.label)
    self.assertEqual(' >', menu_item.suffix)

    self.assertEqual(None, menu_item.next)
    self.assertEqual(None, menu_item.prev)
    self.assertEqual(None, menu_item.parent)
    self.assertEqual(menu_item, menu_item.submenu)

    self.assertEqual([], menu_item.children)

    menu_item = Submenu('Test Item', [
      MenuItem('Test Item 1', NO_OP),
      MenuItem('Test Item 2', NO_OP),
    ])

    self.assertEqual(2, len(menu_item.children))

  def test_add(self):
    submenu = Submenu('Menu')
    item_1 = MenuItem('Test Item 1', NO_OP)
    item_2 = MenuItem('Test Item 2', NO_OP)

    self.assertEqual([], submenu.children)

    submenu.add(item_1)
    self.assertEqual([item_1], submenu.children)

    submenu.add(item_2)
    self.assertEqual([item_1, item_2], submenu.children)

  def test_add_raises_when_already_in_menu(self):
    submenu_1 = Submenu('Menu 1')
    submenu_2 = Submenu('Menu 2')
    item = MenuItem('Test Item', NO_OP)

    submenu_1.add(item)
    self.assertRaises(ValueError, submenu_2.add, item)


class TestRadioMenuItem(unittest.TestCase):
  def setUp(self):
    IS_CALLED.value = False

  def test_parameters(self):
    group = RadioGroup(NO_OP, 'selected_item')
    menu_item = RadioMenuItem('Test Item', group, 'selected_item')

    self.assertEqual('[X] ', menu_item.prefix)
    self.assertEqual('Test Item', menu_item.label)
    self.assertEqual('', menu_item.suffix)

    self.assertEqual(None, menu_item.next)
    self.assertEqual(None, menu_item.prev)
    self.assertEqual(None, menu_item.parent)
    self.assertEqual(menu_item, menu_item.submenu)

  def test_selection(self):
    group = RadioGroup(action, 'other_item')
    menu_item = RadioMenuItem('Test Item', group, 'selected_item')

    menu_item.select()
    self.assertTrue(IS_CALLED)

  def test_when_already_selected(self):
    group = RadioGroup(action, 'selected_item')
    menu_item = RadioMenuItem('Test Item', group, 'selected_item')

    menu_item.select()
    self.assertFalse(IS_CALLED)


class TestMenuCursor(unittest.TestCase):
  def setUp(self):
    IS_CALLED.value = False

  def test_selection_of_item(self):
    cursor = menu_cursor(curses.KEY_ENTER)
    self.assertEqual(('selected 1',), IS_CALLED.value)
    self.assertTrue(cursor.is_done)

  def test_selection_of_submenu(self):
    cursor = menu_cursor(curses.KEY_DOWN, curses.KEY_DOWN, curses.KEY_ENTER)
    self.assertEqual('Item 3', cursor.selection.label)
    self.assertFalse(IS_CALLED.value)
    self.assertFalse(cursor.is_done)

  def test_up(self):
    cursor = menu_cursor()

    for expected in ('Item 1', 'Empty Submenu', 'Inner Submenu', 'Item 2', 'Item 1'):
      self.assertEqual(expected, cursor.selection.label)
      cursor.handle_key(nyx.curses.KeyInput(curses.KEY_UP))

  def test_down(self):
    cursor = menu_cursor()

    for expected in ('Item 1', 'Item 2', 'Inner Submenu', 'Empty Submenu', 'Item 1'):
      self.assertEqual(expected, cursor.selection.label)
      cursor.handle_key(nyx.curses.KeyInput(curses.KEY_DOWN))

  def test_left(self):
    cursor = menu_cursor()

    for expected in ('Item 1', 'Item 4', 'Item 1'):
      self.assertEqual(expected, cursor.selection.label)
      cursor.handle_key(nyx.curses.KeyInput(curses.KEY_LEFT))

  def test_left_when_inner_submenu(self):
    cursor = menu_cursor(curses.KEY_DOWN, curses.KEY_DOWN, curses.KEY_RIGHT)

    for expected in ('Item 3', 'Inner Submenu', 'Item 4'):
      self.assertEqual(expected, cursor.selection.label)
      cursor.handle_key(nyx.curses.KeyInput(curses.KEY_LEFT))

  def test_right(self):
    cursor = menu_cursor()

    for expected in ('Item 1', 'Item 4', 'Item 1'):
      self.assertEqual(expected, cursor.selection.label)
      cursor.handle_key(nyx.curses.KeyInput(curses.KEY_RIGHT))

  def test_right_when_inner_submenu(self):
    cursor = menu_cursor(curses.KEY_DOWN, curses.KEY_DOWN)

    for expected in ('Inner Submenu', 'Item 3', 'Item 4', 'Item 1'):
      self.assertEqual(expected, cursor.selection.label)
      cursor.handle_key(nyx.curses.KeyInput(curses.KEY_RIGHT))

  def test_esc(self):
    cursor = menu_cursor(27)
    self.assertTrue(cursor.is_done)

    # pressing 'm' closes the menu too

    cursor = menu_cursor(ord('m'))
    self.assertTrue(cursor.is_done)
