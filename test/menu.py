"""
Unit tests for nyx.menu.
"""

import unittest

from nyx.menu import MenuItem, Submenu, RadioMenuItem, RadioGroup

NO_OP = lambda: None


class TestMenu(unittest.TestCase):
  def test_menu_item_parameters(self):
    menu_item = MenuItem('Test Item', NO_OP)

    self.assertEqual('', menu_item.prefix)
    self.assertEqual('Test Item', menu_item.label)
    self.assertEqual('', menu_item.suffix)
    self.assertEqual(None, menu_item.parent)
    self.assertEqual(menu_item, menu_item.submenu)

  def test_submenu_parameters(self):
    menu_item = Submenu('Test Item')

    self.assertEqual('', menu_item.prefix)
    self.assertEqual('Test Item', menu_item.label)
    self.assertEqual(' >', menu_item.suffix)
    self.assertEqual(None, menu_item.parent)
    self.assertEqual(menu_item, menu_item.submenu)

  def test_radio_menu_item_parameters(self):
    group = RadioGroup(NO_OP, 'selected_item')
    menu_item = RadioMenuItem('Test Item', group, 'selected_item')

    self.assertEqual('[X] ', menu_item.prefix)
    self.assertEqual('Test Item', menu_item.label)
    self.assertEqual('', menu_item.suffix)
    self.assertEqual(None, menu_item.parent)
    self.assertEqual(menu_item, menu_item.submenu)

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
    self.assertEqual(bottom_item, bottom_item.next())
    self.assertEqual(bottom_item, bottom_item.prev())

    self.assertEqual(root_submenu, middle_submenu.parent)
    self.assertEqual(middle_submenu, middle_submenu.submenu)
    self.assertEqual('Middle Item 1', middle_submenu.next().label)
    self.assertEqual('Middle Item 2', middle_submenu.prev().label)
