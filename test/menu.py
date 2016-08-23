"""
Unit tests for nyx.menu.
"""

import unittest

from nyx.menu import MenuItem, Submenu, RadioMenuItem, RadioGroup


class Container(object):
  value = False

  def __nonzero__(self):
    return self.value


def action(*args):
  IS_CALLED.value = True


NO_OP = lambda: None
IS_CALLED = Container()


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

    self.assertFalse(IS_CALLED)
    menu_item.select()
    self.assertTrue(IS_CALLED)

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
