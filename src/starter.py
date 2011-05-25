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
import locale
import platform

import version
import cli.controller
import cli.logPanel
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

LOG_DUMP_PATH = os.path.expanduser("~/.arm/log")
DEFAULT_CONFIG = os.path.expanduser("~/.arm/armrc")
CONFIG = {"startup.controlPassword": None,
          "startup.interface.ipAddress": "127.0.0.1",
          "startup.interface.port": 9051,
          "startup.blindModeEnabled": False,
          "startup.events": "N3",
          "startup.dataDirectory": "~/.arm",
          "features.config.descriptions.enabled": True,
          "features.config.descriptions.persist": True,
          "log.configDescriptions.readManPageSuccess": util.log.INFO,
          "log.configDescriptions.readManPageFailed": util.log.NOTICE,
          "log.configDescriptions.internalLoadSuccess": util.log.NOTICE,
          "log.configDescriptions.internalLoadFailed": util.log.ERR,
          "log.configDescriptions.persistance.loadSuccess": util.log.INFO,
          "log.configDescriptions.persistance.loadFailed": util.log.INFO,
          "log.configDescriptions.persistance.saveSuccess": util.log.INFO,
          "log.configDescriptions.persistance.saveFailed": util.log.NOTICE,
          "log.savingDebugLog": util.log.NOTICE}

OPT = "i:c:dbe:vh"
OPT_EXPANDED = ["interface=", "config=", "debug", "blind", "event=", "version", "help"]
HELP_MSG = """Usage arm [OPTION]
Terminal status monitor for Tor relays.

  -i, --interface [ADDRESS:]PORT  change control interface from %s:%i
  -c, --config CONFIG_PATH        loaded configuration options, CONFIG_PATH
                                    defaults to: %s
  -d, --debug                     writes all arm logs to %s
  -b, --blind                     disable connection lookups
  -e, --event EVENT_FLAGS         event types in message log  (default: %s)
%s
  -v, --version                   provides version information
  -h, --help                      presents this help

Example:
arm -b -i 1643          hide connection data, attaching to control port 1643
arm -e we -c /tmp/cfg   use this configuration file with 'WARN'/'ERR' events
""" % (CONFIG["startup.interface.ipAddress"], CONFIG["startup.interface.port"], DEFAULT_CONFIG, LOG_DUMP_PATH, CONFIG["startup.events"], cli.logPanel.EVENT_LISTING)

# filename used for cached tor config descriptions
CONFIG_DESC_FILENAME = "torConfigDesc.txt"

# messages related to loading the tor configuration descriptions
DESC_LOAD_SUCCESS_MSG = "Loaded configuration descriptions from '%s' (runtime: %0.3f)"
DESC_LOAD_FAILED_MSG = "Unable to load configuration descriptions (%s)"
DESC_INTERNAL_LOAD_SUCCESS_MSG = "Falling back to descriptions for Tor %s"
DESC_INTERNAL_LOAD_FAILED_MSG = "Unable to load fallback descriptions. Categories and help for Tor's configuration options won't be available. (%s)"
DESC_READ_MAN_SUCCESS_MSG = "Read descriptions for tor's configuration options from its man page (runtime %0.3f)"
DESC_READ_MAN_FAILED_MSG = "Unable to get the descriptions of Tor's configuration options from its man page (%s)"
DESC_SAVE_SUCCESS_MSG = "Saved configuration descriptions to '%s' (runtime: %0.3f)"
DESC_SAVE_FAILED_MSG = "Unable to save configuration descriptions (%s)"

NO_INTERNAL_CFG_MSG = "Failed to load the parsing configuration. This will be problematic for a few things like torrc validation and log duplication detection (%s)"
STANDARD_CFG_LOAD_FAILED_MSG = "Failed to load configuration (using defaults): \"%s\""
STANDARD_CFG_NOT_FOUND_MSG = "No configuration found at '%s', using defaults"

# torrc entries that are scrubbed when dumping
PRIVATE_TORRC_ENTRIES = ["HashedControlPassword", "Bridge", "HiddenServiceDir"]

