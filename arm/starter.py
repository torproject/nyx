"""
Command line application for monitoring Tor relays, providing real time status
information. This starts the applicatin, getting a tor connection and parsing
arguments.
"""

import collections
import getopt
import getpass
import locale
import logging
import os
import platform
import sys
import time

import arm.controller
import arm.logPanel
import arm.util.torConfig
import arm.util.torTools
import arm.util.uiTools

import stem.connection
import stem.util.conf
import stem.util.connection
import stem.util.log
import stem.util.system

from arm import __version__, __release_date__
from stem.control import Controller

LOG_DUMP_PATH = os.path.expanduser("~/.arm/log")

CONFIG = stem.util.conf.config_dict("arm", {
  'tor.password': None,
  'startup.blindModeEnabled': False,
  'startup.events': 'N3',
  'msg.help': '',
  'msg.wrong_port_type': '',
  'msg.wrong_socket_type': '',
  'msg.uncrcognized_auth_type': '',
  'msg.missing_password_bug': '',
  'msg.unreadable_cookie_file': '',
  'msg.tor_is_running_as_root': '',
  'msg.arm_is_running_as_root': '',
  'msg.unable_to_read_config': '',
})

# torrc entries that are scrubbed when dumping
PRIVATE_TORRC_ENTRIES = ["HashedControlPassword", "Bridge", "HiddenServiceDir"]

# Makes subcommands provide us with English results (this is important so we
# can properly parse it).

os.putenv("LANG", "C")

# Our default arguments. The _get_args() function provides a named tuple of
# this merged with our argv.

ARGS = {
  'control_address': '127.0.0.1',
  'control_port': 9051,
  'user_provided_port': False,
  'control_socket': '/var/run/tor/control',
  'user_provided_socket': False,
  'config': os.path.expanduser("~/.arm/armrc"),
  'debug': False,
  'blind': False,
  'logged_events': 'N3',
  'print_version': False,
  'print_help': False,
}

OPT = "gi:s:c:dbe:vh"
OPT_EXPANDED = ["interface=", "socket=", "config=", "debug", "blind", "event=", "version", "help"]

IS_SETTINGS_LOADED = False


def _load_settings():
  """
  Loads arms internal settings from its 'settings.cfg'. This comes bundled with
  arm and should be considered to be an error if it can't be loaded. If the
  settings have already been loaded then this is a no-op.

  :raises: **ValueError** if the settings can't be loaded
  """

  global IS_SETTINGS_LOADED

  if not IS_SETTINGS_LOADED:
    config = stem.util.conf.get_config("arm")
    settings_path = os.path.join(os.path.dirname(__file__), 'settings.cfg')

    try:
      config.load(settings_path)
      IS_SETTINGS_LOADED = True
    except IOError as exc:
      raise ValueError("Unable to load arm's internal configuration (%s): %s" % (settings_path, exc))


def _get_args(argv):
  """
  Parses our arguments, providing a named tuple with their values.

  :param list argv: input arguments to be parsed

  :returns: a **named tuple** with our parsed arguments

  :raises: **ValueError** if we got an invalid argument
  :raises: **getopt.GetoptError** if the arguments don't conform with what we
    accept
  """

  args = dict(ARGS)

  for opt, arg in getopt.getopt(argv, OPT, OPT_EXPANDED)[0]:
    if opt in ("-i", "--interface"):
      if ':' in arg:
        address, port = arg.split(':', 1)
      else:
        address, port = None, arg

      if address is not None:
        if not stem.util.connection.is_valid_ipv4_address(address):
          raise ValueError("'%s' isn't a valid IPv4 address" % address)

        args['control_address'] = address

      if not stem.util.connection.is_valid_port(port):
        raise ValueError("'%s' isn't a valid port number" % port)

      args['control_port'] = int(port)
      args['user_provided_port'] = True
    elif opt in ("-s", "--socket"):
      args['control_socket'] = arg
      args['user_provided_socket'] = True
    elif opt in ("-c", "--config"):
      args['config'] = arg
    elif opt in ("-d", "--debug"):
      args['debug'] = True
    elif opt in ("-b", "--blind"):
      args['blind'] = True
    elif opt in ("-e", "--event"):
      args['logged_events'] = arg
    elif opt in ("-v", "--version"):
      args['print_version'] = True
    elif opt in ("-h", "--help"):
      args['print_help'] = True

  # translates our args dict into a named tuple

  Args = collections.namedtuple('Args', args.keys())
  return Args(**args)


