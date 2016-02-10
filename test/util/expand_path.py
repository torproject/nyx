import unittest

from nyx.util import expand_path, uses_settings

from mock import patch, Mock


class TestExpandPath(unittest.TestCase):
  @patch('nyx.util.tor_controller')
  @patch('stem.util.system.cwd', Mock(return_value = '/your_cwd'))
  @uses_settings
  def test_expand_path(self, tor_controller_mock, config):
    tor_controller_mock().get_pid.return_value = 12345
    self.assertEqual('/absolute/path/to/torrc', expand_path('/absolute/path/to/torrc'))
    self.assertEqual('/your_cwd/torrc', expand_path('torrc'))

    config.set('tor.chroot', '/chroot')
    self.assertEqual('/chroot/absolute/path/to/torrc', expand_path('/absolute/path/to/torrc'))
    self.assertEqual('/chroot/your_cwd/torrc', expand_path('torrc'))

    config.set('tor.chroot', None)
