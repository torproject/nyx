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

SETTINGS_PATH = os.path.join(os.path.dirname(__file__), 'settings.cfg')

CONFIG = stem.util.conf.config_dict("arm", {
  'debug_log_path': '',
  'tor.password': None,
  'startup.events': 'N3',
  'msg.debug_header': '',
  'msg.wrong_port_type': '',
  'msg.wrong_socket_type': '',
  'msg.uncrcognized_auth_type': '',
  'msg.missing_password_bug': '',
  'msg.unreadable_cookie_file': '',
  'msg.tor_is_running_as_root': '',
  'msg.arm_is_running_as_root': '',
  'msg.config_not_found': '',
  'msg.unable_to_read_config': '',
  'msg.unable_to_determine_pid': '',
  'msg.unknown_term': '',
})


def _load_settings(config = 'arm'):
  """
  Loads arm's internal settings from its 'settings.cfg'. This comes bundled
  with arm and should be considered to be an error if it can't be loaded. If
  the settings have already been loaded then this is a no-op.

  :param str config: configuration config to load the parameters into

  :returns: **stem.util.conf.Config** for the given handle

  :raises: **ValueError** if the settings can't be loaded
  """

  config = stem.util.conf.get_config(config)

  if not config.get('settings_loaded', False):
    try:
      config.load(SETTINGS_PATH)
    except IOError as exc:
      raise ValueError("Unable to load arm's internal configuration (%s): %s" % (SETTINGS_PATH, exc))

  return config


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
        raise ValueError("Unable to connect to '%s': %s" % (args.control_socket, exc))
  elif args.user_provided_socket:
    raise ValueError("The socket file you specified (%s) doesn't exist" % args.control_socket)

  try:
    return stem.control.Controller.from_port(args.control_address, args.control_port)
  except stem.SocketError as exc:
    if args.user_provided_port:
      raise ValueError("Unable to connect to %s:%i: %s" % (args.control_address, args.control_port, exc))

  if not stem.util.system.is_running('tor'):
    raise ValueError("Unable to connect to tor. Are you sure it's running?")
  else:
    raise ValueError("Unable to connect to tor. Maybe it's running without a ControlPort?")


def _authenticate(controller, password):
  """
  Authenticates to the given Controller.

  :param stem.control.Controller controller: controller to be authenticated to
  :param str args: password to authenticate with, **None** if nothing was provided

  :raises: **ValueError** if unable to authenticate
  """

  chroot = arm.util.torTools.get_chroot()

  try:
    controller.authenticate(password = password, chroot_path = chroot)
  except stem.connection.IncorrectSocketType:
    control_socket = controller.get_socket()

    if isinstance(control_socket, stem.socket.ControlPort):
      raise ValueError(CONFIG['msg.wrong_port_type'].format(port = control_socket.get_port()))
    else:
      raise ValueError(CONFIG['msg.wrong_socket_type'])
  except stem.connection.UnrecognizedAuthMethods as exc:
    raise ValueError(CONFIG['msg.uncrcognized_auth_type'].format(auth_methods = ', '.join(exc.unknown_auth_methods)))
  except stem.connection.IncorrectPassword:
    raise ValueError("Incorrect password")
  except stem.connection.MissingPassword:
    if password:
      raise ValueError(CONFIG['msg.missing_password_bug'])

    password = getpass.getpass("Tor controller password: ")
    return _authenticate(controller, password)
  except stem.connection.UnreadableCookieFile as exc:
    raise ValueError(CONFIG['msg.unreadable_cookie_file'].format(path = exc.cookie_path, issue = str(exc)))
  except stem.connection.AuthenticationFailure as exc:
    raise ValueError("Unable to authenticate: %s" % exc)


def _setup_debug_logging():
  """
  Configures us to log at stem's trace level to debug log path.

  :raises: **IOError** if we can't log to this location
  """

  debug_path = os.path.expanduser(CONFIG['debug_log_path'])
  debug_dir = os.path.dirname(debug_path)

  if not os.path.exists(debug_dir):
    os.makedirs(debug_dir)

  debug_handler = logging.FileHandler(debug_path, mode = 'w')
  debug_handler.setLevel(stem.util.log.logging_level(stem.util.log.TRACE))
  debug_handler.setFormatter(logging.Formatter(
    fmt = '%(asctime)s [%(levelname)s] %(message)s',
    datefmt = '%m/%d/%Y %H:%M:%S'
  ))

  stem.util.log.get_logger().addHandler(debug_handler)


