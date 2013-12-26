"""
Command line application for monitoring Tor relays, providing real time status
information. This starts the applicatin, getting a tor connection and parsing
arguments.
"""

import curses
import getopt
import getpass
import locale
import logging
import os
import platform
import sys
import time
import threading

import arm
import arm.arguments
import arm.controller
import arm.logPanel
import arm.util.panel
import arm.util.torConfig
import arm.util.torTools
import arm.util.tracker
import arm.util.uiTools

import stem
import stem.connection
import stem.control
import stem.util.conf
import stem.util.connection
import stem.util.log
import stem.util.system

from arm.util import msg, trace, notice, warn

SETTINGS_PATH = os.path.join(os.path.dirname(__file__), 'settings.cfg')

CONFIG = stem.util.conf.config_dict("arm", {
  'tor.chroot': '',
  'tor.password': None,
})


def main():
  config = stem.util.conf.get_config('arm')
  config.set('start_time', str(int(time.time())))

  try:
    config.load(SETTINGS_PATH)
  except IOError as exc:
    print msg('config.unable_to_load_settings', path = SETTINGS_PATH, error = exc)
    sys.exit(1)

  try:
    args = arm.arguments.parse(sys.argv[1:])
  except getopt.GetoptError as exc:
    print msg('usage.invalid_arguments', error = exc)
    sys.exit(1)
  except ValueError as exc:
    print exc
    sys.exit(1)

  if args.print_help:
    print arm.arguments.get_help()
    sys.exit()
  elif args.print_version:
    print arm.arguments.get_version()
    sys.exit()

  if args.debug_path is not None:
    try:
      _setup_debug_logging(args)
      print msg('debug.saving_to_path', path = args.debug_path)
    except IOError as exc:
      print msg('debug.unable_to_write_file', path = args.debug_path, error = exc.strerror)
      sys.exit(1)

  # loads user's personal armrc if available

  if os.path.exists(args.config):
    try:
      config.load(args.config)
    except IOError as exc:
      warn('config.unable_to_read_file', error = exc.strerror)
  else:
    notice('config.nothing_loaded', path = args.config)

  config.set('startup.events', args.logged_events)

  # check that the chroot exists and strip trailing slashes

  chroot = CONFIG['tor.chroot'].strip().rstrip(os.path.sep)

  if chroot and not os.path.exists(chroot):
    stem.util.log.notice(msg('setup.chroot_doesnt_exist', path = chroot))
    config.set('tor.chroot', '')
  else:
    config.set('tor.chroot', chroot)  # use the normalized path

  # validates and expands log event flags

  try:
    arm.logPanel.expandEvents(args.logged_events)
  except ValueError as exc:
    for flag in str(exc):
      print msg('usage.unrecognized_log_flag', flag = flag)

    sys.exit(1)

  try:
    controller = _get_controller(args)
    _authenticate(controller, CONFIG['tor.password'])
    arm.util.torTools.getConn().init(controller)
  except ValueError as exc:
    print exc
    exit(1)

  # Removing references to the controller password so the memory can be
  # freed. Without direct memory access this is about the best we can do.

  config.set('tor.password', '')

  # Give a notice if tor or arm are running with root. Querying connections
  # usually requires us to have the same permissions as tor so if tor is
  # running as root then drop this notice (they're already then being warned
  # about tor being root anyway).

  tor_user = controller.get_user(None)

  if tor_user == "root":
    notice('setup.tor_is_running_as_root')
  elif os.getuid() == 0:
    notice('setup.arm_is_running_as_root', tor_user = tor_user if tor_user else "<tor user>")

  # fetches descriptions for tor's configuration options

  arm.util.torConfig.loadConfigurationDescriptions(os.path.dirname(__file__))

  # Attempts to rename our process from "python setup.py <input args>" to
  # "arm <input args>"

  stem.util.system.set_process_name("arm\0%s" % "\0".join(sys.argv[1:]))

  # Makes subcommands provide us with English results (this is important so we
  # can properly parse it).

  os.putenv("LANG", "C")

  # check that we'll be able to get tor's pid later

  try:
    controller.get_pid()
  except ValueError:
    warn('setup.unable_to_determine_pid')

  # If we're running under FreeBSD then check the system for a chroot path.

  if not CONFIG['tor.chroot'] and platform.system() == 'FreeBSD':
    jail_chroot = stem.util.system.get_bsd_jail_path(controller.get_pid(0))

    if jail_chroot and os.path.exists(jail_chroot):
      info('setup.set_freebsd_chroot', path = jail_chroot)
      config.set('tor.chroot', jail_chroot)

  # If using our LANG variable for rendering multi-byte characters lets us
  # get unicode support then then use it. This needs to be done before
  # initializing curses.

  if arm.util.uiTools.isUnicodeAvailable():
    locale.setlocale(locale.LC_ALL, "")

  # provides a notice about any event types tor supports but arm doesn't

  missing_event_types = arm.logPanel.getMissingEventTypes()

  if missing_event_types:
    info('setup.unknown_event_types', event_types = ', '.join(missing_event_types))

  try:
    curses.wrapper(arm.controller.start_arm)
  except UnboundLocalError as exc:
    if os.environ['TERM'] != 'xterm':
      print msg('setup.unknown_term', term = os.environ['TERM'])
    else:
      raise exc
  except KeyboardInterrupt:
    # Skip printing stack trace in case of keyboard interrupt. The
    # HALT_ACTIVITY attempts to prevent daemons from triggering a curses redraw
    # (which would leave the user's terminal in a screwed up state). There is
    # still a tiny timing issue here (after the exception but before the flag
    # is set) but I've never seen it happen in practice.

    arm.util.panel.HALT_ACTIVITY = True
  finally:
    _shutdown_daemons(controller)