def _get_controller(args):
  """
  Provides a Controller for the endpoint specified in the given arguments.

  :param namedtuple args: arguments that arm was started with

  :returns: :class:`~stem.control.Controller` for the given arguments

  :raises: **ValueError** if unable to acquire a controller connection
  """

  if os.path.exists(args.control_socket):
    try:
      return Controller.from_socket_file(args.control_socket)
    except stem.SocketError as exc:
      if args.user_provided_socket:
        raise ValueError("Unable to connect to '%s': %s" % (args.control_socket, exc))
  elif args.user_provided_socket:
    raise ValueError("The socket file you specified (%s) doesn't exist" % args.control_socket)

  try:
    return Controller.from_port(args.control_address, args.control_port)
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


def _dumpConfig():
  """
  Dumps the current arm and tor configurations at the DEBUG runlevel. This
  attempts to scrub private information, but naturally the user should double
  check that I didn't miss anything.
  """
  
  config = stem.util.conf.get_config("arm")
  conn = arm.util.torTools.getConn()
  
  # dumps arm's configuration
  armConfigEntry = ""
  armConfigKeys = list(config.keys())
  armConfigKeys.sort()
  
  for configKey in armConfigKeys:
    # Skips some config entries that are loaded by default. This fetches
    # the config values directly to avoid misflagging them as being used by
    # arm.
    
    if not configKey.startswith("config.summary.") and not configKey.startswith("torrc.") and not configKey.startswith("msg."):
      armConfigEntry += "%s -> %s\n" % (configKey, config.get_value(configKey))
  
  if armConfigEntry: armConfigEntry = "Arm Configuration:\n%s" % armConfigEntry
  else: armConfigEntry = "Arm Configuration: None"
  
  # dumps tor's version and configuration
  torConfigEntry = "Tor (%s) Configuration:\n" % conn.getInfo("version", None)
  
  for line in conn.getInfo("config-text", "").split("\n"):
    if not line: continue
    elif " " in line: key, value = line.split(" ", 1)
    else: key, value = line, ""
    
    if key in PRIVATE_TORRC_ENTRIES:
      torConfigEntry += "%s <scrubbed>\n" % key
    else:
      torConfigEntry += "%s %s\n" % (key, value)
  
  stem.util.log.debug(armConfigEntry.strip())
  stem.util.log.debug(torConfigEntry.strip())