def _armrc_dump(armrc_path):
  """
  Provides a dump of our armrc or a description of why it can't be read.

  :param str armrc_path: path of the armrc

  :returns: **str** with either a dump or description of our armrc
  """

  if not os.path.exists(armrc_path):
    return "[file doesn't exist]"

  try:
    with open(armrc_path) as armrc_file:
      return armrc_file.read()
  except IOError as exc:
    return "[unable to read file: %s]" % exc.strerror


def _shutdown_daemons():
  """
  Stops and joins on worker threads.
  """

  halt_tor_controller = threading.Thread(target = arm.util.torTools.getConn().close)
  halt_tor_controller.start()

  halt_threads = [
    arm.controller.stop_controller(),
    arm.util.tracker.stop_trackers(),
    halt_tor_controller,
  ]

  for thread in halt_threads:
    thread.join()


def main():
  config = stem.util.conf.get_config("arm")
  config.set('attribute.start_time', str(int(time.time())))

  try:
    _load_settings()
    args = arm.arguments.parse(sys.argv[1:])
  except getopt.GetoptError as exc:
    print "%s (for usage provide --help)" % exc
    sys.exit(1)
  except ValueError as exc:
    print exc
    sys.exit(1)

  if args.print_help:
    print arm.arguments.get_help()
    sys.exit()

  if args.print_version:
    print "arm version %s (released %s)\n" % (arm.__version__, arm.__release_date__)
    sys.exit()

  if args.debug:
    try:
      _setup_debug_logging()
    except IOError as exc:
      print "Unable to write to our debug log file (%s): %s" % (CONFIG['debug_log_path'], exc.strerror)
      sys.exit(1)

    stem.util.log.trace(CONFIG['msg.debug_header'].format(
      arm_version = arm.__version__,
      stem_version = stem.__version__,
      python_version = '.'.join(map(str, sys.version_info[:3])),
      system = platform.system(),
      platform = " ".join(platform.dist()),
      armrc_path = args.config,
      armrc_content = _armrc_dump(args.config),
    ))

    print "Saving a debug log to %s, please check it for sensitive information before sharing" % CONFIG['debug_log_path']

  # loads user's personal armrc if available

  if os.path.exists(args.config):
    try:
      config.load(args.config)
    except IOError as exc:
      stem.util.log.warn(CONFIG['msg.unable_to_read_config'].format(error = exc.strerror))
  else:
    stem.util.log.notice(CONFIG['msg.config_not_found'].format(path = args.config))

  if args.blind:
    config.set('features.panels.show.connection', 'false')

  config.set("startup.events", args.logged_events)

  # validates and expands log event flags

  try:
    arm.logPanel.expandEvents(args.logged_events)
  except ValueError as exc:
    for flag in str(exc):
      print "Unrecognized event flag: %s" % flag

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
    stem.util.log.notice(CONFIG['msg.tor_is_running_as_root'])
  elif os.getuid() == 0:
    stem.util.log.notice(CONFIG['msg.arm_is_running_as_root'].format(
      tor_user = tor_user if tor_user else "<tor user>"
    ))

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
    stem.util.log.warn(CONFIG['msg.unable_to_determine_pid'])

  # If using our LANG variable for rendering multi-byte characters lets us
  # get unicode support then then use it. This needs to be done before
  # initializing curses.

  if arm.util.uiTools.isUnicodeAvailable():
    locale.setlocale(locale.LC_ALL, "")

  # provides a notice about any event types tor supports but arm doesn't

  missing_event_types = arm.logPanel.getMissingEventTypes()

  if missing_event_types:
    plural_label = "s" if len(missing_event_types) > 1 else ""
    stem.util.log.info("arm doesn't recognize the following event type%s: %s (log 'UNKNOWN' events to see them)" % (plural_label, ", ".join(missing_event_types)))

  try:
    curses.wrapper(arm.controller.start_arm)
  except UnboundLocalError as exc:
    if os.environ['TERM'] != 'xterm':
      print CONFIG['msg.unknown_term'].format(term = os.environ['TERM'])
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
    _shutdown_daemons()

if __name__ == '__main__':
  main()
