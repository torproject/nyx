"""
Command line application for monitoring Tor relays, providing real time status
information. This starts the application, parsing arguments and getting a Tor
connection.
"""

import curses
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
import arm.util.panel
import arm.util.torConfig
import arm.util.torTools
import arm.util.tracker
import arm.util.uiTools

import stem
import stem.util.conf
import stem.util.log
import stem.util.system

from arm.util import BASE_DIR, init_controller, authenticate, msg, trace, info, notice, warn, load_settings

CONFIG = stem.util.conf.get_config('arm')


def main():
  CONFIG.set('start_time', str(int(time.time())))

  try:
    load_settings()
  except IOError as exc:
    print msg('config.unable_to_load_settings', error = exc)
    sys.exit(1)

  try:
    args = arm.arguments.parse(sys.argv[1:])
    CONFIG.set('startup.events', args.logged_events)
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

  _load_user_armrc(args.config)

  try:
    controller = init_controller(args)
    authenticate(controller, CONFIG.get('tor.password', None), CONFIG.get('tor.chroot', ''))

    # TODO: Our tor_controller() method will gradually replace the torTools
    # module, but until that we need to initialize it too.

    arm.util.torTools.getConn().init(controller)
  except ValueError as exc:
    print exc
    exit(1)

  _warn_if_root(controller)
  _warn_if_unable_to_get_pid(controller)
  _setup_freebsd_chroot(controller)
  _notify_of_unknown_events()
  _clear_password()
  _load_tor_config_descriptions()
  _use_english_subcommands()
  _use_unicode()
  _set_process_name()

  try:
    curses.wrapper(arm.controller.start_arm)
  except UnboundLocalError as exc:
    if os.environ['TERM'] != 'xterm':
      print msg('setup.unknown_term', term = os.environ['TERM'])
    else:
      raise exc
  except KeyboardInterrupt:
    pass  # skip printing a stack trace
  finally:
    arm.util.panel.HALT_ACTIVITY = True
    _shutdown_daemons(controller)


def _setup_debug_logging(args):
  """
  Configures us to log at stem's trace level to debug log path, and notes some
  general diagnostic information.
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

  logger = stem.util.log.get_logger()
  logger.addHandler(debug_handler)

  armrc_content = "[file doesn't exist]"

  if os.path.exists(args.config):
    try:
      with open(args.config) as armrc_file:
        armrc_content = armrc_file.read()
    except IOError as exc:
      armrc_content = "[unable to read file: %s]" % exc.strerror

  trace(
    'debug.header',
    arm_version = arm.__version__,
    stem_version = stem.__version__,
    python_version = '.'.join(map(str, sys.version_info[:3])),
    system = platform.system(),
    platform = ' '.join(platform.dist()),
    armrc_path = args.config,
    armrc_content = armrc_content,
  )


def _load_user_armrc(path):
  """
  Loads user's personal armrc if it's available.
  """

  if os.path.exists(path):
    try:
      CONFIG.load(path)

      # If the user provided us with a chroot then validate and normalize the
      # path.

      chroot = CONFIG.get('tor.chroot', '').strip().rstrip(os.path.sep)

      if chroot and not os.path.exists(chroot):
        notice('setup.chroot_doesnt_exist', path = chroot)
        CONFIG.set('tor.chroot', '')
      else:
        CONFIG.set('tor.chroot', chroot)  # use the normalized path
    except IOError as exc:
      warn('config.unable_to_read_file', error = exc.strerror)
  else:
    notice('config.nothing_loaded', path = path)


def _warn_if_root(controller):
  """
  Give a notice if tor or arm are running with root.
  """

  tor_user = controller.get_user(None)

  if tor_user == 'root':
    notice('setup.tor_is_running_as_root')
  elif os.getuid() == 0:
    tor_user = tor_user if tor_user else '<tor user>'
    notice('setup.arm_is_running_as_root', tor_user = tor_user)


def _warn_if_unable_to_get_pid(controller):
  """
  Provide a warning if we're unable to determine tor's pid. This in turn will
  limit our ability to query information about the process later.
  """

  try:
    controller.get_pid()
  except ValueError:
    warn('setup.unable_to_determine_pid')


def _setup_freebsd_chroot(controller):
  """
  If we're running under FreeBSD then check the system for a chroot path.
  """

  if not CONFIG.get('tor.chroot', None) and platform.system() == 'FreeBSD':
    jail_chroot = stem.util.system.get_bsd_jail_path(controller.get_pid(0))

    if jail_chroot and os.path.exists(jail_chroot):
      info('setup.set_freebsd_chroot', path = jail_chroot)
      CONFIG.set('tor.chroot', jail_chroot)


def _notify_of_unknown_events():
  """
  Provides a notice about any event types tor supports but arm doesn't.
  """

  missing_events = arm.arguments.missing_event_types()

  if missing_events:
    info('setup.unknown_event_types', event_types = ', '.join(missing_events))


def _clear_password():
  """
  Removing the reference to our controller password so the memory can be freed.
  Without direct memory access this is about the best we can do to clear it.
  """

  CONFIG.set('tor.password', '')


def _load_tor_config_descriptions():
  """
  Attempt to determine descriptions for tor's configuration options.
  """

  arm.util.torConfig.loadConfigurationDescriptions(BASE_DIR)


def _use_english_subcommands():
  """
  Make subcommands we run (ps, netstat, etc) provide us with English results.
  This is important so we can parse the output.
  """

  os.putenv('LANG', 'C')


def _use_unicode():
  """
  If using our LANG variable for rendering multi-byte characters lets us
  get unicode support then then use it. This needs to be done before
  initializing curses.
  """

  if arm.util.uiTools.isUnicodeAvailable():
    locale.setlocale(locale.LC_ALL, '')


def _set_process_name():
  """
  Attempts to rename our process from "python setup.py <input args>" to
  "arm <input args>".
  """

  stem.util.system.set_process_name("arm\0%s" % "\0".join(sys.argv[1:]))


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
