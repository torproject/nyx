# Copyright 2010-2016, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Tor curses monitoring application.

::

  nyx_interface - nyx interface singleton
  tor_controller - tor connection singleton

  init_controller - initializes our connection to tor
  expand_path - expands path with respect to our chroot
  join - joins a series of strings up to a set length
  msg - string from our configuration

  Interface - overall nyx interface
    |- get_page - page we're showing
    |- set_page - sets the page we're showing
    |- page_count - pages within our interface
    |
    |- is_paused - checks if the interface is paused
    +- set_paused - sets paused state
"""

import distutils.spawn
import os
import sys

import stem.connection
import stem.control
import stem.util.conf
import stem.util.log
import stem.util.system

__version__ = '1.4.6-dev'
__release_date__ = 'April 28, 2011'
__author__ = 'Damian Johnson'
__contact__ = 'atagar@torproject.org'
__url__ = 'http://www.atagar.com/arm/'
__license__ = 'GPLv3'

__all__ = [
  'arguments',
  'controller',
  'curses',
  'log',
  'menu',
  'panel',
  'popups',
  'starter',
  'tracker',
]

TOR_CONTROLLER = None
BASE_DIR = os.path.sep.join(__file__.split(os.path.sep)[:-1])
DATA_DIR = os.path.expanduser('~/.nyx')
TESTING = False

# technically can change but we use this query a *lot* so needs to be cached

stem.control.CACHEABLE_GETINFO_PARAMS = list(stem.control.CACHEABLE_GETINFO_PARAMS) + ['address']

# disable trace level messages about cache hits

stem.control.LOG_CACHE_FETCHES = False

try:
  uses_settings = stem.util.conf.uses_settings('nyx', os.path.join(BASE_DIR, 'settings'), lazy_load = False)
except IOError as exc:
  print("Unable to load nyx's internal configurations: %s" % exc)
  sys.exit(1)


def main():
  try:
    import nyx.starter
    nyx.starter.main()
  except ImportError as exc:
    if exc.message == 'No module named stem':
      if distutils.spawn.find_executable('pip') is not None:
        advice = ", try running 'sudo pip install stem'"
      elif distutils.spawn.find_executable('apt-get') is not None:
        advice = ", try running 'sudo apt-get install python-stem'"
      else:
        advice = ', you can find it at https://stem.torproject.org/download.html'

      print('nyx requires stem' + advice)
    elif exc.message == 'No module named curses':
      if distutils.spawn.find_executable('apt-get') is not None:
        advice = ", try running 'sudo apt-get install python-curses'"
      else:
        advice = ''  # not sure what to do for other platforms

      print('nyx requires curses' + advice)
    else:
      print('Unable to start nyx: %s' % exc)

    sys.exit(1)


def nyx_interface():
  """
  Singleton controller for our interface.

  :returns: :class:`~nyx.Interface` controller
  """

  import nyx.controller
  return nyx.controller.get_controller()


def tor_controller():
  """
  Singleton for getting our tor controller connection.

  :returns: :class:`~stem.control.Controller` nyx is using
  """

  return TOR_CONTROLLER


def init_controller(*args, **kwargs):
  """
  Sets the Controller used by nyx. This is a passthrough for Stem's
  :func:`~stem.connection.connect` function.

  :returns: :class:`~stem.control.Controller` nyx is using
  """

  global TOR_CONTROLLER
  TOR_CONTROLLER = stem.connection.connect(*args, **kwargs)
  return TOR_CONTROLLER


@uses_settings
def expand_path(path, config):
  """
  Expands relative paths and include our chroot if one was set.

  :param str path: path to be expanded

  :returns: **str** with the expanded path
  """

  if path is None:
    return None

  try:
    chroot = config.get('tor.chroot', '')
    tor_cwd = stem.util.system.cwd(tor_controller().get_pid(None))
    return chroot + stem.util.system.expand_path(path, tor_cwd)
  except IOError as exc:
    stem.util.log.info('Unable to expand a relative path (%s): %s' % (path, exc))
    return path


def join(entries, joiner = ' ', size = None):
  """
  Joins a series of strings similar to str.join(), but only up to a given size.
  This returns an empty string if none of the entries will fit. For example...

    >>> join(['This', 'is', 'a', 'looooong', 'message'], size = 18)
    'This is a looooong'

    >>> join(['This', 'is', 'a', 'looooong', 'message'], size = 17)
    'This is a'

    >>> join(['This', 'is', 'a', 'looooong', 'message'], size = 2)
    ''

  :param list entries: strings to be joined
  :param str joiner: strings to join the entries with
  :param int size: maximum length the result can be, there's no length
    limitation if **None**

  :returns: **str** of the joined entries up to the given length
  """

  if size is None:
    return joiner.join(entries)

  result = ''

  for entry in entries:
    new_result = joiner.join((result, entry)) if result else entry

    if len(new_result) > size:
      break
    else:
      result = new_result

  return result


@uses_settings
def msg(message, config, **attr):
  """
  Provides the given message.

  :param str message: message handle to log
  :param dict attr: attributes to format the message with

  :returns: **str** that was requested
  """

  try:
    return config.get('msg.%s' % message).format(**attr)
  except:
    msg = 'BUG: We attempted to use an undefined string resource (%s)' % message

    if TESTING:
      raise ValueError(msg)

    stem.util.log.notice(msg)
    return ''


class Interface(object):
  """
  Overall state of the nyx interface.
  """

  def __init__(self):
    self._page = 0
    self._paused = False

  def get_page(self):
    """
    Provides the page we're showing.

    :return: **int** of the page we're showing
    """

    return self._page

  def set_page(self, page_number):
    """
    Sets the selected page.

    :param int page_number: page to be shown

    :raises: **ValueError** if the page_number is invalid
    """

    if page_number < 0 or page_number >= self.page_count():
      raise ValueError('Invalid page number: %i' % page_number)

    if page_number != self._page:
      self._page = page_number
      self._force_redraw = True
      self.header_panel().redraw()

  def page_count(self):
    """
    Provides the number of pages the interface has.

    :returns: **int** number of pages in the interface
    """

    return len(self._page_panels)

  def is_paused(self):
    """
    Checks if the interface is configured to be paused.

    :returns: **True** if the interface is paused, **False** otherwise
    """

    return self._paused

  def set_paused(self, is_pause):
    """
    Pauses or unpauses the interface.

    :param bool is_pause: suspends the interface if **True**, resumes it
      otherwise
    """

    if is_pause != self._paused:
      self._paused = is_pause

      for panel_impl in self.get_all_panels():
        panel_impl.set_paused(is_pause)

      for panel_impl in self.get_display_panels():
        panel_impl.redraw()