def _get_controller(args):
  """
  Provides a Controller for the endpoint specified in the given arguments.

  :param namedtuple args: arguments that arm was started with

  :returns: :class:`~stem.control.Controller` for the given arguments

  :raises: **ValueError** if unable to acquire a controller connection
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


def _authenticate(controller, password):
  """
  Authenticates to the given Controller.

  :param stem.control.Controller controller: controller to be authenticated
  :param str password: password to authenticate with, **None** if nothing was
    provided

  :raises: **ValueError** if unable to authenticate
  """

  try:
    controller.authenticate(password = password, chroot_path = CONFIG['tor.chroot'])
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
    return _authenticate(controller, password)
  except stem.connection.UnreadableCookieFile as exc:
    raise ValueError(msg('connect.unreadable_cookie_file', path = exc.cookie_path, issue = str(exc)))
  except stem.connection.AuthenticationFailure as exc:
    raise ValueError(msg('connect.general_auth_failure', error = exc))


def _setup_debug_logging(args):
  """
  Configures us to log at stem's trace level to debug log path, and notes some
  general diagnostic information.

  :param namedtuple args: arguments that arm was started with

  :raises: **IOError** if we can't log to this location
  """

  debug_dir = os.path.dirname(args.debug_path)

  if not os.path.exists(debug_dir):
    os.makedirs(debug_dir)

  debug_handler = logging.FileHandler(args.debug_path, mode = 'w')
  debug_handler.setLevel(stem.util.log.logging_level(stem.util.log.TRACE))
  debug_handler.setFormatter(logging.Formatter(
    fmt = '%(asctime)s [%(levelname)s] %(message)s',
    datefmt = '%m/%d/%Y %H:%M:%S'
  ))

  stem.util.log.get_logger().addHandler(debug_handler)

  if not os.path.exists(args.config):
    armrc_content = "[file doesn't exist]"
  else:
    try:
      with open(args.config) as armrc_file:
        armrc_content = armrc_file.read()
    except IOError as exc:
      armrc_content = "[unable to read file: %s]" % exc.strerror

  trace('debug.header',
    arm_version = arm.__version__,
    stem_version = stem.__version__,
    python_version = '.'.join(map(str, sys.version_info[:3])),
    system = platform.system(),
    platform = " ".join(platform.dist()),
    armrc_path = args.config,
    armrc_content = armrc_content,
  )


def _shutdown_daemons(controller):
  """
  Stops and joins on worker threads.
  """

  close_controller = threading.Thread(target = controller.close)
  close_controller.setDaemon(True)
  close_controller.start()

  halt_threads = [
    arm.controller.stop_controller(),
    arm.util.tracker.stop_trackers(),
    close_controller,
  ]

  for thread in halt_threads:
    thread.join()


if __name__ == '__main__':
  main()
