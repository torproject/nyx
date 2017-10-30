"""
Unit tests for nyx.panel.connection.
"""

import datetime
import unittest

import stem.exit_policy
import stem.version
import nyx.panel.connection
import test

from nyx.tracker import Connection
from nyx.panel.connection import Category, LineType, Line, Entry
from test import require_curses

try:
  # added in python 3.3
  from unittest.mock import Mock, patch
except ImportError:
  from mock import Mock, patch

TIMESTAMP = 1468170303.7
CONNECTION = Connection(TIMESTAMP, False, '127.0.0.1', 3531, '75.119.206.243', 22, 'tcp', False)

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


class MockCircuit(object):
  def __init__(self, circ_id = 7, status = 'BUILT', purpose = 'GENERAL', path = None):
    self.id = circ_id
    self.status = status
    self.purpose = purpose

    if path:
      self.path = path
    else:
      self.path = [
        ('1F43EE37A0670301AD9CB555D94AFEC2C89FDE86', 'Unnamed'),
        ('B6D83EC2D9E18B0A7A33428F8CFA9C536769E209', 'moria1'),
        ('E0BD57A11F00041A9789577C53A1B784473669E4', 'caerSidi'),
      ]


def line(entry = MockEntry(), line_type = LineType.CONNECTION, connection = CONNECTION, circ = MockCircuit(), fingerprint = '1F43EE37A0670301AD9CB555D94AFEC2C89FDE86', nickname = 'Unnamed', locale = 'de'):
  return Line(entry, line_type, connection, circ, fingerprint, nickname, locale)


