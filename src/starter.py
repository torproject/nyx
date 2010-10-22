#!/usr/bin/env python

"""
Command line application for monitoring Tor relays, providing real time status
information. This is the starter for the application, handling and validating
command line parameters.
"""

import os
import sys
import getopt

import version
import interface.controller
import interface.logPanel
import util.conf
import util.connections
import util.hostnames
import util.log
import util.panel
import util.sysTools
import util.torrc
import util.torTools
import util.uiTools
import TorCtl.TorCtl
import TorCtl.TorUtil

DEFAULT_CONFIG = os.path.expanduser("~/.armrc")
DEFAULTS = {"startup.controlPassword": None,
            "startup.interface.ipAddress": "127.0.0.1",
            "startup.interface.port": 9051,
            "startup.blindModeEnabled": False,
            "startup.events": "N3"}

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
""" % (DEFAULTS["startup.interface.ipAddress"], DEFAULTS["startup.interface.port"], DEFAULT_CONFIG, DEFAULTS["startup.events"], interface.logPanel.EVENT_LISTING)

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
  param = dict([(key, None) for key in DEFAULTS.keys()])
  configPath = DEFAULT_CONFIG            # path used for customized configuration
  
  # parses user input, noting any issues
  try:
    opts, args = getopt.getopt(sys.argv[1:], OPT, OPT_EXPANDED)
  except getopt.GetoptError, exc:
    print str(exc) + " (for usage provide --help)"
    sys.exit()
  
  for opt, arg in opts:
    if opt in ("-i", "--interface"):
      # defines control interface address/port
      controlAddr, controlPort = None, None
      divIndex = arg.find(":")
      
      try:
        if divIndex == -1:
          controlPort = int(arg)
        else:
          controlAddr = arg[0:divIndex]
          controlPort = int(arg[divIndex + 1:])
      except ValueError:
        print "'%s' isn't a valid port number" % arg
        sys.exit()
      
      param["startup.interface.ipAddress"] = controlAddr
      param["startup.interface.port"] = controlPort
    elif opt in ("-c", "--config"): configPath = arg  # sets path of user's config
    elif opt in ("-b", "--blind"):
      param["startup.blindModeEnabled"] = True        # prevents connection lookups
    elif opt in ("-e", "--event"):
      param["startup.events"] = arg                   # set event flags
    elif opt in ("-v", "--version"):
      print "arm version %s (released %s)\n" % (version.VERSION, version.LAST_MODIFIED)
      sys.exit()
    elif opt in ("-h", "--help"):
      print HELP_MSG
      sys.exit()
  
  config = util.conf.getConfig("arm")
  
  # attempts to fetch attributes for parsing tor's logs, configuration, etc
  try: config.load("%s/settings.cfg" % os.path.dirname(sys.argv[0]))
  except IOError, exc:
    msg = "Failed to load the parsing configuration. This will be problematic for a few things like torrc validation and log duplication detection (%s)" % str(exc)
    util.log.log(util.log.WARN, msg)
  
  # loads user's personal armrc if available
  if os.path.exists(configPath):
    try:
      config.load(configPath)
      
      # revises defaults to match user's configuration
      config.update(DEFAULTS)
      
      # loads user preferences for utilities
      for utilModule in (util.conf, util.connections, util.hostnames, util.log, util.panel, util.sysTools, util.torrc, util.torTools, util.uiTools):
        utilModule.loadConfig(config)
    except IOError, exc:
      msg = "Failed to load configuration (using defaults): \"%s\"" % str(exc)
      util.log.log(util.log.WARN, msg)
  else:
    # no armrc found, falling back to the defaults in the source
    msg = "No configuration found at '%s', using defaults" % configPath
    util.log.log(util.log.NOTICE, msg)
  
  # overwrites undefined parameters with defaults
  for key in param.keys():
    if param[key] == None: param[key] = DEFAULTS[key]
  
  # validates that input has a valid ip address and port
  controlAddr = param["startup.interface.ipAddress"]
  controlPort = param["startup.interface.port"]
  
  if not isValidIpAddr(controlAddr):
    print "'%s' isn't a valid IP address" % controlAddr
    sys.exit()
  elif controlPort < 0 or controlPort > 65535:
    print "'%s' isn't a valid port number (ports range 0-65535)" % controlPort
    sys.exit()
  
  # validates and expands log event flags
  try:
    expandedEvents = interface.logPanel.expandEvents(param["startup.events"])
  except ValueError, exc:
    for flag in str(exc):
      print "Unrecognized event flag: %s" % flag
    sys.exit()
  
  # temporarily disables TorCtl logging to prevent issues from going to stdout while starting
  TorCtl.TorUtil.loglevel = "NONE"
  
  # sets up TorCtl connection, prompting for the passphrase if necessary and
  # sending problems to stdout if they arise
  TorCtl.INCORRECT_PASSWORD_MSG = "Controller password found in '%s' was incorrect" % configPath
  authPassword = config.get("startup.controlPassword", DEFAULTS["startup.controlPassword"])
  conn = TorCtl.TorCtl.connect(controlAddr, controlPort, authPassword)
  if conn == None: sys.exit(1)
  
  controller = util.torTools.getConn()
  controller.init(conn)
  
  interface.controller.startTorMonitor(expandedEvents, param["startup.blindModeEnabled"])
  conn.close()

