"""
Unit tests for nyx.panel.header.
"""

import time
import unittest

import nyx.panel.header
import stem.exit_policy
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
      0: '',
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

  @require_curses
  def test_draw_resource_usage(self):
    vals = nyx.panel.header._sampling(
      start_time = 1460166022.231895,
      connection_time = 1460267022.231895,
      is_connected = False,
      tor_cpu = '2.1',
      nyx_cpu = '5.4',
      memory = '118 MB',
      memory_percent = '3.0',
      pid = '22439',
    )

    test_input = {
      80: 'cpu: 2.1% tor, 5.4% nyx    mem: 118 MB (3.0%)  pid: 22439  uptime: 1-04:03:20',
      70: 'cpu: 2.1% tor, 5.4% nyx    mem: 118 MB (3.0%)  pid: 22439',
      60: 'cpu: 2.1% tor, 5.4% nyx    mem: 118 MB (3.0%)  pid: 22439',
      50: 'cpu: 2.1% tor, 5.4% nyx    mem: 118 MB (3.0%)',
      40: 'cpu: 2.1% tor, 5.4% nyx',
      30: 'cpu: 2.1% tor, 5.4% nyx',
      20: '',
      10: '',
      0: '',
    }

    for width, expected in test_input.items():
      self.assertEqual(expected, test.render(nyx.panel.header._draw_resource_usage, 0, 0, width, vals, None).content)

  @require_curses
  def test_draw_fingerprint_and_fd_usage(self):
    vals = nyx.panel.header._sampling(
      fingerprint = '1A94D1A794FCB2F8B6CBC179EF8FDD4008A98D3B',
      fd_used = None,
    )

    test_input = {
      80: 'fingerprint: 1A94D1A794FCB2F8B6CBC179EF8FDD4008A98D3B',
      70: 'fingerprint: 1A94D1A794FCB2F8B6CBC179EF8FDD4008A98D3B',
      60: 'fingerprint: 1A94D1A794FCB2F8B6CBC179EF8FDD4008A98D3B',
      50: 'fingerprint: 1A94D1A794FCB2F8B6CBC179EF8FDD4008...',
      40: 'fingerprint: 1A94D1A794FCB2F8B6CBC179...',
      30: 'fingerprint: 1A94D1A794FCB2...',
      20: 'fingerprint: 1A94...',
      10: 'fingerp...',
      0: '',
    }

    for width, expected in test_input.items():
      self.assertEqual(expected, test.render(nyx.panel.header._draw_fingerprint_and_fd_usage, 0, 0, width, vals).content)

  @require_curses
  def test_draw_fingerprint_and_fd_usage_with_fd_count(self):
    test_input = {
      59: 'fingerprint: <stub>',
      60: 'fingerprint: <stub>, file descriptors: 60 / 100 (60%)',
      75: 'fingerprint: <stub>, file descriptors: 75 / 100 (75%)',
      89: 'fingerprint: <stub>, file descriptors: 89 / 100 (89%)',
      90: 'fingerprint: <stub>, file descriptors: 90 / 100 (90%)',
      95: 'fingerprint: <stub>, file descriptors: 95 / 100 (95%)',
      99: 'fingerprint: <stub>, file descriptors: 99 / 100 (99%)',
      100: 'fingerprint: <stub>, file descriptors: 100 / 100 (100%)',
    }

    for fd_used, expected in test_input.items():
      vals = nyx.panel.header._sampling(
        fingerprint = '<stub>',
        fd_used = fd_used,
        fd_limit = 100,
      )

      self.assertEqual(expected, test.render(nyx.panel.header._draw_fingerprint_and_fd_usage, 0, 0, 80, vals).content)

  @require_curses
  def test_draw_flags(self):
    self.assertEqual('flags: none', test.render(nyx.panel.header._draw_flags, 0, 0, []).content)
    self.assertEqual('flags: Guard', test.render(nyx.panel.header._draw_flags, 0, 0, ['Guard']).content)
    self.assertEqual('flags: Running, Exit', test.render(nyx.panel.header._draw_flags, 0, 0, ['Running', 'Exit']).content)

  @require_curses
  def test_draw_exit_policy(self):
    self.assertEqual('exit policy: reject *:*', test.render(nyx.panel.header._draw_exit_policy, 0, 0, stem.exit_policy.ExitPolicy('reject *:*')).content)
    self.assertEqual('exit policy: accept *:80, accept *:443, reject *:*', test.render(nyx.panel.header._draw_exit_policy, 0, 0, stem.exit_policy.ExitPolicy('accept *:80', 'accept *:443', 'reject *:*')).content)

  @require_curses
  def test_draw_newnym_option(self):
    self.assertEqual("press 'n' for a new identity", test.render(nyx.panel.header._draw_newnym_option, 0, 0, 0).content)
    self.assertEqual('building circuits, available again in 1 second', test.render(nyx.panel.header._draw_newnym_option, 0, 0, 1).content)
    self.assertEqual('building circuits, available again in 5 seconds', test.render(nyx.panel.header._draw_newnym_option, 0, 0, 5).content)
