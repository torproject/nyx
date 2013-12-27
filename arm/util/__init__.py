"""
General purpose utilities for a variety of tasks including logging the
application's status, making cross platform system calls, parsing tor data,
and safely working with curses (hiding some of the gory details).
"""

__all__ = ["connections", "panel", "sysTools", "textInput", "torConfig", "torTools", "tracker", "uiTools"]

import os

import arm.util.torTools

import stem.util.conf
import stem.util.log

TOR_CONTROLLER = None
BASE_DIR = os.path.sep.join(__file__.split(os.path.sep)[:-2])


def tor_controller():
  """
  Singleton for getting our tor controller connection.

  :returns: :class:`~stem.control.Controller` arm is using
  """

  return TOR_CONTROLLER


def init_controller(controller):
  """
  Registers an initialized tor controller.

  :param stem.control.Controller controller: tor controller for arm to use
  """

  global TOR_CONTROLLER
  TOR_CONTROLLER = controller

  # TODO: Our controller() method will gradually replace the torTools module,
  # but until that we need to initialize it too.

  arm.util.torTools.getConn().init(controller)


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


def load_settings():
  """
  Loads arms internal settings. This should be treated as a fatal failure if
  unsuccessful.

  :raises: **IOError** if we're unable to read or parse our internal
    configurations
  """

  config = stem.util.conf.get_config('arm')

  if not config.get('settings_loaded', False):
    config_dir = os.path.join(BASE_DIR, 'config')

    for config_file in os.listdir(config_dir):
      config.load(os.path.join(config_dir, config_file))

    config.set('settings_loaded', 'true')


def _log(runlevel, message, **attr):
  """
  Logs the given message, formatted with optional attributes.

  :param stem.util.log.Runlevel runlevel: runlevel at which to log the message
  :param str message: message handle to log
  :param dict attr: attributes to format the message with
  """

  stem.util.log.log(runlevel, msg(message, **attr))
