"""
General purpose utilities for a variety of tasks supporting seth features and
safely working with curses (hiding some of the gory details).
"""

__all__ = [
  'log',
  'panel',
  'text_input',
  'tor_config',
  'tracker',
  'ui_tools',
]

import calendar
import collections
import os
import sys
import time

import stem.connection
import stem.util.conf
import stem.util.system

from seth.util import log

TOR_CONTROLLER = None
BASE_DIR = os.path.sep.join(__file__.split(os.path.sep)[:-2])

StateBandwidth = collections.namedtuple('StateBandwidth', (
  'read_entries',
  'write_entries',
  'last_read_time',
  'last_write_time',
))

try:
  uses_settings = stem.util.conf.uses_settings('seth', os.path.join(BASE_DIR, 'config'), lazy_load = False)
except IOError as exc:
  print "Unable to load seth's internal configurations: %s" % exc
  sys.exit(1)


def tor_controller():
  """
  Singleton for getting our tor controller connection.

  :returns: :class:`~stem.control.Controller` seth is using
  """

  return TOR_CONTROLLER


def init_controller(*args, **kwargs):
  """
  Sets the Controller used by seth. This is a passthrough for Stem's
  :func:`~stem.connection.connect` function.

  :returns: :class:`~stem.control.Controller` seth is using
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
    log.notice('BUG: We attempted to use an undefined string resource (%s)' % message)
    return ''


@uses_settings
def bandwidth_from_state(config):
  """
  Read Tor's state file to determine its recent bandwidth usage. These
  samplings are at fifteen minute granularity, and can only provide results if
  we've been running for at least a day. This provides a named tuple with the
  following...

    * read_entries and write_entries

      List of the average bytes read or written during each fifteen minute
      period, oldest to newest.

    * last_read_time and last_write_time

      Unix timestamp for when the last entry was recorded.

  :returns: **namedtuple** with the state file's bandwidth informaiton

  :raises: **ValueError** if unable to get the bandwidth information from our
    state file
  """

  controller = tor_controller()

  if not controller.is_localhost():
    raise ValueError('we can only prepopulate bandwidth information for a local tor instance')

  start_time = stem.util.system.start_time(controller.get_pid(None))
  uptime = time.time() - start_time if start_time else None

  # Only attempt to prepopulate information if we've been running for a day.
  # Reason is that the state file stores a day's worth of data, and we don't
  # want to prepopulate with information from a prior tor instance.

  if not uptime:
    raise ValueError("unable to determine tor's uptime")
  elif uptime < (24 * 60 * 60):
    raise ValueError("insufficient uptime, tor must've been running for at least a day")

  # read the user's state file in their data directory (usually '~/.tor')

  data_dir = controller.get_conf('DataDirectory', None)

  if not data_dir:
    raise ValueError("unable to determine tor's data directory")

  state_path = os.path.join(config.get('tor.chroot', '') + data_dir, 'state')

  try:
    with open(state_path) as state_file:
      state_content = state_file.readlines()
  except IOError as exc:
    raise ValueError('unable to read the state file at %s, %s' % (state_path, exc))

  # We're interested in two types of entries from our state file...
  #
  # * BWHistory*Values - Comma separated list of bytes we read or wrote
  #   during each fifteen minute period. The last value is an incremental
  #   counter for our current period, so ignoring that.
  #
  # * BWHistory*Ends - When our last sampling was recorded, in UTC.

  attr = {}

  for line in state_content:
    line = line.strip()

    if line.startswith('BWHistoryReadValues '):
      attr['read_entries'] = [int(entry) / 900 for entry in line[20:].split(',')[:-1]]
    elif line.startswith('BWHistoryWriteValues '):
      attr['write_entries'] = [int(entry) / 900 for entry in line[21:].split(',')[:-1]]
    elif line.startswith('BWHistoryReadEnds '):
      attr['last_read_time'] = calendar.timegm(time.strptime(line[18:], '%Y-%m-%d %H:%M:%S')) - 900
    elif line.startswith('BWHistoryWriteEnds '):
      attr['last_write_time'] = calendar.timegm(time.strptime(line[19:], '%Y-%m-%d %H:%M:%S')) - 900

  if len(attr) != 4:
    raise ValueError('bandwidth stats missing from state file')

  return StateBandwidth(**attr)
