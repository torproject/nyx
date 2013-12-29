import unittest

from mock import Mock, patch

from arm.arguments import parse
from arm.util import init_controller

import stem
import stem.connection
import stem.socket


class TestGetController(unittest.TestCase):
  @patch('os.path.exists', Mock(return_value = True))
  @patch('stem.util.system.is_running')
  @patch('stem.control.Controller.from_socket_file', Mock(side_effect = stem.SocketError('failed')))
  @patch('stem.control.Controller.from_port', Mock(side_effect = stem.SocketError('failed')))
  def test_failue_with_the_default_endpoint(self, is_running_mock):
    is_running_mock.return_value = False
    self._assert_init_controller_fails_with([], "Unable to connect to tor. Are you sure it's running?")

    is_running_mock.return_value = True
    self._assert_init_controller_fails_with([], "Unable to connect to tor. Maybe it's running without a ControlPort?")

  @patch('os.path.exists')
  @patch('stem.util.system.is_running', Mock(return_value = True))
  @patch('stem.control.Controller.from_socket_file', Mock(side_effect = stem.SocketError('failed')))
  @patch('stem.control.Controller.from_port', Mock(side_effect = stem.SocketError('failed')))
  def test_failure_with_a_custom_endpoint(self, path_exists_mock):
    path_exists_mock.return_value = True
    self._assert_init_controller_fails_with(['--interface', '80'], "Unable to connect to 127.0.0.1:80: failed")
    self._assert_init_controller_fails_with(['--socket', '/tmp/my_socket'], "Unable to connect to '/tmp/my_socket': failed")

    path_exists_mock.return_value = False
    self._assert_init_controller_fails_with(['--interface', '80'], "Unable to connect to 127.0.0.1:80: failed")
    self._assert_init_controller_fails_with(['--socket', '/tmp/my_socket'], "The socket file you specified (/tmp/my_socket) doesn't exist")

  @patch('os.path.exists', Mock(return_value = False))
  @patch('stem.control.Controller.from_port')
  def test_getting_a_control_port(self, from_port_mock):
    from_port_mock.return_value = 'success'

    self.assertEqual('success', init_controller(parse([])))
    from_port_mock.assert_called_once_with('127.0.0.1', 9051)
    from_port_mock.reset_mock()

    self.assertEqual('success', init_controller(parse(['--interface', '255.0.0.10:80'])))
    from_port_mock.assert_called_once_with('255.0.0.10', 80)

  @patch('os.path.exists', Mock(return_value = True))
  @patch('stem.control.Controller.from_socket_file')
  def test_getting_a_control_socket(self, from_socket_file_mock):
    from_socket_file_mock.return_value = 'success'

    self.assertEqual('success', init_controller(parse([])))
    from_socket_file_mock.assert_called_once_with('/var/run/tor/control')
    from_socket_file_mock.reset_mock()

    self.assertEqual('success', init_controller(parse(['--socket', '/tmp/my_socket'])))
    from_socket_file_mock.assert_called_once_with('/tmp/my_socket')

  def _assert_init_controller_fails_with(self, args, msg):
    try:
      init_controller(parse(args))
      self.fail()
    except ValueError, exc:
      self.assertEqual(msg, str(exc))
