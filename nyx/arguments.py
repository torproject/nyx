# Copyright 2013-2016, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Commandline argument parsing for nyx.
"""

import collections
import getopt
import os

import nyx
import nyx.log

import stem.util.connection

from nyx import DATA_DIR, msg

DEFAULT_ARGS = {
  'control_address': '127.0.0.1',
  'control_port': 9051,
  'user_provided_port': False,
  'control_socket': '/var/run/tor/control',
  'user_provided_socket': False,
  'config': os.path.join(DATA_DIR, 'nyxrc'),
  'debug_path': None,
  'logged_events': 'NOTICE,WARN,ERR,NYX_NOTICE,NYX_WARNING,NYX_ERROR',
  'print_version': False,
  'print_help': False,
}

OPT = 'i:s:c:d:l:vh'

OPT_EXPANDED = [
  'interface=',
  'socket=',
  'config=',
  'debug=',
  'log=',
  'version',
  'help',
]

TOR_EVENT_TYPES = {
  # runlevels

  'd': 'DEBUG',
  'i': 'INFO',
  'n': 'NOTICE',
  'w': 'WARN',
  'e': 'ERR',

  # important events

  'b': 'BW',
  'c': 'CIRC',
  's': 'STREAM',

  # everything else

  'a': 'ADDRMAP',
  'f': 'AUTHDIR_NEWDESCS',
  'j': 'BUILDTIMEOUT_SET',
  'k': 'CELL_STATS',
  'l': 'CIRC_BW',
  'm': 'CIRC_MINOR',
  'p': 'CONF_CHANGED',
  'q': 'CONN_BW',
  'r': 'CLIENTS_SEEN',
  'u': 'DESCCHANGED',
  'g': 'GUARD',
  'h': 'HS_DESC',
  'v': 'HS_DESC_CONTENT',
  'x': 'NETWORK_LIVENESS',
  'y': 'NEWCONSENSUS',
  'z': 'NEWDESC',
  'B': 'NS',
  'o': 'ORCONN',
  'C': 'SIGNAL',
  'F': 'STREAM_BW',
  'G': 'STATUS_CLIENT',
  'H': 'STATUS_GENERAL',
  'I': 'STATUS_SERVER',
  'J': 'TB_EMPTY',
  't': 'TRANSPORT_LAUNCHED',
}


def parse(argv):
  """
  Parses our arguments, providing a named tuple with their values.

  :param list argv: input arguments to be parsed

  :returns: a **named tuple** with our parsed arguments

  :raises: **ValueError** if we got an invalid argument
  """

  args = dict(DEFAULT_ARGS)

  try:
    recognized_args, unrecognized_args = getopt.getopt(argv, OPT, OPT_EXPANDED)

    if unrecognized_args:
      error_msg = "aren't recognized arguments" if len(unrecognized_args) > 1 else "isn't a recognized argument"
      raise getopt.GetoptError("'%s' %s" % ("', '".join(unrecognized_args), error_msg))
  except getopt.GetoptError as exc:
    raise ValueError(msg('usage.invalid_arguments', error = exc))

  for opt, arg in recognized_args:
    if opt in ('-i', '--interface'):
      if ':' in arg:
        address, port = arg.split(':', 1)
      else:
        address, port = None, arg

      if address is not None:
        if not stem.util.connection.is_valid_ipv4_address(address):
          raise ValueError(msg('usage.not_a_valid_address', address_input = address))

        args['control_address'] = address

      if not stem.util.connection.is_valid_port(port):
        raise ValueError(msg('usage.not_a_valid_port', port_input = port))

      args['control_port'] = int(port)
      args['user_provided_port'] = True
    elif opt in ('-s', '--socket'):
      args['control_socket'] = arg
      args['user_provided_socket'] = True
    elif opt in ('-c', '--config'):
      args['config'] = arg
    elif opt in ('-d', '--debug'):
      args['debug_path'] = os.path.expanduser(arg)
    elif opt in ('-l', '--log'):
      try:
        validate_events(arg)
      except ValueError as exc:
        raise ValueError(msg('usage.unrecognized_log_events', events = exc))

      args['logged_events'] = arg
    elif opt in ('-v', '--version'):
      args['print_version'] = True
    elif opt in ('-h', '--help'):
      args['print_help'] = True

  # translates our args dict into a named tuple

  Args = collections.namedtuple('Args', args.keys())
  return Args(**args)


def get_help():
  """
  Provides our --help usage information.

  :returns: **str** with our usage information
  """

  return msg(
    'usage.help_output',
    address = DEFAULT_ARGS['control_address'],
    port = DEFAULT_ARGS['control_port'],
    socket = DEFAULT_ARGS['control_socket'],
    config_path = DEFAULT_ARGS['config'],
  )


def get_version():
  """
  Provides our --version information.

  :returns: **str** with our versioning information
  """

  return msg(
    'usage.version_output',
    version = nyx.__version__,
    date = nyx.__release_date__,
  )


def validate_events(events):
  """
  Check whether the events are any one of Tor runlevels, Nyx runlevels or
  Tor events.
  """

  valid_events = set(TOR_EVENT_TYPES.values() + nyx.log.NYX_RUNLEVELS)
  accepted_events, invalid_events = set(), set()

  events = events.split(',')
  for event in events:
    if event in valid_events:
      accepted_events.update([event])
    else:
      invalid_events.update([event])

  if invalid_events:
    raise ValueError(','.join(invalid_events))
  else:
    return accepted_events
