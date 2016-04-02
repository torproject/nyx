"""
Unit tests for nyx.popups.
"""

import unittest

import nyx.popups
import test

from mock import patch

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
  def test_about(self, get_controller_mock):
    get_controller_mock().header_panel().get_height.return_value = 0

    rendered = test.render(nyx.popups.show_about)
    self.assertEqual(EXPECTED_ABOUT_POPUP, rendered.content)
