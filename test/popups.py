"""
Unit tests for nyx.popups.
"""

import curses
import unittest

import nyx.panel
import nyx.popups
import test

from mock import patch, Mock

EXPECTED_HELP_POPUP = """
Page 1 Commands:---------------------------------------------------------------+
| arrows: scroll up and down             a: save snapshot of the log           |
| e: change logged events                f: log regex filter (disabled)        |
| u: duplicate log entries (hidden)      c: clear event log                    |
| r: resize graph                        s: graphed stats (bandwidth)          |
| b: graph bounds (local max)            i: graph update interval (each second)|
|                                                                              |
| Press any key...                                                             |
+------------------------------------------------------------------------------+
""".strip()

EXPECTED_ABOUT_POPUP = """
About:-------------------------------------------------------------------------+
| Nyx, version 1.4.6-dev (released April 28, 2011)                             |
|   Written by Damian Johnson (atagar@torproject.org)                          |
|   Project page: http://www.atagar.com/arm/                                   |
|                                                                              |
| Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)             |
|                                                                              |
| Press any key...                                                             |
+------------------------------------------------------------------------------+
""".strip()

EXPECTED_EMPTY_COUNTS = """
Client Locales---------------------------------------+
| Usage stats aren't available yet, press any key... |
+----------------------------------------------------+
""".strip()

EXPECTED_COUNTS = """
Client Locales-----------------------------------------------------------------+
| de  41 (43%) ***************************                                     |
| ru  32 (33%) *********************                                           |
| ca  11 (11%) *******                                                         |
| us   6 (6 %) ****                                                            |
| fr   5 (5 %) ***                                                             |
|                                                                              |
| Press any key...                                                             |
+------------------------------------------------------------------------------+
""".strip()

EXPECTED_SORT_DIALOG_START = """
Config Option Ordering:--------------------------------------------------------+
| Current Order: Man Page Entry, Name, Is Set                                  |
| New Order:                                                                   |
|                                                                              |
| Name               Value              Value Type         Category            |
| Usage              Summary            Description        Man Page Entry      |
| Is Set             Cancel                                                    |
|                                                                              |
+------------------------------------------------------------------------------+
""".strip()

EXPECTED_SORT_DIALOG_END = """
Config Option Ordering:--------------------------------------------------------+
| Current Order: Man Page Entry, Name, Is Set                                  |
| New Order: Name, Summary                                                     |
|                                                                              |
| Value              Value Type         Category           Usage               |
| Description        Man Page Entry     Is Set             Cancel              |
|                                                                              |
|                                                                              |
+------------------------------------------------------------------------------+
""".strip()


class TestPopups(unittest.TestCase):
  @patch('nyx.controller.get_controller')
  def test_help(self, get_controller_mock):
    header_panel = Mock()

    header_panel.key_handlers.return_value = (
      nyx.panel.KeyHandler('n'),
      nyx.panel.KeyHandler('r'),
    )

    graph_panel = Mock()

    graph_panel.key_handlers.return_value = (
      nyx.panel.KeyHandler('r', 'resize graph'),
      nyx.panel.KeyHandler('s', 'graphed stats', current = 'bandwidth'),
      nyx.panel.KeyHandler('b', 'graph bounds', current = 'local max'),
      nyx.panel.KeyHandler('i', 'graph update interval', current = 'each second'),
    )

    log_panel = Mock()

    log_panel.key_handlers.return_value = (
      nyx.panel.KeyHandler('arrows', 'scroll up and down'),
      nyx.panel.KeyHandler('a', 'save snapshot of the log'),
      nyx.panel.KeyHandler('e', 'change logged events'),
      nyx.panel.KeyHandler('f', 'log regex filter', current = 'disabled'),
      nyx.panel.KeyHandler('u', 'duplicate log entries', current = 'hidden'),
      nyx.panel.KeyHandler('c', 'clear event log'),
    )

    get_controller_mock().header_panel().get_height.return_value = 0
    get_controller_mock().get_display_panels.return_value = [header_panel, graph_panel, log_panel]

    rendered = test.render(nyx.popups.show_help)
    self.assertEqual(EXPECTED_HELP_POPUP, rendered.content)

  @patch('nyx.controller.get_controller')
  def test_about(self, get_controller_mock):
    get_controller_mock().header_panel().get_height.return_value = 0

    rendered = test.render(nyx.popups.show_about)
    self.assertEqual(EXPECTED_ABOUT_POPUP, rendered.content)

  @patch('nyx.controller.get_controller')
  def test_counts_when_empty(self, get_controller_mock):
    get_controller_mock().header_panel().get_height.return_value = 0

    rendered = test.render(nyx.popups.show_counts, 'Client Locales', {})
    self.assertEqual(EXPECTED_EMPTY_COUNTS, rendered.content)

  @patch('nyx.controller.get_controller')
  def test_counts(self, get_controller_mock):
    get_controller_mock().header_panel().get_height.return_value = 0

    clients = {
      'fr': 5,
      'us': 6,
      'ca': 11,
      'ru': 32,
      'de': 41,
    }

    rendered = test.render(nyx.popups.show_counts, 'Client Locales', clients, fill_char = '*')
    self.assertEqual(EXPECTED_COUNTS, rendered.content)

  @patch('nyx.controller.get_controller')
  def test_sort_dialog(self, get_controller_mock):
    get_controller_mock().header_panel().get_height.return_value = 0

    previous_order = ['Man Page Entry', 'Name', 'Is Set']
    options = ['Name', 'Value', 'Value Type', 'Category', 'Usage', 'Summary', 'Description', 'Man Page Entry', 'Is Set']

    rendered = test.render(nyx.popups.show_sort_dialog, 'Config Option Ordering:', options, previous_order, {})
    self.assertEqual(EXPECTED_SORT_DIALOG_START, rendered.content)
    self.assertEqual(None, rendered.return_value)

  @patch('nyx.controller.get_controller')
  def test_sort_dialog_selecting(self, get_controller_mock):
    # Use the dialog to make a selection. At the end we render two options as
    # being selected (rather than three) because the act of selecing the third
    # closed the popup.

    keypresses = [
      nyx.curses.KeyInput(curses.KEY_ENTER),
      nyx.curses.KeyInput(curses.KEY_DOWN),
      nyx.curses.KeyInput(curses.KEY_ENTER),
      nyx.curses.KeyInput(curses.KEY_ENTER),
    ]

    def draw_func():
      with patch('nyx.curses.key_input', side_effect = keypresses):
        return nyx.popups.show_sort_dialog('Config Option Ordering:', options, previous_order, {})

    get_controller_mock().header_panel().get_height.return_value = 0

    previous_order = ['Man Page Entry', 'Name', 'Is Set']
    options = ['Name', 'Value', 'Value Type', 'Category', 'Usage', 'Summary', 'Description', 'Man Page Entry', 'Is Set']

    rendered = test.render(draw_func)
    self.assertEqual(EXPECTED_SORT_DIALOG_END, rendered.content)
    self.assertEqual(['Name', 'Summary', 'Description'], rendered.return_value)
