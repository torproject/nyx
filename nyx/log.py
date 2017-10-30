# Copyright 2014-2017, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Logging utilities, primiarily short aliases for logging a message at various
runlevels.

::

  day_count - number of days since a given timestamp
  log_file_path - path of tor's log file if one is present on disk
  condense_runlevels - condensed displayable listing of log events
  listen_for_events - notifies listener of tor events
  read_tor_log - provides LogEntry from a tor log file

  LogGroup - thread safe, deduplicated grouping of events
    |- add - adds an event to the group
    |- pop - removes and returns an event
    +- clone - deep copy of this LogGroup

  LogEntry - individual log event
    |- is_duplicate_of - checks if a duplicate message of another LogEntry
    |- day_count - number of days since this even occured
    +- clone - deep copy of this LogEntry

  LogFileOutput - writes log events to a file
    +- write - persist a given message

  LogFilters - regex filtering of log events
    |- select - filters by this regex
    |- selection - current regex filter
    |- latest_selections - past regex selections
    |- match - checks if a LogEntry matches this filter
    +- clone - deep copy of this LogFilters
"""

import collections
import datetime
import os
import re
import time
import threading

import stem.util.conf
import stem.util.log
import stem.util.system

import nyx

try:
  # added in python 3.2
  from functools import lru_cache
except ImportError:
  from stem.util.lru_cache import lru_cache

TOR_RUNLEVELS = ['DEBUG', 'INFO', 'NOTICE', 'WARN', 'ERR']
NYX_RUNLEVELS = ['NYX_DEBUG', 'NYX_INFO', 'NYX_NOTICE', 'NYX_WARNING', 'NYX_ERROR']
TIMEZONE_OFFSET = time.altzone if time.localtime()[8] else time.timezone
GROUP_BY_DAY = True


def day_count(timestamp):
  """
  Provoides a unique number for the day a given timestamp falls on, by local
  time. Daybreaks are rolled over at midnight.

  :param int timestamp: unix timestamp to provide a count for

  :reutrns: **int** for the day it falls on
  """

  return int((timestamp - TIMEZONE_OFFSET) / 86400)


def log_file_path(controller):
  """
  Provides the path where tor's log file resides, if one exists.

  :params stem.control.Controller controller: tor controller connection

  :returns: **str** with the absolute path of our log file, or **None** if one
    doesn't exist
  """

  for log_entry in controller.get_conf('Log', [], True):
    entry_comp = log_entry.split()  # looking for an entry like: notice file /var/log/tor/notices.log

    if entry_comp[1] == 'file':
      return nyx.expand_path(entry_comp[2])


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

    if r == 'WARN':
      nyx_runlevel = 'NYX_WARNING'
    elif r == 'ERR':
      nyx_runlevel = 'NYX_ERROR'
    else:
      nyx_runlevel = 'NYX_%s' % r

    if nyx_runlevel in events:
      nyx_runlevels.append(r)
      events.remove(nyx_runlevel)

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


def listen_for_events(listener, events):
  """
  Configures tor to notify a function of these event types. If tor is
  configured to notify this listener then the old listener is replaced.

  :param function listener: listener to be notified
  :param list events: event types to attempt to set

  :returns: **list** of event types we're successfully now listening to
  """

  events = set(events)  # drops duplicates
  nyx_events = events.intersection(set(NYX_RUNLEVELS))
  tor_events = events.difference(nyx_events)

  controller = nyx.tor_controller()
  controller.remove_event_listener(listener)

  for event_type in list(tor_events):
    try:
      controller.add_event_listener(listener, event_type)
    except stem.ProtocolError:
      stem.util.log.warn("%s isn't an event tor supports" % event_type)
      tor_events.remove(event_type)

  return sorted(tor_events.union(nyx_events))


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

  def __init__(self, max_size):
    self._max_size = max_size
    self._entries = []
    self._dedup_map = {}  # dedup key => most recent entry
    self._lock = threading.RLock()

  def add(self, entry):
    with self._lock:
      duplicate = self._dedup_map.get(entry.dedup_key, None)

      if duplicate:
        if not duplicate.duplicates:
          duplicate.duplicates = [duplicate]

        duplicate.is_duplicate = True
        entry.duplicates = duplicate.duplicates
        entry.duplicates.insert(0, entry)

      self._entries.insert(0, entry)
      self._dedup_map[entry.dedup_key] = entry

      while len(self._entries) > self._max_size:
        self.pop()

  def pop(self):
    with self._lock:
      last_entry = self._entries.pop()

      # By design if the last entry is a duplicate it will also be the last
      # item in its duplicate group.

      if last_entry.is_duplicate:
        last_entry.duplicates.pop()

      if self._dedup_map.get(last_entry.dedup_key, None) == last_entry:
        del self._dedup_map[last_entry.dedup_key]

  def clone(self):
    with self._lock:
      copy = LogGroup(self._max_size)
      copy._entries = [entry.clone() for entry in self._entries]
      return copy

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

  :var str dedup_key: key that can be used for deduplication
  :var bool is_duplicate: true if this matches other messages in the group and
    isn't the first
  :var list duplicates: messages that are identical to this one
  """

  def __init__(self, timestamp, type, message):
    self.timestamp = timestamp
    self.type = type
    self.message = message

    entry_time = time.localtime(self.timestamp)
    self.display_message = '%02i:%02i:%02i [%s] %s' % (entry_time[3], entry_time[4], entry_time[5], self.type, self.message)

    self.is_duplicate = False
    self.duplicates = None

    if GROUP_BY_DAY:
      self.dedup_key = '%s:%s:%s' % (self.type, self.day_count(), self._message_dedup_key())
    else:
      self.dedup_key = '%s:%s' % (self.type, self._message_dedup_key())

  def _message_dedup_key(self):
    """
    Provides key we can use for deduplication for the message portion of our entry.

    :returns: **str** key for deduplication purposes
    """

    if self.type == 'NYX_DEBUG' and 'runtime:' in self.message:
      # most nyx debug messages show runtimes so try matching without that
      return self.message[:self.message.find('runtime:')]

    for common_msg in _common_log_messages().get(self.type, []):
      # if it starts with an asterisk then check the whole message rather
      # than just the start

      if common_msg[0] == '*':
        if common_msg[1:] in self.message:
          return common_msg
      else:
        if self.message.startswith(common_msg):
          return common_msg

    return self.message

  def day_count(self):
    """
    Provides the day this event occured on by local time.

    :reutrns: **int** with the day this occured on
    """

    return day_count(self.timestamp)

  def clone(self):
    copy = LogEntry(self.timestamp, self.type, self.message)
    copy.is_duplicate = self.is_duplicate
    copy.duplicates = None if self.duplicates is None else list(self.duplicates)

    return copy

  def __eq__(self, other):
    if isinstance(other, LogEntry):
      return hash(self) == hash(other)
    else:
      return False

  def __hash__(self):
    return hash(self.display_message)