def main():
  startTime = time.time()

  try:
    _load_settings()
  except ValueError as exc:
    print exc
    sys.exit(1)

  # attempts to fetch attributes for parsing tor's logs, configuration, etc
  
  config = stem.util.conf.get_config("arm")
  
  pathPrefix = os.path.dirname(sys.argv[0])
  if pathPrefix and not pathPrefix.endswith("/"):
    pathPrefix = pathPrefix + "/"

  try:
    args = _get_args(sys.argv[1:])
  except getopt.GetoptError as exc:
    print "%s (for usage provide --help)" % exc
    sys.exit(1)
  except ValueError as exc:
    print exc
    sys.exit(1)

  if args.print_version:
    print "arm version %s (released %s)\n" % (__version__, __release_date__)
    sys.exit()

  if args.print_help:
    print CONFIG['msg.help'].format(
      address = ARGS['control_address'],
      port = ARGS['control_port'],
      socket = ARGS['control_socket'],
      config = ARGS['config'],
      debug_path = LOG_DUMP_PATH,
      events = ARGS['logged_events'],
      event_flags = arm.logPanel.EVENT_LISTING
    )

    sys.exit()

  config.set("startup.blindModeEnabled", str(args.blind))
  config.set("startup.events", args.logged_events)
  
  if args.debug:
    try:
      stem_logger = stem.util.log.get_logger()
      
      debugHandler = logging.FileHandler(LOG_DUMP_PATH)
      debugHandler.setLevel(stem.util.log.logging_level(stem.util.log.TRACE))
      debugHandler.setFormatter(logging.Formatter(
        fmt = '%(asctime)s [%(levelname)s] %(message)s',
        datefmt = '%m/%d/%Y %H:%M:%S'
      ))
      
      stem_logger.addHandler(debugHandler)
      
      currentTime = time.localtime()
      timeLabel = time.strftime("%H:%M:%S %m/%d/%Y (%Z)", currentTime)
      initMsg = "Arm %s Debug Dump, %s" % (version.VERSION, timeLabel)
      pythonVersionLabel = "Python Version: %s" % (".".join([str(arg) for arg in sys.version_info[:3]]))
      osLabel = "Platform: %s (%s)" % (platform.system(), " ".join(platform.dist()))
      
      stem.util.log.trace("%s\n%s\n%s\n%s\n" % (initMsg, pythonVersionLabel, osLabel, "-" * 80))
    except OSError, exc:
      print "Unable to write to debug log file: %s" % exc
    except IOError, exc:
      print "Unable to write to debug log file: %s" % exc.strerror
  
  # loads user's personal armrc if available
  if os.path.exists(args.config):
    try:
      config.load(args.config)
    except IOError, exc:
      stem.util.log.warn(CONFIG['msg.unable_to_read_config'].format(error = exc.strerror))
  else:
    # no armrc found, falling back to the defaults in the source
    stem.util.log.notice(CONFIG['msg.config_not_found'].format(path = args.config))
  
  # validates and expands log event flags
  try:
    arm.logPanel.expandEvents(args.logged_events)
  except ValueError, exc:
    for flag in str(exc):
      print "Unrecognized event flag: %s" % flag
    sys.exit()

  try:
    controller = _get_controller(args)
    _authenticate(controller, CONFIG['tor.password'])
  except ValueError as exc:
    print exc
    exit(1)

  # Removing references to the controller password so the memory can be
  # freed. Without direct memory access this is about the best we can do.

  config.set('tor.password', '')

  # initializing the connection may require user input (for the password)
  # skewing the startup time results so this isn't counted
  initTime = time.time() - startTime
  controllerWrapper = arm.util.torTools.getConn()
  
  torUser = None
  if controller:
    controllerWrapper.init(controller)
    
    # give a notice if tor is running with root
    torUser = controllerWrapper.getMyUser()
    if torUser == "root":
      stem.util.log.notice(CONFIG['msg.tor_is_running_as_root'])
  
  # Give a notice if arm is running with root. Querying connections usually
  # requires us to have the same permissions as tor so if tor is running as
  # root then drop this notice (they're already then being warned about tor
  # being root, anyway).
  
  if torUser != "root" and os.getuid() == 0:
    torUserLabel = torUser if torUser else "<tor user>"
    stem.util.log.notice(msg.arm_is_running_as_root.format(tor_user = torUserLabel))
  
  # fetches descriptions for tor's configuration options
  arm.util.torConfig.loadConfigurationDescriptions(pathPrefix)
  
  # dump tor and arm configuration when in debug mode
  if args.debug:
    stem.util.log.notice("Saving a debug log to '%s' (please check it for sensitive information before sharing)" % LOG_DUMP_PATH)
    _dumpConfig()
  
  # Attempts to rename our process from "python setup.py <input args>" to
  # "arm <input args>"
  
  try:
    stem.util.system.set_process_name("arm\0%s" % "\0".join(sys.argv[1:]))
  except: pass
  
  # If using our LANG variable for rendering multi-byte characters lets us
  # get unicode support then then use it. This needs to be done before
  # initializing curses.
  if arm.util.uiTools.isUnicodeAvailable():
    locale.setlocale(locale.LC_ALL, "")
  
  arm.controller.startTorMonitor(time.time() - initTime)

if __name__ == '__main__':
  main()

