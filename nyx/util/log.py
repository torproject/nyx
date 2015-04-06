"""
Logging utilities, primiarily short aliases for logging a message at various
runlevels.
"""

import collections
import time

import stem.util.log
import stem.util.system

import nyx.util

LogEntry = collections.namedtuple('LogEntry', [
  'timestamp',
  'runlevel',
  'message',
])

TOR_RUNLEVELS = ['DEBUG', 'INFO', 'NOTICE', 'WARN', 'ERR']


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
  LogEntry this provides have three attributes...

    * **timestamp** (int)
    * **runlevel** (str)
    * **message** (str)

  **Note:** The 'timestamp' has a hardcoded year of 2012 due to :trac:`15607`.

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
