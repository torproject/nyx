"""
General purpose utilities for a variety of tasks supporting nyx features and
safely working with curses (hiding some of the gory details).
"""

import os
import sys

import stem.connection
import stem.control
import stem.util.conf
import stem.util.log

from nyx.util import log

__all__ = [
  'log',
  'panel',
  'text_input',
  'tor_config',
  'tracker',
  'ui_tools',
]

TOR_CONTROLLER = None
BASE_DIR = os.path.sep.join(__file__.split(os.path.sep)[:-2])
DATA_DIR = os.path.expanduser('~/.nyx')
TESTING = False

# technically can change but we use this query a *lot* so needs to be cached

stem.control.CACHEABLE_GETINFO_PARAMS = list(stem.control.CACHEABLE_GETINFO_PARAMS) + ['address']

# disable trace level messages about cache hits

stem.control.LOG_CACHE_FETCHES = False

try:
  uses_settings = stem.util.conf.uses_settings('nyx', os.path.join(BASE_DIR, 'config'), lazy_load = False)
except IOError as exc:
  print("Unable to load nyx's internal configurations: %s" % exc)
  sys.exit(1)


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
