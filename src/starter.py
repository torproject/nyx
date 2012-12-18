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
import getpass
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

from stem.control import Controller
import stem.connection

LOG_DUMP_PATH = os.path.expanduser("~/.arm/log")
DEFAULT_CONFIG = os.path.expanduser("~/.arm/armrc")
CONFIG = {"startup.controlPassword": None,
          "startup.interface.ipAddress": "127.0.0.1",
          "startup.interface.port": 9051,
          "startup.interface.socket": "/var/run/tor/control",
          "startup.blindModeEnabled": False,
          "startup.events": "N3",
          "startup.dataDirectory": "~/.arm",
          "wizard.default": {},
          "features.allowDetachedStartup": True,
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

OPT = "gi:s:c:dbe:vh"
OPT_EXPANDED = ["interface=", "socket=", "config=", "debug", "blind", "event=", "version", "help"]

HELP_MSG = """Usage arm [OPTION]
Terminal status monitor for Tor relays.

  -p, --prompt                    only start the control interpretor
  -i, --interface [ADDRESS:]PORT  change control interface from %s:%i
  -s, --socket SOCKET_PATH        attach using unix domain socket if present,
                                    SOCKET_PATH defaults to: %s
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
""" % (CONFIG["startup.interface.ipAddress"], CONFIG["startup.interface.port"], CONFIG["startup.interface.socket"], DEFAULT_CONFIG, LOG_DUMP_PATH, CONFIG["startup.events"], cli.logPanel.EVENT_LISTING)

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
STANDARD_CFG_NOT_FOUND_MSG = "No armrc loaded, using defaults. You can customize arm by placing a configuration file at '%s' (see the armrc.sample for its options)."

# torrc entries that are scrubbed when dumping
PRIVATE_TORRC_ENTRIES = ["HashedControlPassword", "Bridge", "HiddenServiceDir"]

# notices given if the user is running arm or tor as root
TOR_ROOT_NOTICE = "Tor is currently running with root permissions. This is not a good idea and shouldn't be necessary. See the 'User UID' option from Tor's man page for an easy method of reducing its permissions after startup."
ARM_ROOT_NOTICE = "Arm is currently running with root permissions. This is not a good idea, and will still work perfectly well if it's run with the same user as Tor (ie, starting with \"sudo -u %s arm\")."

# Makes subcommands provide us with English results (this is important so we
# can properly parse it).

os.putenv("LANG", "C")

def allowConnectionTypes():
  """
  This provides a tuple with booleans indicating if we should or shouldn't
  attempt to connect by various methods...
  (allowPortConnection, allowSocketConnection, allowDetachedStart)
  """
  
  confKeys = util.conf.getConfig("arm").getKeys()
  
  isPortArgPresent = "startup.interface.ipAddress" in confKeys or "startup.interface.port" in confKeys
  isSocketArgPresent = "startup.interface.socket" in confKeys
  
  skipPortConnection = isSocketArgPresent and not isPortArgPresent
  skipSocketConnection = isPortArgPresent and not isSocketArgPresent
  
  # Flag to indicate if we'll start arm reguardless of being unable to connect
  # to Tor. This is the default behavior if the user hasn't provided a port or
  # socket to connect to, so we can show the relay setup wizard.
  
  allowDetachedStart = CONFIG["features.allowDetachedStartup"] and not isPortArgPresent and not isSocketArgPresent
  
  return (not skipPortConnection, not skipSocketConnection, allowDetachedStart)

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
        loadedVersion = util.torConfig.loadOptionDescriptions("%sresources/%s" % (pathPrefix, CONFIG_DESC_FILENAME), False)
        isConfigDescriptionsLoaded = True
        
        msg = DESC_INTERNAL_LOAD_SUCCESS_MSG % loadedVersion
        util.log.log(CONFIG["log.configDescriptions.internalLoadSuccess"], msg)
      except IOError, exc:
        msg = DESC_INTERNAL_LOAD_FAILED_MSG % util.sysTools.getFileErrorMsg(exc)
        util.log.log(CONFIG["log.configDescriptions.internalLoadFailed"], msg)

def _getController(controlAddr="127.0.0.1", controlPort=9051, passphrase=None, incorrectPasswordMsg="", printError=True):
  """
  Custom handler for establishing a stem connection (... needs an overhaul).
  """
  
  controller = None
  try:
    chroot = util.torTools.getConn().getPathPrefix()
    controller = Controller.from_port(controlAddr, controlPort)
    
    try:
      controller.authenticate(password = passphrase, chroot_path = chroot)
    except stem.connection.MissingPassword:
      try:
        passphrase = getpass.getpass("Controller password: ")
        controller.authenticate(password = passphrase, chroot_path = chroot)
      except:
        return None
    
    return controller
  except Exception, exc:
    if controller: controller.close()
    
    # attempts to connect with the default wizard address too
    wizardPort = CONFIG["wizard.default"].get("Control")
    
    if wizardPort and wizardPort.isdigit():
      wizardPort = int(wizardPort)
      
      # Attempt to connect to the wizard port. If the connection fails then
      # don't print anything and continue with the error case for the initial
      # connection failure. Otherwise, return the connection result.
      
      if controlPort != wizardPort:
        controller = _getController(controlAddr, wizardPort)
        if controller != None: return controller
      else: return None # wizard connection attempt, don't print anything
    
    if passphrase and str(exc) == "Unable to authenticate: password incorrect":
      # provide a warning that the provided password didn't work, then try
      # again prompting for the user to enter it
      print incorrectPasswordMsg
      return _getController(controlAddr, controlPort)
    elif printError:
      print exc
    
    return None

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
  torConfigEntry = "Tor (%s) Configuration:\n" % conn.getInfo("version", None)
  
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
    elif opt in ("-s", "--socket"):
      param["startup.interface.socket"] = arg
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
  
  # prevent arm from starting without a tor instance if...
  # - we're launching a prompt
  # - tor is running (otherwise it would be kinda confusing, "tor is running
  #   but why does arm say that it's shut down?")
  
  if util.torTools.isTorRunning():
    config.set("features.allowDetachedStartup", "false")
  
  # revises defaults to match user's configuration
  config.update(CONFIG)
  
  # loads user preferences for utilities
  for utilModule in (util.conf, util.connections, util.hostnames, util.log, util.panel, util.procTools, util.sysTools, util.torConfig, util.torTools, util.uiTools):
    utilModule.loadConfig(config)
  
  # syncs config and parameters, saving changed config options and overwriting
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
  
  # By default attempts to connect using the control socket if it exists. This
  # skips attempting to connect by socket or port if the user has given
  # arguments for connecting to the other.
  
  controller = None
  allowPortConnection, allowSocketConnection, allowDetachedStart = allowConnectionTypes()
  
  socketPath = param["startup.interface.socket"]
  if os.path.exists(socketPath) and allowSocketConnection:
    try:
      # TODO: um... what about passwords?
      # https://trac.torproject.org/6881
      
      controller = Controller.from_socket_file(socketPath)
      controller.authenticate()
    except IOError, exc:
      if not allowPortConnection:
        print "Unable to use socket '%s': %s" % (socketPath, exc)
  elif not allowPortConnection:
    print "Socket '%s' doesn't exist" % socketPath
  
  if not controller and allowPortConnection:
    # sets up stem connection, prompting for the passphrase if necessary and
    # sending problems to stdout if they arise
    authPassword = config.get("startup.controlPassword", CONFIG["startup.controlPassword"])
    incorrectPasswordMsg = "Password found in '%s' was incorrect" % configPath
    controller = _getController(controlAddr, controlPort, authPassword, incorrectPasswordMsg, not allowDetachedStart)
    
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
  
  if controller is None and not allowDetachedStart: sys.exit(1)
  
  # initializing the connection may require user input (for the password)
  # skewing the startup time results so this isn't counted
  initTime = time.time() - startTime
  controllerWrapper = util.torTools.getConn()
  
  torUser = None
  if controller:
    controllerWrapper.init(controller)
    
    # give a notice if tor is running with root
    torUser = controllerWrapper.getMyUser()
    if torUser == "root":
      util.log.log(util.log.NOTICE, TOR_ROOT_NOTICE)
  
  # Give a notice if arm is running with root. Querying connections usually
  # requires us to have the same permissions as tor so if tor is running as
  # root then drop this notice (they're already then being warned about tor
  # being root, anyway).
  
  if torUser != "root" and os.getuid() == 0:
    torUserLabel = torUser if torUser else "<tor user>"
    util.log.log(util.log.NOTICE, ARM_ROOT_NOTICE % torUserLabel)
  
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

