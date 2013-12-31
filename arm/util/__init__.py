"""
General purpose utilities for a variety of tasks including logging the
application's status, making cross platform system calls, parsing tor data,
and safely working with curses (hiding some of the gory details).
"""

__all__ = ["connections", "panel", "sysTools", "textInput", "torConfig", "torTools", "tracker", "uiTools"]

import getpass
import os

import stem
import stem.connection
import stem.control
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


def init_controller(args):
  """
  Provides a Controller for the endpoint specified in the given arguments.

  :param namedtuple args: arguments that arm was started with

  :returns: :class:`~stem.control.Controller` for the given arguments

  :raises: **ValueError** if unable to acquire a controller connection
  """

  global TOR_CONTROLLER
  TOR_CONTROLLER = _get_controller(args)
  return TOR_CONTROLLER


def authenticate(controller, password, chroot_path = ''):
  """
  Authenticates to the given Controller.

  :param stem.control.Controller controller: controller to be authenticated
  :param str password: password to authenticate with, **None** if nothing was
    provided
  :param str chroot_path: chroot tor resides within

  :raises: **ValueError** if unable to authenticate
  """

  try:
    controller.authenticate(password = password, chroot_path = chroot_path)
  except stem.connection.IncorrectSocketType:
    control_socket = controller.get_socket()

    if isinstance(control_socket, stem.socket.ControlPort):
      raise ValueError(msg('connect.wrong_port_type', port = control_socket.get_port()))
    else:
      raise ValueError(msg('connect.wrong_socket_type'))
  except stem.connection.UnrecognizedAuthMethods as exc:
    raise ValueError(msg('uncrcognized_auth_type', auth_methods = ', '.join(exc.unknown_auth_methods)))
  except stem.connection.IncorrectPassword:
    raise ValueError(msg('connect.incorrect_password'))
  except stem.connection.MissingPassword:
    if password:
      raise ValueError(msg('connect.missing_password_bug'))

    password = getpass.getpass(msg('connect.password_prompt') + ' ')
    return authenticate(controller, password)
  except stem.connection.UnreadableCookieFile as exc:
    raise ValueError(msg('connect.unreadable_cookie_file', path = exc.cookie_path, issue = str(exc)))
  except stem.connection.AuthenticationFailure as exc:
    raise ValueError(msg('connect.general_auth_failure', error = exc))


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


def _get_controller(args):
  """
  Provides a Controller for the endpoint specified in the given arguments.
  """

  if os.path.exists(args.control_socket):
    try:
      return stem.control.Controller.from_socket_file(args.control_socket)
    except stem.SocketError as exc:
      if args.user_provided_socket:
        raise ValueError(msg('connect.unable_to_use_socket', path = args.control_socket, error = exc))
  elif args.user_provided_socket:
    raise ValueError(msg('connect.socket_doesnt_exist', path = args.control_socket))

  try:
    return stem.control.Controller.from_port(args.control_address, args.control_port)
  except stem.SocketError as exc:
    if args.user_provided_port:
      raise ValueError(msg('connect.unable_to_use_port', address = args.control_address, port = args.control_port, error = exc))

  if not stem.util.system.is_running('tor'):
    raise ValueError(msg('connect.tor_isnt_running'))
  else:
    raise ValueError(msg('connect.no_control_port'))