class TestConnectionPanel(unittest.TestCase):
  @require_curses
  def test_draw_title(self):
    rendered = test.render(nyx.panel.connection._draw_title, [], True)
    self.assertEqual('Connection Details:', rendered.content)

    rendered = test.render(nyx.panel.connection._draw_title, [], False)
    self.assertEqual('Connections:', rendered.content)

    entries = [MockEntry(entry_type = category) for category in (Category.INBOUND, Category.INBOUND, Category.OUTBOUND, Category.INBOUND, Category.CONTROL)]

    rendered = test.render(nyx.panel.connection._draw_title, entries, False)
    self.assertEqual('Connections (3 inbound, 1 outbound, 1 control):', rendered.content)

  @require_curses
  def test_draw_details_incomplete_circuit(self):
    selected = line(line_type = LineType.CIRCUIT_HEADER, circ = MockCircuit(status = 'EXTENDING'))

    rendered = test.render(nyx.panel.connection._draw_details, selected)
    self.assertEqual(DETAILS_BUILDING_CIRCUIT, rendered.content)

  @require_curses
  @patch('nyx.tracker.get_consensus_tracker')
  def test_draw_details_no_consensus_data(self, consensus_tracker_mock):
    consensus_tracker_mock().get_relay_fingerprints.return_value = None

    rendered = test.render(nyx.panel.connection._draw_details, line())
    self.assertEqual(DETAILS_NO_CONSENSUS_DATA, rendered.content)

  @require_curses
  @patch('nyx.tracker.get_consensus_tracker')
  def test_draw_details_when_private(self, consensus_tracker_mock):
    consensus_tracker_mock().get_relay_fingerprints.return_value = None
    selected = line(entry = MockEntry(is_private = True))

    rendered = test.render(nyx.panel.connection._draw_details, selected)
    self.assertEqual(DETAILS_WHEN_PRIVATE, rendered.content)

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

    rendered = test.render(nyx.panel.connection._draw_details, line())
    self.assertEqual(DETAILS_FOR_RELAY, rendered.content)

  @require_curses
  @patch('nyx.tracker.get_consensus_tracker')
  def test_draw_details_with_multiple_matches(self, consensus_tracker_mock):
    consensus_tracker_mock().get_relay_fingerprints.return_value = {
      52: '1F43EE37A0670301AD9CB555D94AFEC2C89FDE86',
      80: 'B6D83EC2D9E18B0A7A33428F8CFA9C536769E209',
      443: 'E0BD57A11F00041A9789577C53A1B784473669E4',
    }

    rendered = test.render(nyx.panel.connection._draw_details, line())
    self.assertEqual(DETAILS_FOR_MULTIPLE_MATCHES, rendered.content)

  @require_curses
  @patch('nyx.panel.connection.tor_controller')
  def test_draw_line(self, tor_controller_mock):
    tor_controller_mock().is_geoip_unavailable.return_value = False
    tor_controller_mock().get_info.return_value = '82.121.9.9'

    test_data = ((
      line(),
      ' 75.119.206.243:22 (de)  -->  82.121.9.9:3531                  15.4s (INBOUND)',
    ), (
      line(entry = MockEntry(entry_type = Category.CIRCUIT), line_type = LineType.CIRCUIT_HEADER),
      ' 82.121.9.9             -->  75.119.206.243:22 (de)            15.4s (CIRCUIT)',
    ), (
      line(line_type = LineType.CIRCUIT, fingerprint = '1F43EE37A0670301AD9CB555D94AFEC2C89FDE86'),
      ' |  82.121.9.9                                                    1 / Guard',
    ), (
      line(line_type = LineType.CIRCUIT, fingerprint = 'B6D83EC2D9E18B0A7A33428F8CFA9C536769E209'),
      ' |  82.121.9.9                                                    2 / Middle',
    ), (
      line(line_type = LineType.CIRCUIT, fingerprint = 'E0BD57A11F00041A9789577C53A1B784473669E4'),
      ' +- 82.121.9.9                                                    3 / End',
    ))

    for test_line, expected in test_data:
      rendered = test.render(nyx.panel.connection._draw_line, 0, 0, test_line, False, 80, TIMESTAMP + 15.4)
      self.assertEqual(expected, rendered.content)

  @require_curses
  @patch('nyx.panel.connection.tor_controller')
  def test_draw_address_column(self, tor_controller_mock):
    tor_controller_mock().is_geoip_unavailable.return_value = False
    tor_controller_mock().get_info.return_value = '82.121.9.9'

    test_data = ((
      line(),
      '75.119.206.243:22 (de)  -->  82.121.9.9:3531',
    ), (
      line(entry = MockEntry(entry_type = Category.EXIT)),
      '82.121.9.9:3531        -->  75.119.206.243:22 (SSH)',
    ), (
      line(line_type = LineType.CIRCUIT_HEADER, circ = MockCircuit(status = 'EXTENDING')),
      'Building...            -->  82.121.9.9',
    ), (
      line(line_type = LineType.CIRCUIT),
      '82.121.9.9',
    ))

    for test_line, expected in test_data:
      rendered = test.render(nyx.panel.connection._draw_address_column, 0, 0, test_line, ())
      self.assertEqual(expected, rendered.content)

  @require_curses
  @patch('nyx.tracker.get_port_usage_tracker')
  def test_draw_line_details(self, port_usage_tracker_mock):
    process = Mock()
    process.name = 'firefox'
    process.pid = 722

    port_usage_tracker_mock().fetch.return_value = process

    test_data = ((
      line(),
      '1F43EE37A0670301AD9CB555D94AFEC2C89FDE86  Unnamed',
    ), (
      line(line_type = LineType.CIRCUIT_HEADER),
      'Purpose: General, Circuit ID: 7',
    ), (
      line(entry = MockEntry(entry_type = Category.CONTROL)),
      'firefox (722)',
    ))

    for test_line, expected in test_data:
      rendered = test.render(nyx.panel.connection._draw_line_details, 0, 0, test_line, 80, ())
      self.assertEqual(expected, rendered.content)

  @require_curses
  def test_draw_right_column(self):
    rendered = test.render(nyx.panel.connection._draw_right_column, 0, 0, line(), TIMESTAMP + 62, ())
    self.assertEqual('  1.0m (INBOUND)', rendered.content)

    legacy_connection = Connection(TIMESTAMP, True, '127.0.0.1', 3531, '75.119.206.243', 22, 'tcp', False)
    test_line = line(entry = MockEntry(entry_type = Category.CONTROL), connection = legacy_connection)

    rendered = test.render(nyx.panel.connection._draw_right_column, 0, 0, test_line, TIMESTAMP + 68, ())
    self.assertEqual('+ 1.1m (CONTROL)', rendered.content)

    test_data = {
      '1F43EE37A0670301AD9CB555D94AFEC2C89FDE86': '    1 / Guard',
      'B6D83EC2D9E18B0A7A33428F8CFA9C536769E209': '    2 / Middle',
      'E0BD57A11F00041A9789577C53A1B784473669E4': '    3 / End',
    }

    for fp, expected in test_data.items():
      test_line = line(line_type = LineType.CIRCUIT, fingerprint = fp)

      rendered = test.render(nyx.panel.connection._draw_right_column, 0, 0, test_line, TIMESTAMP + 62, ())
      self.assertEqual(expected, rendered.content)
