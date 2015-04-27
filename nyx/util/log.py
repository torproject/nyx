"""
Logging utilities, primiarily short aliases for logging a message at various
runlevels.
"""

import time
import threading

import stem.util.conf
import stem.util.log
import stem.util.system

import nyx.util

try:
  # added in python 3.2
  from functools import lru_cache
except ImportError:
  from stem.util.lru_cache import lru_cache

TOR_RUNLEVELS = ['DEBUG', 'INFO', 'NOTICE', 'WARN', 'ERR']
TIMEZONE_OFFSET = time.altzone if time.localtime()[8] else time.timezone


def days_since(timestamp):
  """
  Provides the number of days since a given unix timestamp, by local time.

  :param int timestamp: unix timestamp to provide time since

  :reutrns: **int** with the number of days since this event
  """

  return int((timestamp - TIMEZONE_OFFSET) / 86400)


@lru_cache()
def condense_runlevels(*events):
  """
  Provides runlevel events with condensed. For example...

    >>> condense_runlevels('DEBUG', 'NOTICE', 'WARN', 'ERR', 'NYX_NOTICE', 'NYX_WARN', 'NYX_ERR', 'BW')
    ['TOR/NYX NOTICE-ERROR', 'DEBUG', 'BW']

  :param list events: event types to be condensed

  :returns: **list** of the input events, with condensed runlevels
  """

  def ranges(runlevels):
    ranges = []

    while runlevels:
      # provides the (start, end) for a contiguous range
      start = end = runlevels[0]

      for r in TOR_RUNLEVELS[TOR_RUNLEVELS.index(start):]:
        if r in runlevels:
          runlevels.remove(r)
          end = r
        else:
          break

      ranges.append((start, end))

    return ranges

  events = list(events)
  tor_runlevels, nyx_runlevels = [], []

  for r in TOR_RUNLEVELS:
    if r in events:
      tor_runlevels.append(r)
      events.remove(r)

    if 'NYX_%s' % r in events:
      nyx_runlevels.append(r)
      events.remove('NYX_%s' % r)

  tor_ranges = ranges(tor_runlevels)
  nyx_ranges = ranges(nyx_runlevels)

  result = []

  for runlevel_range in tor_ranges:
    if runlevel_range[0] == runlevel_range[1]:
      range_label = runlevel_range[0]
    else:
      range_label = '%s-%s' % (runlevel_range[0], runlevel_range[1])

    if runlevel_range in nyx_ranges:
      result.append('TOR/NYX %s' % range_label)
      nyx_ranges.remove(runlevel_range)
    else:
      result.append(range_label)

  for runlevel_range in nyx_ranges:
    if runlevel_range[0] == runlevel_range[1]:
      result.append('NYX %s' % runlevel_range[0])
    else:
      result.append('NYX %s-%s' % (runlevel_range[0], runlevel_range[1]))

  return result + events


@lru_cache()
def _common_log_messages():
  """
  Provides a mapping of message types to its common log messages. These are
  message prefixes unless it starts with an asterisk, in which case it can
  appear anywhere in the message.

  :returns: **dict** of the form {event_type => [msg1, msg2...]}
  """

  nyx_config, messages = stem.util.conf.get_config('nyx'), {}

  for conf_key in nyx_config.keys():
    if conf_key.startswith('dedup.'):
      event_type = conf_key[6:]
      messages[event_type] = nyx_config.get(conf_key, [])

  return messages


class LogGroup(object):
  """
  Thread safe collection of LogEntry instancs, which maintains a certain size
  and supports deduplication.
  """

  def __init__(self, max_size, group_by_day = False):
    self._max_size = max_size
    self._group_by_day = group_by_day
    self._entries = []
    self._lock = threading.RLock()

  def add(self, timestamp, type, message):
    self.add_entry(LogEntry(timestamp, type, message))

  def add_entry(self, entry):
    with self._lock:
      duplicate = None
      our_day = entry.days_since()

      for existing_entry in self._entries:
        if self._group_by_day and our_day != existing_entry.days_since():
          break
        elif entry.is_duplicate_of(existing_entry):
          duplicate = existing_entry
          break

      if duplicate:
        if not duplicate.duplicates:
          duplicate.duplicates = [duplicate]

        duplicate.is_duplicate = True
        entry.duplicates = duplicate.duplicates
        entry.duplicates.insert(0, entry)

      self._entries.insert(0, entry)

      while len(self._entries) > self._max_size:
        self.pop()

  def pop(self):
    with self._lock:
      last_entry = self._entries.pop()

      # By design if the last entry is a duplicate it will also be the last
      # item in its duplicate group.

      if last_entry.is_duplicate:
        last_entry.duplicates.pop()

  def __len__(self):
    with self._lock:
      return len(self._entries)

  def __iter__(self):
    with self._lock:
      for entry in self._entries:
        yield entry


