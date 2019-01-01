# Copyright 2009-2019, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Command line application for monitoring Tor relays, providing real time status
information. This starts the application, parsing arguments and getting a Tor
connection.
"""

import locale
import logging
import os
import platform
import sys
import time

import nyx
import nyx.arguments
import nyx.curses
import nyx.tracker

import stem
import stem.connection
import stem.util.log
import stem.util.system

from nyx import init_controller, uses_settings, nyx_interface

DEBUG_HEADER = """
Nyx {nyx_version} Debug Dump
Stem Version: {stem_version}
Python Version: {python_version}
Platform: {system} ({platform})
--------------------------------------------------------------------------------
Nyx Configuration ({nyxrc_path}):
{nyxrc_content}
--------------------------------------------------------------------------------
""".strip()

TORRC = """
--------------------------------------------------------------------------------
Torrc ({torrc_path}):
{torrc_content}
--------------------------------------------------------------------------------
""".rstrip()


@uses_settings
def main(config):
  config.set('start_time', str(int(time.time())))

  try:
    args = nyx.arguments.parse(sys.argv[1:])
    config.set('logged_events', args.logged_events)
  except ValueError as exc:
    print(exc)
    sys.exit(1)

  if args.print_help:
    print(nyx.arguments.get_help())
    sys.exit()
  elif args.print_version:
    print(nyx.arguments.get_version())
    sys.exit()

  if args.debug_path is not None:
    try:
      _setup_debug_logging(args)
      print('Saving a debug log to %s, please check it for sensitive information before sharing it.' % args.debug_path)
    except IOError as exc:
      print('Unable to write to our debug log file (%s): %s' % (args.debug_path, exc.strerror))
      sys.exit(1)

  if os.path.exists(args.config):
    try:
      config.load(args.config)
    except IOError as exc:
      stem.util.log.warn('Failed to load configuration (using defaults): "%s"' % exc.strerror)
  else:
    stem.util.log.notice('No nyxrc loaded, using defaults. You can customize nyx by placing a configuration file at %s (see https://nyx.torproject.org/nyxrc.sample for its options).' % args.config)

  # If a password is provided via the user's nyxrc that will be use, otherwise
  # users are prompted for a password if required.

  controller_password = config.get('password', None)

  if controller_password:
    stem.connection.CONNECT_MESSAGES['incorrect_password'] = 'Unable to authenticate to tor using the controller password in %s' % args.config

  controller = init_controller(
    control_port = args.control_port,
    control_socket = args.control_socket,
    password = controller_password,
    password_prompt = True,
    chroot_path = nyx.chroot(),
  )

  if controller is None:
    exit(1)

  if args.debug_path is not None:
    torrc_path = controller.get_info('config-file')

    try:
      with open(torrc_path) as torrc_file:
        torrc_content = torrc_file.read()
    except Exception as exc:
      torrc_content = 'Unable to read %s: %s' % (torrc_path, exc)

    stem.util.log.trace(TORRC.format(torrc_path = torrc_path, torrc_content = torrc_content))

  _warn_if_root(controller)
  _warn_if_unable_to_get_pid(controller)
  _warn_about_unused_config_keys()
  _use_unicode()
  _set_process_name()

  # These os.putenv calls fail on FreeBSD, and even attempting causes python to
  # print the following to stdout...
  #
  #   nyx: environment corrupt; missing value for

  if not stem.util.system.is_bsd():
    os.putenv('LANG', 'C')  # make subcommands (ps, netstat, etc) provide english results
    os.putenv('ESCDELAY', '0')  # make 'esc' take effect right away

  try:
    nyx.curses.start(nyx.draw_loop, acs_support = config.get('acs_support', True), transparent_background = True, cursor = False)
  except KeyboardInterrupt:
    pass  # skip printing a stack trace
  finally:
    nyx.curses.halt()
    _shutdown_daemons(controller)


def _setup_debug_logging(args):
  """
  Configures us to log at stem's trace level to a debug log path. This starts
  it off with some general diagnostic information.
  """

  debug_dir = os.path.dirname(args.debug_path)

  if debug_dir and not os.path.exists(debug_dir):
    os.makedirs(debug_dir)

  debug_handler = logging.FileHandler(args.debug_path, mode = 'w')
  debug_handler.setLevel(stem.util.log.logging_level(stem.util.log.TRACE))
  debug_handler.setFormatter(logging.Formatter(
    fmt = '%(asctime)s [%(levelname)s] %(message)s',
    datefmt = '%m/%d/%Y %H:%M:%S'
  ))

  logger = stem.util.log.get_logger()
  logger.addHandler(debug_handler)

  nyxrc_content = "[file doesn't exist]"

  if os.path.exists(args.config):
    try:
      with open(args.config) as nyxrc_file:
        nyxrc_content = nyxrc_file.read()
    except IOError as exc:
      nyxrc_content = '[unable to read file: %s]' % exc.strerror

  stem.util.log.trace(DEBUG_HEADER.format(
    nyx_version = nyx.__version__,
    stem_version = stem.__version__,
    python_version = '.'.join(map(str, sys.version_info[:3])),
    system = platform.system(),
    platform = ' '.join(platform.dist()),
    nyxrc_path = args.config,
    nyxrc_content = nyxrc_content,
  ))


def _warn_if_root(controller):
  """
  Give a notice if tor or nyx are running with root.
  """

  if controller.get_user(None) == 'root':
    stem.util.log.notice("Tor is currently running with root permissions. This isn't a good idea, nor should it be necessary. See the 'User UID' option on Tor's man page for an easy method of reducing its permissions after startup.")
  elif os.getuid() == 0:
    stem.util.log.notice("Nyx is currently running with root permissions. This isn't a good idea, nor should it be necessary.")


def _warn_if_unable_to_get_pid(controller):
  """
  Provide a warning if we're unable to determine tor's pid. This in turn will
  limit our ability to query information about the process later.
  """

  try:
    controller.get_pid()
  except ValueError:
    stem.util.log.warn("Unable to determine Tor's pid. Some information, like its resource usage will be unavailable.")


@uses_settings
def _warn_about_unused_config_keys(config):
  """
  Provides a notice if the user's nyxrc has any entries that are unused.
  """

  for key in sorted(config.unused_keys()):
    if not key.startswith('msg.') and not key.startswith('dedup.'):
      stem.util.log.notice('Unused configuration entry: %s' % key)


@uses_settings
def _use_unicode(config):
  """
  If using our LANG variable for rendering multi-byte characters lets us
  get unicode support then then use it. This needs to be done before
  initializing curses.
  """

  if config.get('unicode_support', True):
    is_lang_unicode = 'utf-' in os.getenv('LANG', '').lower()

    if is_lang_unicode and nyx.curses.is_wide_characters_supported():
      locale.setlocale(locale.LC_ALL, '')


def _set_process_name():
  """
  Attempts to rename our process from "python setup.py <input args>" to
  "nyx <input args>".
  """

  process_name = 'nyx\0%s' % '\0'.join(sys.argv[1:])

  try:
    stem.util.system.set_process_name(process_name)
  except IOError as exc:
    stem.util.log.info("Unable to rename our process from '%s' to '%s' (%s)." % (stem.util.system.get_process_name(), process_name.replace('\0', ' '), exc))


def _shutdown_daemons(controller):
  """
  Stops and joins on worker threads.
  """

  halt_threads = [nyx.tracker.stop_trackers()]
  interface = nyx_interface()

  if interface:
    halt_threads.append(interface.halt())

  for thread in halt_threads:
    thread.join()

  controller.close()


if __name__ == '__main__':
  main()
