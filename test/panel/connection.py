"""
Unit tests for nyx.panel.connection.
"""

import datetime
import unittest

import stem.exit_policy
import stem.version
import nyx.panel.connection
import test

from stem.util import connection
from nyx.panel.connection import Category, LineType, Line, Entry
from test import require_curses
from mock import Mock, patch

DETAILS_BUILDING_CIRCUIT = """
+------------------------------------------------------------------------------+
| Building Circuit...                                                          |
|                                                                              |
|                                                                              |
|                                                                              |
|                                                                              |
|                                                                              |
|                                                                              |
+------------------------------------------------------------------------------+
""".strip()

DETAILS_NO_CONSENSUS_DATA = """
+------------------------------------------------------------------------------+
| address: 75.119.206.243:22                                                   |
| locale: de                                                                   |
| No consensus data found                                                      |
|                                                                              |
|                                                                              |
|                                                                              |
|                                                                              |
+------------------------------------------------------------------------------+
""".strip()

DETAILS_WHEN_PRIVATE = """
+------------------------------------------------------------------------------+
| address: <scrubbed>:22                                                       |
| locale: ??                                                                   |
| No consensus data found                                                      |
|                                                                              |
|                                                                              |
|                                                                              |
|                                                                              |
+------------------------------------------------------------------------------+
""".strip()

DETAILS_FOR_RELAY = """
+------------------------------------------------------------------------------+
| address: 75.119.206.243:22                                                   |
| locale: de   fingerprint: B6D83EC2D9E18B0A7A33428F8CFA9C536769E209           |
| nickname: caerSidi                  orport: 9051       dirport: 9052         |
| published: 17:15 03/01/2012         os: Debian         version: 0.2.1.30     |
| flags: Fast, HSDir                                                           |
| exit policy: reject 1-65535                                                  |
| contact: spiffy_person@torproject.org                                        |
+------------------------------------------------------------------------------+
""".strip()

DETAILS_FOR_MULTIPLE_MATCHES = """
+------------------------------------------------------------------------------+
| address: 75.119.206.243:22                                                   |
| locale: de                                                                   |
| Multiple matches, possible fingerprints are:                                 |
| 1. or port: 52    fingerprint: 1F43EE37A0670301AD9CB555D94AFEC2C89FDE86      |
| 2. or port: 80    fingerprint: B6D83EC2D9E18B0A7A33428F8CFA9C536769E209      |
| 3. or port: 443   fingerprint: E0BD57A11F00041A9789577C53A1B784473669E4      |
|                                                                              |
+------------------------------------------------------------------------------+
""".strip()


class MockEntry(Entry):
  def __init__(self, lines = [], entry_type = Category.INBOUND, is_private = False):
    self._lines = lines
    self._type = entry_type
    self._is_private = is_private

  def lines(self):
    return self._lines

  def get_type(self):
    return self._type

  def is_private(self):
    return self._is_private


class TestConnectionPanel(unittest.TestCase):
  @require_curses
  def test_draw_title(self):
    self.assertEqual('Connection Details:', test.render(nyx.panel.connection._draw_title, [], True).content)
    self.assertEqual('Connections:', test.render(nyx.panel.connection._draw_title, [], False).content)

    entries = [
      MockEntry(entry_type = Category.INBOUND),
      MockEntry(entry_type = Category.INBOUND),
      MockEntry(entry_type = Category.OUTBOUND),
      MockEntry(entry_type = Category.INBOUND),
      MockEntry(entry_type = Category.CONTROL),
    ]

    self.assertEqual('Connections (3 inbound, 1 outbound, 1 control):', test.render(nyx.panel.connection._draw_title, entries, False).content)

  @require_curses
  def test_draw_details_incomplete_circuit(self):
    circ = Mock()
    circ.status = 'EXTENDING'

    selected = Line(MockEntry(), LineType.CIRCUIT_HEADER, None, circ, None, None, None)
    self.assertEqual(DETAILS_BUILDING_CIRCUIT, test.render(nyx.panel.connection._draw_details, selected).content)

  @require_curses
  @patch('nyx.tracker.get_consensus_tracker')
  def test_draw_details_no_consensus_data(self, consensus_tracker_mock):
    consensus_tracker_mock().get_relay_fingerprints.return_value = None

    selected = Line(MockEntry(), LineType.CONNECTION, connection.Connection('127.0.0.1', 3531, '75.119.206.243', 22, 'tcp', False), None, None, None, 'de')
    self.assertEqual(DETAILS_NO_CONSENSUS_DATA, test.render(nyx.panel.connection._draw_details, selected).content)

  @require_curses
  @patch('nyx.tracker.get_consensus_tracker')
  def test_draw_details_when_private(self, consensus_tracker_mock):
    consensus_tracker_mock().get_relay_fingerprints.return_value = None

    selected = Line(MockEntry(is_private = True), LineType.CONNECTION, connection.Connection('127.0.0.1', 3531, '75.119.206.243', 22, 'tcp', False), None, None, None, 'de')
    self.assertEqual(DETAILS_WHEN_PRIVATE, test.render(nyx.panel.connection._draw_details, selected).content)

  @require_curses
  @patch('nyx.panel.connection.tor_controller')
  @patch('nyx.tracker.get_consensus_tracker')
  def test_draw_details_for_relay(self, consensus_tracker_mock, tor_controller_mock):
    router_status_entry = Mock()
    router_status_entry.or_port = 9051
    router_status_entry.dir_port = 9052
    router_status_entry.nickname = 'caerSidi'
    router_status_entry.flags = ['Fast', 'HSDir']
    router_status_entry.published = datetime.datetime(2012, 3, 1, 17, 15, 27)

    tor_controller_mock().get_network_status.return_value = router_status_entry

    server_descriptor = Mock()
    server_descriptor.exit_policy = stem.exit_policy.ExitPolicy('reject *:*')
    server_descriptor.tor_version = stem.version.Version('0.2.1.30')
    server_descriptor.operating_system = 'Debian'
    server_descriptor.contact = 'spiffy_person@torproject.org'

    tor_controller_mock().get_server_descriptor.return_value = server_descriptor

    consensus_tracker_mock().get_relay_fingerprints.return_value = {
      22: 'B6D83EC2D9E18B0A7A33428F8CFA9C536769E209'
    }

    selected = Line(MockEntry(), LineType.CONNECTION, connection.Connection('127.0.0.1', 3531, '75.119.206.243', 22, 'tcp', False), None, None, None, 'de')
    self.assertEqual(DETAILS_FOR_RELAY, test.render(nyx.panel.connection._draw_details, selected).content)

  @require_curses
  @patch('nyx.tracker.get_consensus_tracker')
  def test_draw_details_with_multiple_matches(self, consensus_tracker_mock):
    consensus_tracker_mock().get_relay_fingerprints.return_value = {
      52: '1F43EE37A0670301AD9CB555D94AFEC2C89FDE86',
      80: 'B6D83EC2D9E18B0A7A33428F8CFA9C536769E209',
      443: 'E0BD57A11F00041A9789577C53A1B784473669E4',
    }

    selected = Line(MockEntry(), LineType.CONNECTION, connection.Connection('127.0.0.1', 3531, '75.119.206.243', 22, 'tcp', False), None, None, None, 'de')
    self.assertEqual(DETAILS_FOR_MULTIPLE_MATCHES, test.render(nyx.panel.connection._draw_details, selected).content)
