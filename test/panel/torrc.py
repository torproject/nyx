"""
Unit tests for nyx.panel.torrc.
"""

import unittest

import nyx.panel.torrc
import test

from test import require_curses
from mock import patch, Mock

TORRC = """
ORPort 9050
ControlPort 9051
Exitpolicy reject *:*  # non-exit relay
CookieAuthentication 1
""".strip()

RENDERED_DEFAULT = """
Tor Configuration File (/path/to/torrc):
1 ORPort 9050
2 ControlPort 9051
3 Exitpolicy reject *:*  # non-exit relay
4 CookieAuthentication 1
""".strip()

RENDERED_WITHOUT_COMMENTS = """
Tor Configuration File (/path/to/torrc):
1 ORPort 9050
2 ControlPort 9051
3 Exitpolicy reject *:*
4 CookieAuthentication 1
""".strip()

RENDERED_WITHOUT_LINE_NUMBERS = """
Tor Configuration File (/path/to/torrc):
ORPort 9050
ControlPort 9051
Exitpolicy reject *:*  # non-exit relay
CookieAuthentication 1
""".strip()

RENDERED_WITH_ERROR = """
Tor Configuration File (/path/to/torrc):
Unable to read our torrc: [Errno 2] No such file or directory: '/path/to/torrc'
""".strip()


class TestGraphPanel(unittest.TestCase):
  @require_curses
  @patch('nyx.panel.torrc._read_torrc', Mock(return_value = TORRC.splitlines()))
  @patch('nyx.panel.torrc.expand_path', Mock(return_value = '/path/to/torrc'))
  @patch('nyx.panel.torrc.tor_controller', Mock())
  def test_draw_with_content(self):
    panel = nyx.panel.torrc.TorrcPanel()
    self.assertEqual(RENDERED_DEFAULT, test.render(panel.draw).content)

  @require_curses
  @patch('nyx.panel.torrc._read_torrc', Mock(return_value = TORRC.splitlines()))
  @patch('nyx.panel.torrc.expand_path', Mock(return_value = '/path/to/torrc'))
  @patch('nyx.panel.torrc.tor_controller', Mock())
  def test_draw_without_comments(self):
    panel = nyx.panel.torrc.TorrcPanel()
    panel._show_comments = False
    self.assertEqual(RENDERED_WITHOUT_COMMENTS, test.render(panel.draw).content)

  @require_curses
  @patch('nyx.panel.torrc._read_torrc', Mock(return_value = TORRC.splitlines()))
  @patch('nyx.panel.torrc.expand_path', Mock(return_value = '/path/to/torrc'))
  @patch('nyx.panel.torrc.tor_controller', Mock())
  def test_draw_without_line_numbers(self):
    panel = nyx.panel.torrc.TorrcPanel()
    panel._show_line_numbers = False
    self.assertEqual(RENDERED_WITHOUT_LINE_NUMBERS, test.render(panel.draw).content)

  @require_curses
  @patch('nyx.panel.torrc._read_torrc', Mock(side_effect = IOError("[Errno 2] No such file or directory: '/path/to/torrc'")))
  @patch('nyx.panel.torrc.expand_path', Mock(return_value = '/path/to/torrc'))
  @patch('nyx.panel.torrc.tor_controller', Mock())
  def test_draw_with_error(self):
    panel = nyx.panel.torrc.TorrcPanel()
    panel._show_line_numbers = False
    self.assertEqual(RENDERED_WITH_ERROR, test.render(panel.draw).content)
