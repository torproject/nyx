import unittest

from mock import Mock, patch

from arm.util import (
  init_controller,
  authenticate,
)

import stem
import stem.connection
import stem.socket


class TestAuthenticate(unittest.TestCase):
  def test_success(self):
    controller = Mock()

    authenticate(controller, None)
    controller.authenticate.assert_called_with(password = None, chroot_path = '')
    controller.authenticate.reset_mock()

    authenticate(controller, 's3krit!!!', '/my/chroot')
    controller.authenticate.assert_called_with(password = 's3krit!!!', chroot_path = '/my/chroot')

  @patch('getpass.getpass')
  def test_success_with_password_prompt(self, getpass_mock):
    controller = Mock()

    def authenticate_mock(password, **kwargs):
      if password is None:
        raise stem.connection.MissingPassword('no password')
      elif password == 'my_password':
        return None  # success
      else:
        raise ValueError("Unexpected authenticate_mock input: %s" % password)

    controller.authenticate.side_effect = authenticate_mock
    getpass_mock.return_value = 'my_password'

    authenticate(controller, None)
    controller.authenticate.assert_any_call(password = None, chroot_path = '')
    controller.authenticate.assert_any_call(password = 'my_password', chroot_path = '')

  def test_failure(self):
    controller = Mock()

    controller.authenticate.side_effect = stem.connection.IncorrectSocketType('unable to connect to socket')
    controller.get_socket.return_value = stem.socket.ControlPort(connect = False)
    self._assert_authenticate_fails_with(controller, 'Please check in your torrc that 9051 is the ControlPort.')

    controller.get_socket.return_value = stem.socket.ControlSocketFile(connect = False)
    self._assert_authenticate_fails_with(controller, 'Are you sure the interface you specified belongs to')

    controller.authenticate.side_effect = stem.connection.UnrecognizedAuthMethods('unable to connect', ['telepathy'])
    self._assert_authenticate_fails_with(controller, 'Tor is using a type of authentication we do not recognize...\n\n  telepathy')

    controller.authenticate.side_effect = stem.connection.IncorrectPassword('password rejected')
    self._assert_authenticate_fails_with(controller, 'Incorrect password')

    controller.authenticate.side_effect = stem.connection.UnreadableCookieFile('permission denied', '/tmp/my_cookie', False)
    self._assert_authenticate_fails_with(controller, "We were unable to read tor's authentication cookie...\n\n  Path: /tmp/my_cookie\n  Issue: permission denied")

    controller.authenticate.side_effect = stem.connection.OpenAuthRejected('crazy failure')
    self._assert_authenticate_fails_with(controller, 'Unable to authenticate: crazy failure')

  def _assert_authenticate_fails_with(self, controller, msg):
    try:
      init_controller(authenticate(controller, None))
      self.fail()
    except ValueError, exc:
      if not msg in str(exc):
        self.fail("Expected...\n\n%s\n\n... which couldn't be found in...\n\n%s" % (msg, exc))
