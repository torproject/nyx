import time
import unittest

from nyx.util.tracker import Daemon

from mock import Mock, patch


class TestDaemon(unittest.TestCase):
  @patch('nyx.util.tracker.tor_controller')
  @patch('nyx.util.tracker.system')
  def test_init(self, system_mock, tor_controller_mock):
    # Check that we register ourselves to listen for status changes, and
    # properly retrieve the process' pid and name.

    tor_controller_mock().get_pid.return_value = 12345
    system_mock.name_by_pid.return_value = 'local_tor'

    daemon = Daemon(0.05)

    self.assertEqual(0.05, daemon.get_rate())
    self.assertEqual(12345, daemon._process_pid)
    self.assertEqual('local_tor', daemon._process_name)

    tor_controller_mock().add_status_listener.assert_called_with(daemon._tor_status_listener)
    system_mock.name_by_pid.assert_called_with(12345)

  @patch('nyx.util.tracker.tor_controller')
  @patch('nyx.util.tracker.system')
  def test_init_without_name(self, system_mock, tor_controller_mock):
    # Check when we default to 'tor' if unable to determine the process' name.

    tor_controller_mock().get_pid.return_value = 12345
    system_mock.name_by_pid.return_value = None

    daemon = Daemon(0.05)
    self.assertEqual('tor', daemon._process_name)

  @patch('nyx.util.tracker.tor_controller')
  @patch('nyx.util.tracker.system')
  def test_init_without_pid(self, system_mock, tor_controller_mock):
    # Check when we can't determine tor's pid.

    tor_controller_mock().get_pid.return_value = None

    daemon = Daemon(0.05)
    self.assertEqual(None, daemon._process_pid)
    self.assertEqual('tor', daemon._process_name)
    self.assertEqual(0, system_mock.call_count)

  @patch('nyx.util.tracker.tor_controller', Mock(return_value = Mock()))
  @patch('nyx.util.tracker.system', Mock(return_value = Mock()))
  def test_daemon_calls_task(self):
    # Check that our Daemon calls the task method at the given rate.

    with Daemon(0.01) as daemon:
      time.sleep(0.05)
      self.assertTrue(2 < daemon.run_counter())

  @patch('nyx.util.tracker.tor_controller', Mock(return_value = Mock()))
  @patch('nyx.util.tracker.system', Mock(return_value = Mock()))
  def test_pausing_daemon(self):
    # Check that we can pause and unpause daemon.

    with Daemon(0.01) as daemon:
      time.sleep(0.2)
      self.assertTrue(2 < daemon.run_counter())

      daemon.set_paused(True)
      daemon._run_counter = 0
      time.sleep(0.05)
      self.assertEqual(0, daemon.run_counter())

      daemon.set_paused(False)
      time.sleep(0.2)
      self.assertTrue(2 < daemon.run_counter())
