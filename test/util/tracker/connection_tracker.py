import time
import unittest

from seth.util.tracker import ConnectionTracker

from stem.util import connection

from mock import Mock, patch

CONNECTION_1 = connection.Connection('127.0.0.1', 3531, '75.119.206.243', 22, 'tcp')
CONNECTION_2 = connection.Connection('127.0.0.1', 1766, '86.59.30.40', 443, 'tcp')
CONNECTION_3 = connection.Connection('127.0.0.1', 1059, '74.125.28.106', 80, 'tcp')


class TestConnectionTracker(unittest.TestCase):
  @patch('seth.util.tracker.tor_controller')
  @patch('seth.util.tracker.connection.get_connections')
  @patch('seth.util.tracker.system', Mock(return_value = Mock()))
  @patch('seth.util.tracker.connection.system_resolvers', Mock(return_value = [connection.Resolver.NETSTAT]))
  def test_fetching_connections(self, get_value_mock, tor_controller_mock):
    tor_controller_mock().get_pid.return_value = 12345
    get_value_mock.return_value = [CONNECTION_1, CONNECTION_2, CONNECTION_3]

    with ConnectionTracker(0.04) as daemon:
      time.sleep(0.01)

      connections = daemon.get_value()

      self.assertEqual(1, daemon.run_counter())
      self.assertEqual([CONNECTION_1, CONNECTION_2, CONNECTION_3], connections)

      get_value_mock.return_value = []  # no connection results
      time.sleep(0.05)
      connections = daemon.get_value()

      self.assertEqual(2, daemon.run_counter())
      self.assertEqual([], connections)

  @patch('seth.util.tracker.tor_controller')
  @patch('seth.util.tracker.connection.get_connections')
  @patch('seth.util.tracker.system', Mock(return_value = Mock()))
  @patch('seth.util.tracker.connection.system_resolvers', Mock(return_value = [connection.Resolver.NETSTAT, connection.Resolver.LSOF]))
  def test_resolver_failover(self, get_value_mock, tor_controller_mock):
    tor_controller_mock().get_pid.return_value = 12345
    get_value_mock.side_effect = IOError()

    with ConnectionTracker(0.01) as daemon:
      time.sleep(0.03)

      self.assertEqual([connection.Resolver.NETSTAT, connection.Resolver.LSOF], daemon._resolvers)
      self.assertEqual([], daemon.get_value())

      time.sleep(0.05)

      self.assertEqual([connection.Resolver.LSOF], daemon._resolvers)
      self.assertEqual([], daemon.get_value())

      time.sleep(0.05)

      self.assertEqual([], daemon._resolvers)
      self.assertEqual([], daemon.get_value())

      # Now make connection resolution work. We still shouldn't provide any
      # results since we stopped looking.

      get_value_mock.return_value = [CONNECTION_1, CONNECTION_2]
      get_value_mock.side_effect = None
      time.sleep(0.05)
      self.assertEqual([], daemon.get_value())

      # Finally, select a custom resolver. This should cause us to query again
      # reguardless of our prior failures.

      daemon.set_custom_resolver(connection.Resolver.NETSTAT)
      time.sleep(0.05)
      self.assertEqual([CONNECTION_1, CONNECTION_2], daemon.get_value())