def _loadConfigurationDescriptions(pathPrefix):
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
    # undefined if caching is disabled)
    descriptorPath = None
    if CONFIG["features.config.descriptions.persist"]:
      dataDir = CONFIG["startup.dataDirectory"]
      if not dataDir.endswith("/"): dataDir += "/"
      
      descriptorPath = os.path.expanduser(dataDir + "cache/") + CONFIG_DESC_FILENAME
    
    # attempts to load configuration descriptions cached in the data directory
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
    
    # fetches configuration options from the man page
    if not isConfigDescriptionsLoaded:
      try:
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
        except (IOError, OSError), exc:
          msg = DESC_SAVE_FAILED_MSG % util.sysTools.getFileErrorMsg(exc)
          util.log.log(CONFIG["log.configDescriptions.persistance.saveFailed"], msg)
    
    # finally fall back to the cached descriptors provided with arm (this is
    # often the case for tbb and manual builds)
    if not isConfigDescriptionsLoaded:
      try:
        loadStartTime = time.time()
        loadedVersion = util.torConfig.loadOptionDescriptions(pathPrefix + CONFIG_DESC_FILENAME, False)
        isConfigDescriptionsLoaded = True
        
        msg = DESC_INTERNAL_LOAD_SUCCESS_MSG % loadedVersion
        util.log.log(CONFIG["log.configDescriptions.internalLoadSuccess"], msg)
      except IOError, exc:
        msg = DESC_INTERNAL_LOAD_FAILED_MSG % util.sysTools.getFileErrorMsg(exc)
        util.log.log(CONFIG["log.configDescriptions.internalLoadFailed"], msg)

def _dumpConfig():
  """
  Dumps the current arm and tor configurations at the DEBUG runlevel. This
  attempts to scrub private information, but naturally the user should double
  check that I didn't miss anything.
  """
  
  config = util.conf.getConfig("arm")
  conn = util.torTools.getConn()
  
  # dumps arm's configuration
  armConfigEntry = ""
  armConfigKeys = list(config.getKeys())
  armConfigKeys.sort()
  
  for configKey in armConfigKeys:
    # Skips some config entries that are loaded by default. This fetches
    # the config values directly to avoid misflagging them as being used by
    # arm.
    
    if not configKey.startswith("config.summary.") and not configKey.startswith("torrc.") and not configKey.startswith("msg."):
      armConfigEntry += "%s -> %s\n" % (configKey, config.contents[configKey])
  
  if armConfigEntry: armConfigEntry = "Arm Configuration:\n%s" % armConfigEntry
  else: armConfigEntry = "Arm Configuration: None"
  
  # dumps tor's version and configuration
  torConfigEntry = "Tor (%s) Configuration:\n" % conn.getInfo("version")
  
  for line in conn.getInfo("config-text", "").split("\n"):
    if not line: continue
    elif " " in line: key, value = line.split(" ", 1)
    else: key, value = line, ""
    
    if key in PRIVATE_TORRC_ENTRIES:
      torConfigEntry += "%s <scrubbed>\n" % key
    else:
      torConfigEntry += "%s %s\n" % (key, value)
  
  util.log.log(util.log.DEBUG, armConfigEntry.strip())
  util.log.log(util.log.DEBUG, torConfigEntry.strip())

