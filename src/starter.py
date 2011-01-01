#!/usr/bin/env python

"""
Command line application for monitoring Tor relays, providing real time status
information. This is the starter for the application, handling and validating
command line parameters.
"""

import os
import sys
import time
import getopt

import version
import interface.controller
import interface.logPanel
import util.conf
import util.connections
import util.hostnames
import util.log
import util.panel
import util.procTools
import util.sysTools
import util.torConfig
import util.torTools
import util.uiTools
import TorCtl.TorCtl
import TorCtl.TorUtil

DEFAULT_CONFIG = os.path.expanduser("~/.arm/armrc")
CONFIG = {"startup.controlPassword": None,
          "startup.interface.ipAddress": "127.0.0.1",
          "startup.interface.port": 9051,
          "startup.blindModeEnabled": False,
          "startup.events": "N3",
          "data.cache.path": "~/.arm/cache",
          "features.config.descriptions.enabled": True,
          "log.configDescriptions.readManPageSuccess": util.log.INFO,
          "log.configDescriptions.readManPageFailed": util.log.WARN,
          "log.configDescriptions.persistance.loadSuccess": util.log.INFO,
          "log.configDescriptions.persistance.loadFailed": util.log.INFO,
          "log.configDescriptions.persistance.saveSuccess": util.log.INFO,
          "log.configDescriptions.persistance.saveFailed": util.log.NOTICE}

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
""" % (CONFIG["startup.interface.ipAddress"], CONFIG["startup.interface.port"], DEFAULT_CONFIG, CONFIG["startup.events"], interface.logPanel.EVENT_LISTING)

# filename used for cached tor config descriptions
CONFIG_DESC_FILENAME = "torConfigDescriptions.txt"

# messages related to loading the tor configuration descriptions
DESC_LOAD_SUCCESS_MSG = "Loaded configuration descriptions from '%s' (runtime: %0.3f)"
DESC_LOAD_FAILED_MSG = "Unable to load configuration descriptions (%s)"
DESC_READ_MAN_SUCCESS_MSG = "Read descriptions for tor's configuration options from its man page (runtime %0.3f)"
DESC_READ_MAN_FAILED_MSG = "Unable to read descriptions for tor's configuration options from its man page (%s)"
DESC_SAVE_SUCCESS_MSG = "Saved configuration descriptions to '%s' (runtime: %0.3f)"
DESC_SAVE_FAILED_MSG = "Unable to save configuration descriptions (%s)"

NO_INTERNAL_CFG_MSG = "Failed to load the parsing configuration. This will be problematic for a few things like torrc validation and log duplication detection (%s)"
STANDARD_CFG_LOAD_FAILED_MSG = "Failed to load configuration (using defaults): \"%s\""
STANDARD_CFG_NOT_FOUND_MSG = "No configuration found at '%s', using defaults"

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

def _loadConfigurationDescriptions():
  """
  Attempts to load descriptions for tor's configuration options, fetching them
  from the man page and persisting them to a file to speed future startups.
  """
  
  # It is important that this is loaded before entering the curses context,
  # otherwise the man call pegs the cpu for around a minute (I'm not sure
  # why... curses must mess the terminal in a way that's important to man).
  
  if CONFIG["features.config.descriptions.enabled"]:
    isConfigDescriptionsLoaded = False
    
    # determines the path where cached descriptions should be persisted (left
    # undefined of arm caching is disabled)
    cachePath, descriptorPath = CONFIG["data.cache.path"], None
    
    if cachePath:
      if not cachePath.endswith("/"): cachePath += "/"
      descriptorPath = os.path.expanduser(cachePath) + CONFIG_DESC_FILENAME
    
    # attempts to load persisted configuration descriptions
    if descriptorPath:
      try:
        loadStartTime = time.time()
        util.torConfig.loadOptionDescriptions(descriptorPath)
        isConfigDescriptionsLoaded = True
        
        msg = DESC_LOAD_SUCCESS_MSG % (descriptorPath, time.time() - loadStartTime)
        util.log.log(CONFIG["log.configDescriptions.persistance.loadSuccess"], msg)
      except IOError, exc:
        msg = DESC_LOAD_FAILED_MSG % util.sysTools.getFileErrorMsg(exc)
        util.log.log(CONFIG["log.configDescriptions.persistance.loadFailed"], msg)
    
    if not isConfigDescriptionsLoaded:
      try:
        # fetches configuration options from the man page
        loadStartTime = time.time()
        util.torConfig.loadOptionDescriptions()
        isConfigDescriptionsLoaded = True
        
        msg = DESC_READ_MAN_SUCCESS_MSG % (time.time() - loadStartTime)
        util.log.log(CONFIG["log.configDescriptions.readManPageSuccess"], msg)
      except IOError, exc:
        msg = DESC_READ_MAN_FAILED_MSG % util.sysTools.getFileErrorMsg(exc)
        util.log.log(CONFIG["log.configDescriptions.readManPageFailed"], msg)
      
      # persists configuration descriptions 
      if isConfigDescriptionsLoaded and descriptorPath:
        try:
          loadStartTime = time.time()
          util.torConfig.saveOptionDescriptions(descriptorPath)
          
          msg = DESC_SAVE_SUCCESS_MSG % (descriptorPath, time.time() - loadStartTime)
          util.log.log(CONFIG["log.configDescriptions.persistance.loadSuccess"], msg)
        except IOError, exc:
          msg = DESC_SAVE_FAILED_MSG % util.sysTools.getFileErrorMsg(exc)
          util.log.log(CONFIG["log.configDescriptions.persistance.saveFailed"], msg)

if __name__ == '__main__':
  startTime = time.time()
  param = dict([(key, None) for key in CONFIG.keys()])
  configPath = DEFAULT_CONFIG # path used for customized configuration
  
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
  try:
    pathPrefix = os.path.dirname(sys.argv[0])
    if pathPrefix and not pathPrefix.endswith("/"):
      pathPrefix = pathPrefix + "/"
    
    config.load("%ssettings.cfg" % pathPrefix)
  except IOError, exc:
    msg = NO_INTERNAL_CFG_MSG % util.sysTools.getFileErrorMsg(exc)
    util.log.log(util.log.WARN, msg)
  
  # loads user's personal armrc if available
  if os.path.exists(configPath):
    try:
      config.load(configPath)
    except IOError, exc:
      msg = STANDARD_CFG_LOAD_FAILED_MSG % util.sysTools.getFileErrorMsg(exc)
      util.log.log(util.log.WARN, msg)
  else:
    # no armrc found, falling back to the defaults in the source
    msg = STANDARD_CFG_NOT_FOUND_MSG % configPath
    util.log.log(util.log.NOTICE, msg)
  
  # revises defaults to match user's configuration
  config.update(CONFIG)
  
  # loads user preferences for utilities
  for utilModule in (util.conf, util.connections, util.hostnames, util.log, util.panel, util.procTools, util.sysTools, util.torConfig, util.torTools, util.uiTools):
    utilModule.loadConfig(config)
  
  # overwrites undefined parameters with defaults
  for key in param.keys():
    if param[key] == None: param[key] = CONFIG[key]
  
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
  authPassword = config.get("startup.controlPassword", CONFIG["startup.controlPassword"])
  conn = TorCtl.TorCtl.connect(controlAddr, controlPort, authPassword)
  if conn == None: sys.exit(1)
  
  # removing references to the controller password so the memory can be freed
  # (unfortunately python does allow for direct access to the memory so this
  # is the best we can do)
  del authPassword
  if "startup.controlPassword" in config.contents:
    del config.contents["startup.controlPassword"]
    
    pwLineNum = None
    for i in range(len(config.rawContents)):
      if config.rawContents[i].strip().startswith("startup.controlPassword"):
        pwLineNum = i
        break
    
    if pwLineNum != None:
      del config.rawContents[i]
  
  # initializing the connection may require user input (for the password)
  # skewing the startup time results so this isn't counted
  initTime = time.time() - startTime
  controller = util.torTools.getConn()
  controller.init(conn)
  
  # fetches descriptions for tor's configuration options
  _loadConfigurationDescriptions()
  
  interface.controller.startTorMonitor(time.time() - initTime, expandedEvents, param["startup.blindModeEnabled"])
  conn.close()

