"""
General purpose utilities for a variety of tasks including logging the
application's status, making cross platform system calls, parsing tor data,
and safely working with curses (hiding some of the gory details).
"""

__all__ = [
  'panel',
  'text_input',
  'tor_config',
  'tracker',
  'ui_tools',
]

import os
import sys

import stem
import stem.connection
import stem.control
import stem.util.conf
import stem.util.log

TOR_CONTROLLER = None
BASE_DIR = os.path.sep.join(__file__.split(os.path.sep)[:-2])

try:
  uses_settings = stem.util.conf.uses_settings('arm', os.path.join(BASE_DIR, 'config'), lazy_load = False)
except IOError as exc:
  print "Unable to load arm's internal configurations: {error}".format(error = exc)
  sys.exit(1)


def tor_controller():
  """
  Singleton for getting our tor controller connection.

  :returns: :class:`~stem.control.Controller` arm is using
  """

  return TOR_CONTROLLER


def init_controller(controller):
  """
  Sets the Controller used by arm.

  :param Controller controller: control connection to be used by arm
  """

  global TOR_CONTROLLER
  TOR_CONTROLLER = controller


def msg(message, **attr):
  """
  Provides the given message.

  :param str message: message handle to log
  :param dict attr: attributes to format the message with

  :returns: **str** that was requested
  """

  config = stem.util.conf.get_config('arm')

  try:
    return config.get('msg.%s' % message).format(**attr)
  except:
    stem.util.log.notice('BUG: We attempted to use an undefined string resource (%s)' % message)
    return ''


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

  stem.util.log.log(runlevel, msg(message, **attr))