if __name__ == '__main__':
  startTime = time.time()
  param = dict([(key, None) for key in CONFIG.keys()])
  isDebugMode = False
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
    elif opt in ("-d", "--debug"): isDebugMode = True # dumps all logs
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
  
  if isDebugMode:
    try:
      util.log.setDumpFile(LOG_DUMP_PATH)
      
      currentTime = time.localtime()
      timeLabel = time.strftime("%H:%M:%S %m/%d/%Y (%Z)", currentTime)
      initMsg = "Arm %s Debug Dump, %s" % (version.VERSION, timeLabel)
      pythonVersionLabel = "Python Version: %s" % (".".join([str(arg) for arg in sys.version_info[:3]]))
      osLabel = "Platform: %s (%s)" % (platform.system(), " ".join(platform.dist()))
      
      util.log.DUMP_FILE.write("%s\n%s\n%s\n%s\n" % (initMsg, pythonVersionLabel, osLabel, "-" * 80))
      util.log.DUMP_FILE.flush()
    except (OSError, IOError), exc:
      print "Unable to write to debug log file: %s" % util.sysTools.getFileErrorMsg(exc)
  
  config = util.conf.getConfig("arm")
  
  # attempts to fetch attributes for parsing tor's logs, configuration, etc
  pathPrefix = os.path.dirname(sys.argv[0])
  if pathPrefix and not pathPrefix.endswith("/"):
    pathPrefix = pathPrefix + "/"
  
  try:
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
  
  # snycs config and parameters, saving changed config options and overwriting
  # undefined parameters with defaults
  for key in param.keys():
    if param[key] == None: param[key] = CONFIG[key]
    else: config.set(key, str(param[key]))
  
  # validates that input has a valid ip address and port
  controlAddr = param["startup.interface.ipAddress"]
  controlPort = param["startup.interface.port"]
  
  if not util.connections.isValidIpAddress(controlAddr):
    print "'%s' isn't a valid IP address" % controlAddr
    sys.exit()
  elif controlPort < 0 or controlPort > 65535:
    print "'%s' isn't a valid port number (ports range 0-65535)" % controlPort
    sys.exit()
  
  # validates and expands log event flags
  try:
    cli.logPanel.expandEvents(param["startup.events"])
  except ValueError, exc:
    for flag in str(exc):
      print "Unrecognized event flag: %s" % flag
    sys.exit()
  
  # temporarily disables TorCtl logging to prevent issues from going to stdout while starting
  TorCtl.TorUtil.loglevel = "NONE"
  
  # sets up TorCtl connection, prompting for the passphrase if necessary and
  # sending problems to stdout if they arise
  TorCtl.TorCtl.INCORRECT_PASSWORD_MSG = "Controller password found in '%s' was incorrect" % configPath
  authPassword = config.get("startup.controlPassword", CONFIG["startup.controlPassword"])
  conn = TorCtl.TorCtl.connect(controlAddr, controlPort, authPassword)
  if conn == None:
    # Connecting to the control port will probably fail if it's using cookie
    # authentication and the cookie path is relative (unfortunately this is
    # the case for TBB). This is discussed in:
    # https://trac.torproject.org/projects/tor/ticket/1101
    #
    # Until this is fixed including a hack to expand the relative path in
    # these cases, setting conn to the established connection if successful
    # and leaving it undefined otherwise. Even if successful this prints the
    # error message saying that the auth cookie couldn't be found
    # (unfortunately this is unavoidable without either changing TorCtl or
    # making this a much bigger hack).
    
    try:
      tmpConn, authType, cookiePath = util.torTools.getConnectionComponents(controlAddr, controlPort)
      
      if authType == util.torTools.AUTH_TYPE.COOKIE:
        torPid = util.torTools.getPid(controlPort)
        
        if torPid and cookiePath[0] != "/":
          # previous attempt to connect failed due to having a relative path - fix it
          tmpConn._cookiePath = util.sysTools.expandRelativePath(cookiePath, torPid)
          tmpConn.authenticate(cookiePath)
          conn = tmpConn # success!
    except: pass
    
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
  _loadConfigurationDescriptions(pathPrefix)
  
  # dump tor and arm configuration when in debug mode
  if isDebugMode:
    util.log.log(CONFIG["log.savingDebugLog"], "Saving a debug log to '%s' (please check it for sensitive information before sharing)" % LOG_DUMP_PATH)
    _dumpConfig()
  
  # Attempts to rename our process from "python setup.py <input args>" to
  # "arm <input args>"
  
  try:
    from util import procName
    procName.renameProcess("arm\0%s" % "\0".join(sys.argv[1:]))
  except: pass
  
  # If using our LANG variable for rendering multi-byte characters lets us
  # get unicode support then then use it. This needs to be done before
  # initializing curses.
  if util.uiTools.isUnicodeAvailable():
    locale.setlocale(locale.LC_ALL, "")
  
  cli.controller.startTorMonitor(time.time() - initTime)

