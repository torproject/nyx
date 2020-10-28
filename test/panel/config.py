"""
Unit tests for nyx.panel.config.
"""

import unittest

import nyx.panel.config
import test

from test import require_curses

try:
  # added in python 3.3
  from unittest.mock import patch
except ImportError:
  from mock import patch

EXPECTED_LINE = 'ControlPort               9051       Port providing access to tor...'

EXPECTED_DETAIL_DIALOG = """
+------------------------------------------------------------------------------+
| ControlPort (General Option)                                                 |
| Value: 9051 (custom, LineList, usage: [address:]port|unix:path|auto [flags]) |
| Description: If set, Tor will accept connections on this port and allow those|
|   connections to control the Tor process using the Tor Control Protocol (des-|
|   cribed in control-spec.txt in torspec). Note: unless you also specify one  |
|   or more of HashedControlPassword or CookieAuthentication, setting this...  |
+------------------------------------------------------------------------------+
""".strip()


class TestConfigPanel(unittest.TestCase):
  @require_curses
  @patch('nyx.panel.config.tor_controller')
  def test_draw_line(self, tor_controller_mock):
    tor_controller_mock().get_info.return_value = True
    tor_controller_mock().get_conf.return_value = ['9051']

    entry = nyx.panel.config.ConfigEntry('ControlPort', 'LineList')

    rendered = test.render(nyx.panel.config._draw_line, 0, 0, entry, False, 10, 35)
    self.assertEqual(EXPECTED_LINE, rendered.content)

  @require_curses
  @patch('nyx.panel.config.tor_controller')
  def test_draw_selection_details(self, tor_controller_mock):
    tor_controller_mock().get_info.return_value = True
    tor_controller_mock().get_conf.return_value = ['9051']

    selected = nyx.panel.config.ConfigEntry('ControlPort', 'LineList')

    rendered = test.render(nyx.panel.config._draw_selection_details, selected)
    self.assertEqual(EXPECTED_DETAIL_DIALOG, rendered.content)
