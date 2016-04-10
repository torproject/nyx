"""
Unit tests for nyx.panel.header.
"""

import time
import unittest

import nyx.panel.header
import test

from test import require_curses
from mock import patch


class TestHeader(unittest.TestCase):
  @require_curses
  def test_draw_platform_section(self):
    vals = nyx.panel.header._sampling(
      hostname = 'odin',
      platform = 'Linux 3.5.0-54-generic',
      version = '0.2.8.1-alpha-dev',
      version_status = 'unrecommended',
    )

    test_input = {
      80: 'nyx - odin (Linux 3.5.0-54-generic)        Tor 0.2.8.1-alpha-dev',
      70: 'nyx - odin (Linux 3.5.0-54-generic)        Tor 0.2.8.1-alpha-dev',
      60: 'nyx - odin (Linux 3.5.0-54-generic)        Tor 0.2.8.1-al...',
      50: 'nyx - odin (Linux 3.5.0-54-generic)',
      40: 'nyx - odin (Linux 3.5.0-54-generic)',
      30: 'nyx - odin (Linux 3.5.0-54...)',
      20: 'nyx - odin (Linu...)',
      10: 'nyx - odin',
      0: 'nyx - odin',
    }

    for width, expected in test_input.items():
      self.assertEqual(expected, test.render(nyx.panel.header._draw_platform_section, 0, 0, width, vals).content)

  @require_curses
  def test_draw_platform_section_without_version(self):
    vals = nyx.panel.header._sampling(
      hostname = 'odin',
      platform = 'Linux 3.5.0-54-generic',
      version = 'Unknown',
    )

    rendered = test.render(nyx.panel.header._draw_platform_section, 0, 0, 80, vals)
    self.assertEqual('nyx - odin (Linux 3.5.0-54-generic)', rendered.content)

  @require_curses
  def test_draw_ports_section(self):
    vals = nyx.panel.header._sampling(
      nickname = 'Unnamed',
      address = '174.21.17.28',
      or_port = '7000',
      dir_port = '7001',
      control_port = '9051',
      auth_type = 'cookie',
      is_relay = True,
    )

    test_input = {
      80: 'Unnamed - 174.21.17.28:7000, Dir Port: 7001, Control Port (cookie): 9051',
      50: 'Unnamed - 174.21.17.28:7000, Dir Port: 7001, Control Port: 9051',
      0: 'Unnamed - 174.21.17.28:7000, Dir Port: 7001, Control Port: 9051',
    }

    for width, expected in test_input.items():
      self.assertEqual(expected, test.render(nyx.panel.header._draw_ports_section, 0, 0, width, vals).content)

  @require_curses
  def test_draw_ports_section_with_relaying(self):
    vals = nyx.panel.header._sampling(
      control_port = None,
      socket_path = '/path/to/control/socket',
      is_relay = False,
    )

    self.assertEqual('Relaying Disabled, Control Socket: /path/to/control/socket', test.render(nyx.panel.header._draw_ports_section, 0, 0, 80, vals).content)

  @require_curses
  @patch('time.localtime')
  def test_draw_disconnected(self, localtime_mock):
    localtime_mock.return_value = time.strptime('22:43 04/09/2016', '%H:%M %m/%d/%Y')
    self.assertEqual('Tor Disconnected (22:43 04/09/2016, press r to reconnect)', test.render(nyx.panel.header._draw_disconnected, 0, 0, 1460267022.231895).content)
