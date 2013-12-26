"""
Commandline argument parsing for arm.
"""

import collections
import getopt
import os

import arm

import stem.connection

from arm.util import msg

DEFAULT_ARGS = {
  'control_address': '127.0.0.1',
  'control_port': 9051,
  'user_provided_port': False,
  'control_socket': '/var/run/tor/control',
  'user_provided_socket': False,
  'config': os.path.expanduser("~/.arm/armrc"),
  'debug_path': None,
  'logged_events': 'N3',
  'print_version': False,
  'print_help': False,
}

OPT = "i:s:c:d:be:vh"

OPT_EXPANDED = [
  "interface=",
  "socket=",
  "config=",
  "debug=",
  "event=",
  "version",
  "help",
]


def parse(argv):
  """
  Parses our arguments, providing a named tuple with their values.

  :param list argv: input arguments to be parsed

  :returns: a **named tuple** with our parsed arguments

  :raises: **ValueError** if we got an invalid argument
  :raises: **getopt.GetoptError** if the arguments don't conform with what we
    accept
  """

  args = dict(DEFAULT_ARGS)

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
      args['debug_path'] = os.path.expanduser(arg)
    elif opt in ("-e", "--event"):
      args['logged_events'] = arg
    elif opt in ("-v", "--version"):
      args['print_version'] = True
    elif opt in ("-h", "--help"):
      args['print_help'] = True

  # translates our args dict into a named tuple

  Args = collections.namedtuple('Args', args.keys())
  return Args(**args)


def get_help():
  """
  Provides our --help usage information.

  :returns: **str** with our usage information
  """

  return msg('usage.help_output',
    address = DEFAULT_ARGS['control_address'],
    port = DEFAULT_ARGS['control_port'],
    socket = DEFAULT_ARGS['control_socket'],
    config = DEFAULT_ARGS['config'],
    events = DEFAULT_ARGS['logged_events'],
    event_flags = msg('misc.event_types'),
  )


def get_version():
  """
  Provides our --version information.

  :returns: **str** with our versioning information
  """

  return msg('usage.version_output',
    version = arm.__version__,
    date = arm.__release_date__,
  )