class LogEntry(object):
  """
  Individual tor or nyx log entry.

  **Note:** Tor doesn't include the date in its timestamps so the year
  component may be inaccurate. (:trac:`15607`)

  :var int timestamp: unix timestamp for when the event occured
  :var str type: event type
  :var str message: event's message
  :var str display_message: message annotated with our time and runlevel

  :var bool is_duplicate: true if this matches other messages in the group and
    isn't the first
  :var list duplicates: messages that are identical to thsi one
  """

  def __init__(self, timestamp, type, message):
    self.timestamp = timestamp
    self.type = type
    self.message = message

    entry_time = time.localtime(self.timestamp)
    self.display_message = '%02i:%02i:%02i [%s] %s' % (entry_time[3], entry_time[4], entry_time[5], self.type, self.message)

    self.is_duplicate = False
    self.duplicates = None

  @lru_cache()
  def is_duplicate_of(self, entry):
    """
    Checks if we are a duplicate of the given message or not.

    :returns: **True** if the given log message is a duplicate of us and **False** otherwise
    """

    if self.type != entry.type:
      return False
    elif self.message == entry.message:
      return True

    for common_msg in _common_log_messages().get(self.type, []):
      # if it starts with an asterisk then check the whole message rather
      # than just the start

      if common_msg[0] == '*':
        if common_msg[1:] in self.message and common_msg[1:] in entry.message:
          return True
      else:
        if self.message.startswith(common_msg) and entry.message.startswith(common_msg):
          return True

    return False

  def days_since(self):
    """
    Provides the number of days since this event, by local time.

    :reutrns: **int** with the number of days since this event
    """

    return days_since(self.timestamp)

  def __eq__(self, other):
    if isinstance(other, LogEntry):
      return hash(self) == hash(other)
    else:
      return False

  def __hash__(self):
    return hash(self.display_message)


def trace(msg, **attr):
  _log(stem.util.log.TRACE, msg, **attr)


def debug(msg, **attr):
  _log(stem.util.log.DEBUG, msg, **attr)


def info(msg, **attr):
  _log(stem.util.log.INFO, msg, **attr)


def notice(msg, **attr):
  _log(stem.util.log.NOTICE, msg, **attr)


def warn(msg, **attr):
  _log(stem.util.log.WARN, msg, **attr)


def error(msg, **attr):
  _log(stem.util.log.ERROR, msg, **attr)


def _log(runlevel, message, **attr):
  """
  Logs the given message, formatted with optional attributes.

  :param stem.util.log.Runlevel runlevel: runlevel at which to log the message
  :param str message: message handle to log
  :param dict attr: attributes to format the message with
  """

  stem.util.log.log(runlevel, nyx.util.msg(message, **attr))


def read_tor_log(path, read_limit = None):
  """
  Provides logging messages from a tor log file, from newest to oldest.

  :param str path: logging location to read from
  :param int read_limit: maximum number of lines to read from the file

  :returns: **iterator** for **LogEntry** for the file's contents

  :raises:
    * **ValueError** if the log file has unrecognized content
    * **IOError** if unable to read the file
  """

  start_time = time.time()
  count, isdst = 0, time.localtime().tm_isdst

  for line in stem.util.system.tail(path, read_limit):
    # entries look like:
    # Jul 15 18:29:48.806 [notice] Parsing GEOIP file.

    line_comp = line.split()

    # Checks that we have all the components we expect. This could happen if
    # we're either not parsing a tor log or in weird edge cases (like being
    # out of disk space).

    if len(line_comp) < 4:
      raise ValueError("Log located at %s has a line that doesn't match the format we expect: %s" % (path, line))
    elif len(line_comp[3]) < 3 or line_comp[3][1:-1].upper() not in TOR_RUNLEVELS:
      raise ValueError('Log located at %s has an unrecognized runlevel: %s' % (path, line_comp[3]))

    runlevel = line_comp[3][1:-1].upper()
    msg = ' '.join(line_comp[4:])

    # Pretending the year is 2012 because 2012 is a leap year. We don't know
    # the actual year (#15607) so picking something else risks strptime failing
    # when it reads Feb 29th (#5265).

    try:
      timestamp_str = '2012 ' + ' '.join(line_comp[:3])
      timestamp_str = timestamp_str.split('.', 1)[0]  # drop fractional seconds
      timestamp_comp = list(time.strptime(timestamp_str, '%Y %b %d %H:%M:%S'))
      timestamp_comp[8] = isdst

      timestamp = int(time.mktime(timestamp_comp))  # converts local to unix time
    except ValueError:
      raise ValueError("Log located at %s has a timestamp we don't recognize: %s" % (path, ' '.join(line_comp[:3])))

    count += 1
    yield LogEntry(timestamp, runlevel, msg)

    if 'opening log file' in msg:
      break  # this entry marks the start of this tor instance

  info('panel.log.read_from_log_file', count = count, path = path, read_limit = read_limit if read_limit else 'none', runtime = '%0.3f' % (time.time() - start_time))
