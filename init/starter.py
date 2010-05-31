#!/usr/bin/env python

"""
Command line application for monitoring Tor relays, providing real time status
information. This is the starter for the application, handling and validating
command line parameters.
"""

import os
import sys
import getopt

# includes parent directory rather than init in path (so sibling modules are included)
sys.path[0] = sys.path[0][:-5]

import interface.controller
import interface.logPanel
import util.conf
import util.torTools
import TorCtl.TorUtil

VERSION = "1.3.5_dev"
LAST_MODIFIED = "Apr 8, 2010"

DEFAULT_CONTROL_ADDR = "127.0.0.1"
DEFAULT_CONTROL_PORT = 9051
DEFAULT_CONFIG = os.path.expanduser("~/.armrc")
DEFAULT_LOGGED_EVENTS = "N3" # tor and arm NOTICE, WARN, and ERR events
AUTH_CFG = "init.password" # config option for user's controller password

OPT = "i:c:be:vh"
OPT_EXPANDED = ["interface=", "config=", "blind", "event=", "version", "help"]
HELP_MSG = """Usage arm [OPTION]
Terminal status monitor for Tor relays.

  -i, --interface [ADDRESS:]PORT  change control interface from %s:%i
  -c, --config CONFIG_PATH        loaded configuration options, CONFIG_PATH
                                    defaults to: %s
  -b, --blind                     disable connection lookups
  -e, --event EVENT_FLAGS         event types in message log  (default: %s)
%s
  -v, --version                   provides version information
  -h, --help                      presents this help

Example:
arm -b -i 1643          hide connection data, attaching to control port 1643
arm -e we -c /tmp/cfg   use this configuration file with 'WARN'/'ERR' events
""" % (DEFAULT_CONTROL_ADDR, DEFAULT_CONTROL_PORT, DEFAULT_CONFIG, DEFAULT_LOGGED_EVENTS, interface.logPanel.EVENT_LISTING)

def isValidIpAddr(ipStr):
  """
  Returns true if input is a valid IPv4 address, false otherwise.
  """
  
  for i in range(4):
    if i < 3:
      divIndex = ipStr.find(".")
      if divIndex == -1: return False # expected a period to be valid
      octetStr = ipStr[:divIndex]
      ipStr = ipStr[divIndex + 1:]
    else:
      octetStr = ipStr
    
    try:
      octet = int(octetStr)
      if not octet >= 0 or not octet <= 255: return False
    except ValueError:
      # address value isn't an integer
      return False
  
  return True

if __name__ == '__main__':
  controlAddr = DEFAULT_CONTROL_ADDR     # controller interface IP address
  controlPort = DEFAULT_CONTROL_PORT     # controller interface port
  configPath = DEFAULT_CONFIG            # path used for customized configuration
  isBlindMode = False                    # allows connection lookups to be disabled
  loggedEvents = DEFAULT_LOGGED_EVENTS   # flags for event types in message log
  
  # parses user input, noting any issues
  try:
    opts, args = getopt.getopt(sys.argv[1:], OPT, OPT_EXPANDED)
  except getopt.GetoptError, exc:
    print str(exc) + " (for usage provide --help)"
    sys.exit()
  
  for opt, arg in opts:
    if opt in ("-i", "--interface"):
      # defines control interface address/port
      try:
        divIndex = arg.find(":")
        
        if divIndex == -1:
          controlPort = int(arg)
        else:
          controlAddr = arg[0:divIndex]
          controlPort = int(arg[divIndex + 1:])
        
        # validates that input is a valid ip address and port
        if divIndex != -1 and not isValidIpAddr(controlAddr):
          raise AssertionError("'%s' isn't a valid IP address" % controlAddr)
        elif controlPort < 0 or controlPort > 65535:
          raise AssertionError("'%s' isn't a valid port number (ports range 0-65535)" % controlPort)
      except ValueError:
        print "'%s' isn't a valid port number" % arg
        sys.exit()
      except AssertionError, exc:
        print exc
        sys.exit()
    elif opt in ("-c", "--config"): configPath = arg        # sets path of user's config
    elif opt in ("-b", "--blind"): isBlindMode = True       # prevents connection lookups
    elif opt in ("-e", "--event"): loggedEvents = arg       # set event flags
    elif opt in ("-v", "--version"):
      print "arm version %s (released %s)\n" % (VERSION, LAST_MODIFIED)
      sys.exit()
    elif opt in ("-h", "--help"):
      print HELP_MSG
      sys.exit()
  
  # attempts to load user's custom configuration
  config = util.conf.getConfig("arm")
  config.path = configPath
  
  try: config.load()
  except IOError, exc: print "Failed to load configuration (using defaults): %s" % exc
  
  # validates and expands log event flags
  try:
    expandedEvents = interface.logPanel.expandEvents(loggedEvents)
  except ValueError, exc:
    for flag in str(exc):
      print "Unrecognized event flag: %s" % flag
    sys.exit()
  
  # temporarily disables TorCtl logging to prevent issues from going to stdout while starting
  TorCtl.TorUtil.loglevel = "NONE"
  
  # sets up TorCtl connection, prompting for the passphrase if necessary and
  # sending problems to stdout if they arise
  util.torTools.INCORRECT_PASSWORD_MSG = "Controller password found in '%s' was incorrect" % configPath
  authPassword = config.get(AUTH_CFG, None)
  conn = util.torTools.connect(controlAddr, controlPort, authPassword)
  if conn == None: sys.exit(1)
  
  controller = util.torTools.getConn()
  controller.init(conn)
  
  interface.controller.startTorMonitor(expandedEvents, isBlindMode)
  conn.close()

