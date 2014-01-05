import unittest

from arm.util.tracker import Daemon

from mock import patch


class TestDaemon(unittest.TestCase):
  @patch('arm.util.tracker.tor_controller')
  @patch('arm.util.tracker.system')
  def test_init(self, system_mock, tor_controller_mock):
    # Check that we register ourselves to listen for status changes, and
    # properly retrieve the process' pid and name.

    tor_controller_mock().get_pid.return_value = 12345
    system_mock.get_name_by_pid.return_value = 'local_tor'

    daemon = Daemon(0.05)

    self.assertEqual(0.05, daemon._rate)
    self.assertEqual(12345, daemon._process_pid)
    self.assertEqual('local_tor', daemon._process_name)

    tor_controller_mock().add_status_listener.assert_called_with(daemon._tor_status_listener)
    system_mock.get_name_by_pid.assert_called_with(12345)

  @patch('arm.util.tracker.tor_controller')
  @patch('arm.util.tracker.system')
  def test_init_without_name(self, system_mock, tor_controller_mock):
    # Check when we default to 'tor' if unable to determine the process' name.

    tor_controller_mock().get_pid.return_value = 12345
    system_mock.get_name_by_pid.return_value = None

    daemon = Daemon(0.05)
    self.assertEqual('tor', daemon._process_name)

  @patch('arm.util.tracker.tor_controller')
  @patch('arm.util.tracker.system')
  def test_init_without_pid(self, system_mock, tor_controller_mock):
    # Check when we can't determine tor's pid.

    tor_controller_mock().get_pid.return_value = None

    daemon = Daemon(0.05)
    self.assertEqual(None, daemon._process_pid)
    self.assertEqual('tor', daemon._process_name)
