import time
import unittest

from nyx.tracker import CustomResolver, ConnectionTracker

from stem.util import connection

try:
  # added in python 3.3
  from unittest.mock import Mock, patch
except ImportError:
  from mock import Mock, patch

STEM_CONNECTIONS = [
  connection.Connection('127.0.0.1', 3531, '75.119.206.243', 22, 'tcp', False),
  connection.Connection('127.0.0.1', 1766, '86.59.30.40', 443, 'tcp', False),
  connection.Connection('127.0.0.1', 1059, '74.125.28.106', 80, 'tcp', False)
]


class TestConnectionTracker(unittest.TestCase):
  @patch('nyx.tracker.tor_controller')
  @patch('nyx.tracker.connection.get_connections')
  @patch('nyx.tracker.system', Mock(return_value = Mock()))
  @patch('nyx.tracker.connection.system_resolvers', Mock(return_value = [connection.Resolver.NETSTAT]))
  def test_fetching_connections(self, get_value_mock, tor_controller_mock):
    tor_controller_mock().get_pid.return_value = 12345
    tor_controller_mock().get_conf.return_value = '0'
    get_value_mock.return_value = STEM_CONNECTIONS

    with ConnectionTracker(0.04) as daemon:
      time.sleep(0.01)

      connections = daemon.get_value()

      self.assertEqual(1, daemon.run_counter())
      self.assertEqual([conn.remote_address for conn in STEM_CONNECTIONS], [conn.remote_address for conn in connections])

      get_value_mock.return_value = []  # no connection results
      time.sleep(0.05)
      connections = daemon.get_value()

      self.assertEqual(2, daemon.run_counter())
      self.assertEqual([], connections)

  @patch('nyx.tracker.tor_controller')
  @patch('nyx.tracker.connection.get_connections')
  @patch('nyx.tracker.system', Mock(return_value = Mock()))
  @patch('nyx.tracker.connection.system_resolvers', Mock(return_value = [connection.Resolver.NETSTAT, connection.Resolver.LSOF]))
  def test_resolver_failover(self, get_value_mock, tor_controller_mock):
    tor_controller_mock().get_pid.return_value = 12345
    tor_controller_mock().get_conf.return_value = '0'
    get_value_mock.side_effect = IOError()

    with ConnectionTracker(0.01) as daemon:
      time.sleep(0.015)

      self.assertEqual([connection.Resolver.NETSTAT, connection.Resolver.LSOF, CustomResolver.INFERENCE], daemon._resolvers)
      self.assertEqual([], daemon.get_value())

      time.sleep(0.025)

      self.assertEqual([connection.Resolver.LSOF, CustomResolver.INFERENCE], daemon._resolvers)
      self.assertEqual([], daemon.get_value())

      time.sleep(0.035)

      self.assertEqual([CustomResolver.INFERENCE], daemon._resolvers)
      self.assertEqual([], daemon.get_value())

      # Now make connection resolution work. We still shouldn't provide any
      # results since we stopped looking.

      get_value_mock.return_value = STEM_CONNECTIONS[:2]
      get_value_mock.side_effect = None
      time.sleep(0.05)
      self.assertEqual([], daemon.get_value())

      # Finally, select a custom resolver. This should cause us to query again
      # reguardless of our prior failures.

      daemon.set_custom_resolver(connection.Resolver.NETSTAT)
      time.sleep(0.05)
      self.assertEqual([conn.remote_address for conn in STEM_CONNECTIONS[:2]], [conn.remote_address for conn in daemon.get_value()])

  @patch('nyx.tracker.tor_controller')
  @patch('nyx.tracker.connection.get_connections')
  @patch('nyx.tracker.system', Mock(return_value = Mock()))
  @patch('nyx.tracker.connection.system_resolvers', Mock(return_value = [connection.Resolver.NETSTAT]))
  def test_tracking_uptime(self, get_value_mock, tor_controller_mock):
    tor_controller_mock().get_pid.return_value = 12345
    tor_controller_mock().get_conf.return_value = '0'
    get_value_mock.return_value = [STEM_CONNECTIONS[0]]
    first_start_time = time.time()

    with ConnectionTracker(0.04) as daemon:
      time.sleep(0.01)

      connections = daemon.get_value()
      self.assertEqual(1, len(connections))

      self.assertEqual(STEM_CONNECTIONS[0].remote_address, connections[0].remote_address)
      self.assertTrue(first_start_time <= connections[0].start_time <= time.time())
      self.assertTrue(connections[0].is_legacy)

      second_start_time = time.time()
      get_value_mock.return_value = STEM_CONNECTIONS[:2]
      time.sleep(0.05)

      connections = daemon.get_value()
      self.assertEqual(2, len(connections))

      self.assertEqual(STEM_CONNECTIONS[0].remote_address, connections[0].remote_address)
      self.assertTrue(first_start_time < connections[0].start_time < time.time())
      self.assertTrue(connections[0].is_legacy)

      self.assertEqual(STEM_CONNECTIONS[1].remote_address, connections[1].remote_address)
      self.assertTrue(second_start_time < connections[1].start_time < time.time())
      self.assertFalse(connections[1].is_legacy)
