"""
Unit tests for nyx.
"""

import collections
import inspect
import os
import time
import unittest

import nyx.curses

from nyx import expand_path, chroot, join, uses_settings

try:
  # added in python 3.3
  from unittest.mock import Mock, patch
except ImportError:
  from mock import Mock, patch

__all__ = [
  'arguments',
  'curses',
  'installation',
  'log',
  'menu',
  'panel',
  'popups',
  'tracker',
]

NYX_BASE = os.path.sep.join(__file__.split(os.path.sep)[:-2])
OUR_SCREEN_SIZE = None
TEST_SCREEN_SIZE = nyx.curses.Dimensions(80, 25)

RenderResult = collections.namedtuple('RenderResult', ['content', 'return_value', 'runtime'])


def require_curses(func):
  """
  Skips the test unless curses is available with a minimal dimension needed by
  our tests.
  """

  if OUR_SCREEN_SIZE is None:
    def _check_screen_size():
      global OUR_SCREEN_SIZE
      OUR_SCREEN_SIZE = nyx.curses.screen_size()

    nyx.curses.start(_check_screen_size)

  def wrapped(self, *args, **kwargs):
    if OUR_SCREEN_SIZE.width < TEST_SCREEN_SIZE.width:
      self.skipTest("screen isn't wide enough")
    elif OUR_SCREEN_SIZE.height < TEST_SCREEN_SIZE.height:
      self.skipTest("screen isn't tall enough")
    else:
      with patch('nyx.curses.screen_size', Mock(return_value = TEST_SCREEN_SIZE)):
        return func(self, *args, **kwargs)

  return wrapped


class mock_keybindings(object):
  """
  Mocks the given keyboard inputs.
  """

  def __init__(self, *keys):
    self._mock = patch('nyx.curses.key_input', side_effect = [nyx.curses.KeyInput(key) for key in keys])

  def __enter__(self, *args):
    self._mock.__enter__(*args)

  def __exit__(self, *args):
    self._mock.__exit__(*args)


def render(func, *args, **kwargs):
  """
  Runs the given curses function, providing content that's rendered on the
  screen. If the function starts with an argument named 'subwindow' then it's
  provided one through :func:`~nyx.curses.draw`.

  :param function func: draw function to be invoked

  :returns: :data:`~test.RenderResult` with information about what was rendered
  """

  attr = {}

  def draw_func():
    nyx.curses._disable_acs()
    nyx.curses.CURSES_SCREEN.erase()
    start_time = time.time()

    func_args = inspect.getfullargspec(func).args

    if func_args[:1] == ['subwindow'] or func_args[:2] == ['self', 'subwindow']:
      def _draw(subwindow):
        return func(subwindow, *args, **kwargs)

      attr['return_value'] = nyx.curses.draw(_draw)
    else:
      attr['return_value'] = func(*args, **kwargs)

    attr['runtime'] = time.time() - start_time
    attr['content'] = nyx.curses.screenshot()

  with patch('nyx.curses.key_input', return_value = nyx.curses.KeyInput(27)):
    nyx.curses.start(draw_func, transparent_background = True, cursor = False)

  return RenderResult(attr.get('content'), attr.get('return_value'), attr.get('runtime'))


class TestBaseUtil(unittest.TestCase):
  def setUp(self):
    nyx.CHROOT = None

  def tearDown(self):
    nyx.CHROOT = None

  @patch('nyx.chroot', Mock(return_value = ''))
  @patch('nyx.tor_controller', Mock())
  @patch('stem.util.system.cwd', Mock(return_value = '/your_cwd'))
  def test_expand_path(self):
    self.assertEqual('/absolute/path/to/torrc', expand_path('/absolute/path/to/torrc'))
    self.assertEqual('/your_cwd/torrc', expand_path('torrc'))

  @patch('nyx.chroot', Mock(return_value = '/chroot'))
  @patch('nyx.tor_controller', Mock())
  @patch('stem.util.system.cwd', Mock(return_value = '/your_cwd'))
  def test_expand_path_with_chroot(self):
    self.assertEqual('/chroot/absolute/path/to/torrc', expand_path('/absolute/path/to/torrc'))
    self.assertEqual('/chroot/your_cwd/torrc', expand_path('torrc'))

  @patch('platform.system', Mock(return_value = 'Linux'))
  @patch('os.path.exists', Mock(return_value = True))
  @uses_settings
  def test_chroot_uses_config(self, config):
    config.set('tor_chroot', '/chroot/path')
    self.assertEqual('/chroot/path', chroot())
    config.set('tor_chroot', None)

  @patch('platform.system', Mock(return_value = 'Linux'))
  @patch('os.path.exists', Mock(return_value = False))
  @uses_settings
  def test_chroot_requires_path_to_exist(self, config):
    config.set('tor_chroot', '/chroot/path')
    self.assertEqual('', chroot())
    config.set('tor_chroot', None)

  def test_join(self):
    # check our pydoc examples

    self.assertEqual('This is a looooong', join(['This', 'is', 'a', 'looooong', 'message'], size = 18))
    self.assertEqual('This is a', join(['This', 'is', 'a', 'looooong', 'message'], size = 17))
    self.assertEqual('', join(['This', 'is', 'a', 'looooong', 'message'], size = 2))

    # include a joining character

    self.assertEqual('Download: 5 MB, Upload: 3 MB', join(['Download: 5 MB', 'Upload: 3 MB', 'Other: 2 MB'], ', ', 30))
