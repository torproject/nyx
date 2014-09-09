"""
General purpose utilities for a variety of tasks supporting arm features and
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

import os
import sys

import stem.connection
import stem.util.conf

from arm.util import log

TOR_CONTROLLER = None
BASE_DIR = os.path.sep.join(__file__.split(os.path.sep)[:-2])

try:
  uses_settings = stem.util.conf.uses_settings('arm', os.path.join(BASE_DIR, 'config'), lazy_load = False)
except IOError as exc:
  print "Unable to load arm's internal configurations: %s" % exc
  sys.exit(1)


def tor_controller():
  """
  Singleton for getting our tor controller connection.

  :returns: :class:`~stem.control.Controller` arm is using
  """

  return TOR_CONTROLLER


def init_controller(*args, **kwargs):
  """
  Sets the Controller used by arm. This is a passthrough for Stem's
  :func:`~stem.connection.connect` function.

  :returns: :class:`~stem.control.Controller` arm is using
  """

  global TOR_CONTROLLER
  TOR_CONTROLLER = stem.connection.connect(*args, **kwargs)
  return TOR_CONTROLLER


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
