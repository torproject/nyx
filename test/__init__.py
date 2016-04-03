"""
Unit tests for nyx.
"""

import collections
import time
import unittest

import nyx.curses

from nyx import expand_path, join, uses_settings

from mock import patch, Mock

__all__ = [
  'arguments',
  'expand_path',
  'installation',
  'log',
  'tracker',
]

# If set we make test content we render for this many seconds.

SHOW_RENDERED_CONTENT = None

RenderResult = collections.namedtuple('RenderResult', ['content', 'return_value', 'runtime'])


def render(func, *args, **kwargs):
  """
  Runs the given curses function, providing content that's rendered on the
  screen.

  :param function func: draw function to be invoked

  :returns: :data:`~test.RenderResult` with information about what was rendered
  """

  attr = {}

  def draw_func():
    nyx.curses.disable_acs()
    nyx.curses.CURSES_SCREEN.erase()

    start_time = time.time()
    attr['return_value'] = func(*args, **kwargs)
    attr['runtime'] = time.time() - start_time
    attr['content'] = nyx.curses.screenshot()

    if SHOW_RENDERED_CONTENT:
      time.sleep(SHOW_RENDERED_CONTENT)

  with patch('nyx.curses.key_input', return_value = Mock()):
    nyx.curses.start(draw_func, transparent_background = True, cursor = False)

  return RenderResult(attr.get('content'), attr.get('return_value'), attr.get('runtime'))


class TestBaseUtil(unittest.TestCase):
  @patch('nyx.tor_controller')
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

  def test_join(self):
    # check our pydoc examples

    self.assertEqual('This is a looooong', join(['This', 'is', 'a', 'looooong', 'message'], size = 18))
    self.assertEqual('This is a', join(['This', 'is', 'a', 'looooong', 'message'], size = 17))
    self.assertEqual('', join(['This', 'is', 'a', 'looooong', 'message'], size = 2))

    # include a joining character

    self.assertEqual('Download: 5 MB, Upload: 3 MB', join(['Download: 5 MB', 'Upload: 3 MB', 'Other: 2 MB'], ', ', 30))