class LogFileOutput(object):
  """
  File where log messages we receive are written. If unable to do so then a
  notification is logged and further write attempts are skipped.
  """

  def __init__(self, path):
    self._file = None

    if path:
      try:
        path_dir = os.path.dirname(path)

        if not os.path.exists(path_dir):
          os.makedirs(path_dir)

        self._file = open(path, 'a')
        stem.util.log.notice('nyx %s opening log file (%s)' % (nyx.__version__, path))
      except (IOError, OSError) as exc:
        stem.util.log.error('Unable to write to log file: %s' % exc.strerror)

  def write(self, msg):
    if self._file:
      try:
        self._file.write(msg + '\n')
        self._file.flush()
      except IOError as exc:
        stem.util.log.error('Unable to write to log file: %s' % exc.strerror)
        self._file = None


class LogFilters(object):
  """
  Regular expression filtering for log output. This is thread safe and tracks
  the latest selections.
  """

  def __init__(self, initial_filters = None, max_filters = 5):
    self._max_filters = max_filters
    self._selected = None
    self._past_filters = collections.OrderedDict()
    self._lock = threading.RLock()

    if initial_filters:
      # register these regexes as options, then blank our selection

      for regex in initial_filters:
        self.select(regex)

      self.select(None)

  def select(self, regex):
    with self._lock:
      if regex is None:
        self._selected = None
        return

      if regex in self._past_filters:
        del self._past_filters[regex]

      try:
        self._past_filters[regex] = re.compile(regex)
        self._selected = regex

        if len(self._past_filters) > self._max_filters:
          self._past_filters.popitem(False)
      except re.error as exc:
        stem.util.log.notice('Invalid regular expression pattern (%s): %s' % (exc, regex))

  def selection(self):
    return self._selected

  def latest_selections(self):
    return list(reversed(list(self._past_filters.keys())))

  def match(self, message):
    regex_filter = self._past_filters.get(self._selected)
    return not regex_filter or bool(regex_filter.search(message))

  def clone(self):
    with self._lock:
      copy = LogFilters(max_filters = self._max_filters)
      copy._selected = self._selected
      copy._past_filters = self._past_filters

      return copy


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
    current_year = str(datetime.datetime.now().year)

    # Pretending it's the current year. We don't know the actual year (#15607)
    # and this may fail due to leap years when picking Feb 29th (#5265).

    try:
      timestamp_str = current_year + ' ' + ' '.join(line_comp[:3])
      timestamp_str = timestamp_str.split('.', 1)[0]  # drop fractional seconds
      timestamp_comp = list(time.strptime(timestamp_str, '%Y %b %d %H:%M:%S'))
      timestamp_comp[8] = isdst

      timestamp = int(time.mktime(tuple(timestamp_comp)))  # converts local to unix time

      if timestamp > time.time():
        # log entry is from before a year boundary
        timestamp_comp[0] -= 1
        timestamp = int(time.mktime(timestamp_comp))
    except ValueError:
      raise ValueError("Log located at %s has a timestamp we don't recognize: %s" % (path, ' '.join(line_comp[:3])))

    count += 1
    yield LogEntry(timestamp, runlevel, msg)

    if 'opening log file' in msg or 'opening new log file' in msg:
      break  # this entry marks the start of this tor instance

  stem.util.log.info("Read %s entries from tor's log file: %s (read limit: %s, runtime: %0.3f)" % (count, path, read_limit if read_limit else 'none', time.time() - start_time))
