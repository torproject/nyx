"""
Unit tests for nyx.popups.
"""

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
