"""
Provides user prompts for setting up a new relay. This autogenerates a torrc
that's used by arm to run its own tor instance.
"""

import re
import os
import sys
import random
import shutil
import getpass
import platform
import functools
import curses

import cli.popups
import cli.controller

from util import connections, enum, log, sysTools, torConfig, torTools, uiTools

# template used to generate the torrc
TORRC_TEMPLATE = "resources/torrcTemplate.txt"

# basic configuration types we can run as
RelayType = enum.Enum("RESUME", "RELAY", "EXIT", "BRIDGE", "CLIENT")

# all options that can be configured
Options = enum.Enum("DIVIDER", "CONTROL", "NICKNAME", "CONTACT", "NOTIFY", "BANDWIDTH", "LIMIT", "CLIENT", "LOWPORTS", "PORTFORWARD", "SYSTEM", "STARTUP", "RSHUTDOWN", "CSHUTDOWN", "NOTICE", "POLICY", "WEBSITES", "EMAIL", "IM", "MISC", "PLAINTEXT", "DISTRIBUTE", "BRIDGED", "BRIDGE1", "BRIDGE2", "BRIDGE3", "REUSE")
RelayOptions = {RelayType.RELAY:   (Options.NICKNAME,
                                    Options.CONTACT,
                                    Options.NOTIFY,
                                    Options.BANDWIDTH,
                                    Options.LIMIT,
                                    Options.CLIENT,
                                    Options.LOWPORTS,
                                    Options.PORTFORWARD,
                                    Options.STARTUP,
                                    Options.RSHUTDOWN,
                                    Options.SYSTEM),
                RelayType.EXIT:    (Options.NICKNAME,
                                    Options.CONTACT,
                                    Options.NOTIFY,
                                    Options.BANDWIDTH,
                                    Options.LIMIT,
                                    Options.CLIENT,
                                    Options.LOWPORTS,
                                    Options.PORTFORWARD,
                                    Options.STARTUP,
                                    Options.RSHUTDOWN,
                                    Options.SYSTEM,
                                    Options.DIVIDER,
                                    Options.NOTICE,
                                    Options.POLICY,
                                    Options.WEBSITES,
                                    Options.EMAIL,
                                    Options.IM,
                                    Options.MISC,
                                    Options.PLAINTEXT),
                RelayType.BRIDGE:  (Options.DISTRIBUTE,
                                    Options.BANDWIDTH,
                                    Options.LIMIT,
                                    Options.CLIENT,
                                    Options.LOWPORTS,
                                    Options.PORTFORWARD,
                                    Options.STARTUP,
                                    Options.RSHUTDOWN,
                                    Options.SYSTEM),
                RelayType.CLIENT:  (Options.BRIDGED,
                                    Options.BRIDGE1,
                                    Options.BRIDGE2,
                                    Options.BRIDGE3,
                                    Options.REUSE,
                                    Options.CSHUTDOWN,
                                    Options.SYSTEM)}

# option sets
CUSTOM_POLICIES = (Options.WEBSITES, Options.EMAIL, Options.IM, Options.MISC, Options.PLAINTEXT)
BRIDGE_ENTRIES = (Options.BRIDGE1, Options.BRIDGE2, Options.BRIDGE3)

# other options provided in the prompts
CANCEL, NEXT, BACK = "Cancel", "Next", "Back"

DESC_SIZE = 5 # height of the description field
MSG_COLOR = "green"
OPTION_COLOR = "yellow"
DISABLED_COLOR = "cyan"

# bracketing pairs used in email address obscuring
BRACKETS = ((' ', ' '),
            ('<', '>'),
            ('[', ']'),
            ('(', ')'),
            ('{', '}'),
            ('|', '|'))

# version requirements for options
VERSION_REQUIREMENTS = {Options.PORTFORWARD: "0.2.3.1-alpha"}

# tor's defaults for config options, used to filter unneeded options
TOR_DEFAULTS = {Options.BANDWIDTH: "5 MB",
                Options.REUSE: "10 minutes"}

# path for the torrc to be placed if replacing the torrc for the system wide
# tor instance
SYSTEM_DROP_PATH = "/var/lib/tor-arm/torrc"
OVERRIDE_SCRIPT = "/usr/share/arm/resources/torrcOverride/override.py"
OVERRIDE_SETUID_SCRIPT = "/usr/bin/torrc-override"

CONFIG = {"wizard.message.role": "",
          "wizard.message.relay": "",
          "wizard.message.exit": "",
          "wizard.message.bridge": "",
          "wizard.message.client": "",
          "wizard.toggle": {},
          "wizard.disabled": [],
          "wizard.suboptions": [],
          "wizard.default": {},
          "wizard.blankValue": {},
          "wizard.label.general": {},
          "wizard.label.role": {},
          "wizard.label.opt": {},
          "wizard.description.general": {},
          "wizard.description.role": {},
          "wizard.description.opt": {},
          "port.category": {},
          "port.exit.all": [],
          "port.exit.web": [],
          "port.exit.mail": [],
          "port.exit.im": [],
          "port.exit.misc": [],
          "port.encrypted": []}

def loadConfig(config):
  config.update(CONFIG)

class ConfigOption:
  """
  Attributes of a configuraition option.
  """
  
  def __init__(self, key, group, default):
    """
    Configuration option constructor.
    
    Arguments:
      key     - configuration option identifier used when querying attributes
      group   - configuration attribute group this belongs to
      default - initial value, uses the config default if unset
    """
    
    self.key = key
    self.group = group
    self.descriptionCache = None
    self.descriptionCacheArg = None
    self.value = default
    self.validator = None
    self._isEnabled = True
  
  def getKey(self):
    return self.key
  
  def getValue(self):
    return self.value
  
  def getDisplayValue(self):
    if not self.value and self.key in CONFIG["wizard.blankValue"]:
      return CONFIG["wizard.blankValue"][self.key]
    else: return self.value
  
  def getDisplayAttr(self):
    myColor = OPTION_COLOR if self.isEnabled() else DISABLED_COLOR
    return curses.A_BOLD | uiTools.getColor(myColor)
  
  def isEnabled(self):
    return self._isEnabled
  
  def setEnabled(self, isEnabled):
    self._isEnabled = isEnabled
  
  def setValidator(self, validator):
    """
    Custom function used to check that a value is valid before setting it.
    This functor should accept two arguments: this option and the value we're
    attempting to set. If its invalid then a ValueError with the reason is
    expected.
    
    Arguments:
      validator - functor for checking the validitiy of values we set
    """
    
    self.validator = validator
  
  def setValue(self, value):
    """
    Attempts to set our value. If a validator has been set then we first check
    if it's alright, raising a ValueError with the reason if not.
    
    Arguments:
      value - value we're attempting to set
    """
    
    if self.validator: self.validator(self, value)
    self.value = value
  
  def getLabel(self, prefix = ""):
    return prefix + CONFIG["wizard.label.%s" % self.group].get(self.key, "")
  
  def getDescription(self, width, prefix = ""):
    if not self.descriptionCache or self.descriptionCacheArg != width:
      optDescription = CONFIG["wizard.description.%s" % self.group].get(self.key, "")
      self.descriptionCache = _splitStr(optDescription, width)
      self.descriptionCacheArg = width
    
    return [prefix + line for line in self.descriptionCache]

class ToggleConfigOption(ConfigOption):
  """
  Configuration option representing a boolean.
  """
  
  def __init__(self, key, group, default, trueLabel, falseLabel):
    ConfigOption.__init__(self, key, group, default)
    self.trueLabel = trueLabel
    self.falseLabel = falseLabel
  
  def getDisplayValue(self):
    return self.trueLabel if self.value else self.falseLabel
  
  def toggle(self):
    # This isn't really here to validate the value (after all this is a
    # boolean, the options are limited!), but rather give a method for functors
    # to be triggered when selected.
    
    if self.validator: self.validator(self, not self.value)
    self.value = not self.value

def showWizard():
  """
  Provides a series of prompts, allowing the user to spawn a customized tor
  instance.
  """
  
  if not sysTools.isAvailable("tor"):
    msg = "Unable to run the setup wizard. Is tor installed?"
    log.log(log.WARN, msg)
    return
  
  # gets tor's version
  torVersion = None
  try:
    versionQuery = sysTools.call("tor --version")
    
    for line in versionQuery:
      if line.startswith("Tor version "):
        torVersion = torTools.parseVersion(line.split(" ")[2])
        break
  except IOError, exc:
    log.log(log.INFO, "'tor --version' query failed: %s" % exc)
  
  relayType, config = None, {}
  for option in Options.values():
    if option == Options.DIVIDER:
      config[option] = option
      continue
    
    toggleValues = CONFIG["wizard.toggle"].get(option)
    default = CONFIG["wizard.default"].get(option, "")
    
    if toggleValues:
      if "," in toggleValues:
        trueLabel, falseLabel = toggleValues.split(",", 1)
      else: trueLabel, falseLabel = toggleValues, ""
      
      isSet = default.lower() == "true"
      config[option] = ToggleConfigOption(option, "opt", isSet, trueLabel.strip(), falseLabel.strip())
    else: config[option] = ConfigOption(option, "opt", default)
  
  # sets input validators
  config[Options.BANDWIDTH].setValidator(_relayRateValidator)
  config[Options.LIMIT].setValidator(_monthlyLimitValidator)
  config[Options.BRIDGE1].setValidator(_bridgeDestinationValidator)
  config[Options.BRIDGE2].setValidator(_bridgeDestinationValidator)
  config[Options.BRIDGE3].setValidator(_bridgeDestinationValidator)
  config[Options.REUSE].setValidator(_circDurationValidator)
  
  # enables custom policies when 'custom' is selected and disables otherwise
  policyOpt = config[Options.POLICY]
  customPolicies = [config[opt] for opt in CUSTOM_POLICIES]
  policyOpt.setValidator(functools.partial(_toggleEnabledAction, customPolicies))
  _toggleEnabledAction(customPolicies, policyOpt, policyOpt.getValue())
  
  lowPortsOpt = config[Options.LOWPORTS]
  disclaimerNotice = [config[Options.NOTICE]]
  lowPortsOpt.setValidator(functools.partial(_toggleEnabledAction, disclaimerNotice))
  _toggleEnabledAction(disclaimerNotice, lowPortsOpt, lowPortsOpt.getValue())
  
  # enables bridge entries when "Use Bridges" is set and disables otherwise
  useBridgeOpt = config[Options.BRIDGED]
  bridgeEntries = [config[opt] for opt in BRIDGE_ENTRIES]
  useBridgeOpt.setValidator(functools.partial(_toggleEnabledAction, bridgeEntries))
  _toggleEnabledAction(bridgeEntries, useBridgeOpt, useBridgeOpt.getValue())
  
  # enables running at startup when 'Use System Instance' is deselected and
  # disables otherwise
  systemOpt = config[Options.SYSTEM]
  startupOpt = [config[Options.STARTUP]]
  systemOpt.setValidator(functools.partial(_toggleEnabledAction, startupOpt, True))
  _toggleEnabledAction(startupOpt, systemOpt, not systemOpt.getValue())
  
  # remembers the last selection made on the type prompt page
  controller = cli.controller.getController()
  manager = controller.getTorManager()
  relaySelection = RelayType.RESUME if manager.isTorrcAvailable() else RelayType.RELAY
  
  # excludes options that are either disabled or for a future tor version
  disabledOpt = list(CONFIG["wizard.disabled"])
  
  for opt, optVersion in VERSION_REQUIREMENTS.items():
    if not torVersion or not torTools.isVersion(torVersion, torTools.parseVersion(optVersion)):
      disabledOpt.append(opt)
  
  # the port forwarding option would only work if tor-fw-helper is in the path
  if not Options.PORTFORWARD in disabledOpt:
    if not sysTools.isAvailable("tor-fw-helper"):
      disabledOpt.append(Options.PORTFORWARD)
  
  # If we haven't run 'resources/torrcOverride/override.py --init' or lack
  # permissions then we aren't able to deal with the system wide tor instance.
  # Also drop the option if we aren't installed since override.py won't be at
  # the expected path.
  if not os.path.exists(os.path.dirname(SYSTEM_DROP_PATH)) or not os.path.exists(OVERRIDE_SCRIPT):
    disabledOpt.append(Options.SYSTEM)
  
  # TODO: The STARTUP option is currently disabled in the 'settings.cfg', and I
  # don't currently have plans to implement it (it would be a big pita, and the
  # tor deb already handles it). *If* it is implemented then I'd limit support
  # for the option to Debian and Ubuntu to start with, via the following...
  
  # Running at startup is currently only supported for Debian and Ubuntu.
  # Patches welcome for supporting other platforms.
  #if not platform.dist()[0] in ("debian", "Ubuntu"):
  #  disabledOpt.append(Options.STARTUP)
  
  while True:
    if relayType == None:
      selection = promptRelayType(relaySelection)
      
      if selection == CANCEL: break
      elif selection == RelayType.RESUME:
        if not manager.isManaged(torTools.getConn()):
          manager.startManagedInstance()
        
        break
      else: relayType, relaySelection = selection, selection
    else:
      selection = promptConfigOptions(relayType, config, disabledOpt)
      
      if selection == BACK: relayType = None
      elif selection == CANCEL: break
      elif selection == NEXT:
        generatedTorrc = getTorrc(relayType, config, disabledOpt)
        
        torrcLocation = manager.getTorrcPath()
        isSystemReplace = not Options.SYSTEM in disabledOpt and config[Options.SYSTEM].getValue()
        if isSystemReplace: torrcLocation = SYSTEM_DROP_PATH
        
        controller.redraw()
        confirmationSelection = showConfirmationDialog(generatedTorrc, torrcLocation)
        
        if confirmationSelection == NEXT:
          log.log(log.INFO, "Writing torrc to '%s':\n%s" % (torrcLocation, generatedTorrc))
          
          # if the torrc already exists then save it to a _bak file
          isBackedUp = False
          if os.path.exists(torrcLocation) and not isSystemReplace:
            try:
              shutil.copy(torrcLocation, torrcLocation + "_bak")
              isBackedUp = True
            except IOError, exc:
              log.log(log.WARN, "Unable to backup the torrc: %s" % exc)
          
          # writes the torrc contents
          try:
            torrcFile = open(torrcLocation, "w")
            torrcFile.write(generatedTorrc)
            torrcFile.close()
          except IOError, exc:
            log.log(log.ERR, "Unable to make torrc: %s" % exc)
            break
          
          # logs where we placed the torrc
          msg = "Tor configuration placed at '%s'" % torrcLocation
          if isBackedUp:
            msg += " (the previous torrc was moved to 'torrc_bak')"
          
          log.log(log.NOTICE, msg)
          
          dataDir = cli.controller.getController().getDataDirectory()
          
          pathPrefix = os.path.dirname(sys.argv[0])
          if pathPrefix and not pathPrefix.endswith("/"):
            pathPrefix = pathPrefix + "/"
          
          # copies exit notice into data directory if it's being used
          if Options.NOTICE in RelayOptions[relayType] and config[Options.NOTICE].getValue() and config[Options.LOWPORTS].getValue():
            src = "%sresources/exitNotice" % pathPrefix
            dst = "%sexitNotice" % dataDir
            
            if not os.path.exists(dst):
              shutil.copytree(src, dst)
            
            # providing a notice that it has sections specific to us operators
            msg = "Exit notice placed at '%s/index.html'. Some of the sections are specific to US relay operators so please change the \"FIXME\" sections if this is inappropriate." % dst
            log.log(log.NOTICE, msg)
          
          runCommand, exitCode = None, 1
          
          if isSystemReplace:
            # running override.py needs root so...
            # - if running as root (bad user, no biscuit!) then run it directly
            # - if the setuid binary is available at '/usr/bin/torrc-override'
            #   then use that
            # - attempt sudo in case passwordless sudo is available
            # - if all of the above fail then log instructions
            
            if os.geteuid() == 0: runCommand = OVERRIDE_SCRIPT
            elif os.path.exists(OVERRIDE_SETUID_SCRIPT): runCommand = OVERRIDE_SETUID_SCRIPT
            else:
              # The -n argument to sudo is *supposed* to be available starting
              # with 1.7.0 [1] however this is a dirty lie (Ubuntu 9.10 uses
              # 1.7.0 and even has the option in its man page, but it doesn't
              # work). Instead checking for version 1.7.1.
              #
              # [1] http://www.sudo.ws/pipermail/sudo-users/2009-January/003889.html
              
              sudoVersionResult = sysTools.call("sudo -V")
              
              # version output looks like "Sudo version 1.7.2p7"
              if len(sudoVersionResult) == 1 and sudoVersionResult[0].count(" ") >= 2:
                versionNum = 0
                
                for comp in sudoVersionResult[0].split(" ")[2].split("."):
                  if comp and comp[0].isdigit():
                    versionNum = (10 * versionNum) + int(comp)
                  else:
                    # invalid format
                    log.log(log.INFO, "Unrecognized sudo version string: %s" % sudoVersionResult[0])
                    versionNum = 0
                    break
                
                if versionNum >= 171:
                  runCommand = "sudo -n %s" % OVERRIDE_SCRIPT
                else:
                  log.log(log.INFO, "Insufficient sudo version for the -n argument")
            
            if runCommand: exitCode = os.system("%s > /dev/null 2>&1" % runCommand)
            
            if exitCode != 0:
              msg = "Tor needs root permissions to replace the system wide torrc. To continue...\n- open another terminal\n- run \"sudo %s\"\n- press 'x' here to tell tor to reload" % OVERRIDE_SCRIPT
              log.log(log.NOTICE, msg)
            else: torTools.getConn().reload()
          elif manager.isTorrcAvailable():
            # If we're connected to a managed instance then just need to
            # issue a sighup to pick up the new settings. Otherwise starts
            # a new tor instance.
            
            conn = torTools.getConn()
            if manager.isManaged(conn): conn.reload()
            else: manager.startManagedInstance()
          else:
            # If we don't have permissions to run the torrc we just made then
            # makes a shell script they can run as root to start tor.
            
            src = "%sresources/startTor" % pathPrefix
            dst = "%sstartTor" % dataDir
            if not os.path.exists(dst): shutil.copy(src, dst)
            
            msg = "Tor needs root permissions to start with this configuration (it will drop itself to the current user afterward). To continue...\n- open another terminal\n- run \"sudo %s\"\n- press 'r' here to tell arm to reconnect" % dst
            log.log(log.NOTICE, msg)
          
          break
        elif confirmationSelection == CANCEL: break
    
    # redraws screen to clear away the dialog we just showed
    cli.controller.getController().redraw()

def promptRelayType(initialSelection):
  """
  Provides a prompt for selecting the general role we'd like Tor to run with.
  This returns a RelayType enumeration for the selection, or CANCEL if the
  dialog was canceled.
  """
  
  options = [ConfigOption(opt, "role", opt) for opt in RelayType.values()]
  options.append(ConfigOption(CANCEL, "general", CANCEL))
  selection = RelayType.indexOf(initialSelection)
  height = 28
  
  # drops the resume option if it isn't applicable
  control = cli.controller.getController()
  if not control.getTorManager().isTorrcAvailable():
    options.pop(0)
    height -= 3
    selection -= 1
  
  popup, _, _ = cli.popups.init(height, 58)
  if not popup: return
  
  try:
    popup.win.box()
    curses.cbreak()
    
    # provides the welcoming message
    topContent = _splitStr(CONFIG["wizard.message.role"], 54)
    for i in range(len(topContent)):
      popup.addstr(i + 1, 2, topContent[i], curses.A_BOLD | uiTools.getColor(MSG_COLOR))
    
    while True:
      y, offset = len(topContent) + 1, 0
      
      for opt in options:
        optionFormat = uiTools.getColor(MSG_COLOR)
        if opt == options[selection]: optionFormat |= curses.A_STANDOUT
        
        # Curses has a weird bug where there's a one-pixel alignment
        # difference between bold and regular text, so it looks better
        # to render the whitespace here as not being bold.
        
        offset += 1
        label = opt.getLabel(" ")
        popup.addstr(y + offset, 2, label, optionFormat | curses.A_BOLD)
        popup.addstr(y + offset, 2 + len(label), " " * (54 - len(label)), optionFormat)
        offset += 1
        
        for line in opt.getDescription(52, " "):
          popup.addstr(y + offset, 2, uiTools.padStr(line, 54), optionFormat)
          offset += 1
      
      popup.win.refresh()
      key = control.getScreen().getch()
      
      if key == curses.KEY_UP: selection = (selection - 1) % len(options)
      elif key == curses.KEY_DOWN: selection = (selection + 1) % len(options)
      elif uiTools.isSelectionKey(key): return options[selection].getValue()
      elif key in (27, ord('q'), ord('Q')): return CANCEL # esc or q - cancel
  finally:
    cli.popups.finalize()

def promptConfigOptions(relayType, config, disabledOpt):
  """
  Prompts the user for the configuration of an internal relay.
  """
  
  topContent = _splitStr(CONFIG.get("wizard.message.%s" % relayType.lower(), ""), 54)
  
  options = [config[opt] for opt in RelayOptions[relayType] if not opt in disabledOpt]
  options.append(Options.DIVIDER)
  options.append(ConfigOption(BACK, "general", "(to role selection)"))
  options.append(ConfigOption(NEXT, "general", "(to confirm options)"))
  
  popupHeight = len(topContent) + len(options) + DESC_SIZE + 5
  popup, _, _ = cli.popups.init(popupHeight, 58)
  if not popup: return
  control = cli.controller.getController()
  key, selection = 0, 0
  
  try:
    curses.cbreak()
    
    while True:
      popup.win.erase()
      popup.win.box()
      
      # provides the description for the relay type
      for i in range(len(topContent)):
        popup.addstr(i + 1, 2, topContent[i], curses.A_BOLD | uiTools.getColor(MSG_COLOR))
      
      y, offset = len(topContent) + 1, 0
      for opt in options:
        if opt == Options.DIVIDER:
          offset += 1
          continue
        
        optionFormat = opt.getDisplayAttr()
        if opt == options[selection]: optionFormat |= curses.A_STANDOUT
        
        offset, indent = offset + 1, 0
        if opt.getKey() in CONFIG["wizard.suboptions"]:
          # If the next entry is also a suboption then show a 'T', otherwise
          # end the bracketing.
          
          bracketChar, nextIndex = curses.ACS_LLCORNER, options.index(opt) + 1
          if nextIndex < len(options) and isinstance(options[nextIndex], ConfigOption):
            if options[nextIndex].getKey() in CONFIG["wizard.suboptions"]:
              bracketChar = curses.ACS_LTEE
          
          popup.addch(y + offset, 3, bracketChar, opt.getDisplayAttr())
          popup.addch(y + offset, 4, curses.ACS_HLINE, opt.getDisplayAttr())
          
          indent = 3
        
        labelFormat = " %%-%is%%s" % (30 - indent)
        label = labelFormat % (opt.getLabel(), opt.getDisplayValue())
        popup.addstr(y + offset, 2 + indent, uiTools.padStr(label, 54 - indent), optionFormat)
        
        # little hack to make "Block" policies red
        if opt != options[selection] and not opt.getValue() and opt.getKey() in CUSTOM_POLICIES:
          optionFormat = curses.A_BOLD | uiTools.getColor("red")
          popup.addstr(y + offset, 33, opt.getDisplayValue(), optionFormat)
      
      # divider between the options and description
      offset += 2
      popup.addch(y + offset, 0, curses.ACS_LTEE)
      popup.addch(y + offset, popup.getWidth() - 1, curses.ACS_RTEE)
      popup.hline(y + offset, 1, popup.getWidth() - 2)
      
      # description for the currently selected option
      for line in options[selection].getDescription(54, " "):
        offset += 1
        popup.addstr(y + offset, 1, line, uiTools.getColor(MSG_COLOR))
      
      popup.win.refresh()
      key = control.getScreen().getch()
      
      if key in (curses.KEY_UP, curses.KEY_DOWN):
        posOffset = -1 if key == curses.KEY_UP else 1
        selection = (selection + posOffset) % len(options)
        
        # skips disabled options and dividers
        while options[selection] == Options.DIVIDER or not options[selection].isEnabled():
          selection = (selection + posOffset) % len(options)
      elif uiTools.isSelectionKey(key):
        if selection == len(options) - 2: return BACK # selected back
        elif selection == len(options) - 1: return NEXT # selected next
        elif isinstance(options[selection], ToggleConfigOption):
          options[selection].toggle()
        else:
          newValue = popup.getstr(y + selection + 1, 33, options[selection].getValue(), curses.A_STANDOUT | uiTools.getColor(OPTION_COLOR), 23)
          if newValue:
            try: options[selection].setValue(newValue.strip())
            except ValueError, exc:
              cli.popups.showMsg(str(exc), 3)
              cli.controller.getController().redraw()
      elif key in (27, ord('q'), ord('Q')): return CANCEL
  finally:
    cli.popups.finalize()

def getTorrc(relayType, config, disabledOpt):
  """
  Provides the torrc generated for the given options.
  """
  
  # TODO: When Robert's 'ownership' feature is available take advantage of it
  # for the RSHUTDOWN and CSHUTDOWN options.
  
  pathPrefix = os.path.dirname(sys.argv[0])
  if pathPrefix and not pathPrefix.endswith("/"):
    pathPrefix = pathPrefix + "/"
  
  templateFile = open("%s%s" % (pathPrefix, TORRC_TEMPLATE), "r")
  template = templateFile.readlines()
  templateFile.close()
  
  # generates the options the template expects
  templateOptions = {}
  
  for key, value in config.items():
    if isinstance(value, ConfigOption):
      value = value.getValue()
    
    if key == Options.BANDWIDTH and value.endswith("/s"):
      # truncates "/s" from the rate for RelayBandwidthRate entry
      value = value[:-2]
    elif key == Options.NOTICE:
      # notice option is only applied if using low ports
      value &= config[Options.LOWPORTS].getValue()
    elif key == Options.CONTACT and _isEmailAddress(value):
      # obscures the email address
      value = _obscureEmailAddress(value)
    
    templateOptions[key.upper()] = value
  
  templateOptions[relayType.upper()] = True
  templateOptions["LOW_PORTS"] = config[Options.LOWPORTS].getValue()
  
  # uses double the relay rate for bursts
  bwOpt = Options.BANDWIDTH.upper()
  
  if templateOptions[bwOpt] != TOR_DEFAULTS[Options.BANDWIDTH]:
    relayRateComp = templateOptions[bwOpt].split(" ")
    templateOptions["BURST"] = "%i %s" % (int(relayRateComp[0]) * 2, " ".join(relayRateComp[1:]))
  
  # paths for our tor related resources
  
  dataDir = cli.controller.getController().getDataDirectory()
  templateOptions["NOTICE_PATH"] = "%sexitNotice/index.html" % dataDir
  templateOptions["LOG_ENTRY"] = "notice file %stor_log" % dataDir
  templateOptions["USERNAME"] = getpass.getuser()
  
  # using custom data directory, unless this is for a system wide instance
  if not config[Options.SYSTEM].getValue() or Options.SYSTEM in disabledOpt:
    templateOptions["DATA_DIR"] = "%stor_data" % dataDir
  
  policyCategories = []
  if not config[Options.POLICY].getValue():
    policyCategories = ["web", "mail", "im", "misc"]
  else:
    if config[Options.WEBSITES].getValue(): policyCategories.append("web")
    if config[Options.EMAIL].getValue(): policyCategories.append("mail")
    if config[Options.IM].getValue(): policyCategories.append("im")
    if config[Options.MISC].getValue(): policyCategories.append("misc")
  
  # uses the CSHUTDOWN or RSHUTDOWN option based on if we're running as a
  # client or not
  if relayType == RelayType.CLIENT:
    templateOptions["SHUTDOWN"] = templateOptions[Options.CSHUTDOWN.upper()]
  else:
    templateOptions["SHUTDOWN"] = templateOptions[Options.RSHUTDOWN.upper()]
  
  if policyCategories:
    isEncryptedOnly = not config[Options.PLAINTEXT].getValue()
    
    policyLines = []
    for category in ["all"] + policyCategories:
      # shows a comment at the start of the section saying what it's for
      topicComment = CONFIG["port.category"].get(category)
      if topicComment:
        for topicComp in _splitStr(topicComment, 78):
          policyLines.append("# " + topicComp)
      
      for portEntry in CONFIG.get("port.exit.%s" % category, []):
        # port entry might be an individual port or a range
        
        if isEncryptedOnly and (not portEntry in CONFIG["port.encrypted"]):
          continue # opting to not include plaintext port and ranges
        
        if "-" in portEntry:
          # if this is a range then use the first port's description
          comment = connections.PORT_USAGE.get(portEntry[:portEntry.find("-")])
        else: comment = connections.PORT_USAGE.get(portEntry)
        
        entry = "ExitPolicy accept *:%s" % portEntry
        if comment: policyLines.append("%-30s# %s" % (entry, comment))
        else: policyLines.append(entry)
      
      if category != policyCategories[-1]:
        policyLines.append("") # newline to split categories
    
    templateOptions["EXIT_POLICY"] = "\n".join(policyLines)
  
  # includes input bridges
  bridgeLines = []
  for bridgeOpt in [Options.BRIDGE1, Options.BRIDGE2, Options.BRIDGE3]:
    bridgeValue = config[bridgeOpt].getValue()
    if bridgeValue: bridgeLines.append("Bridge %s" % bridgeValue)
  
  templateOptions["BRIDGES"] = "\n".join(bridgeLines)
  
  # removes disabled options
  for opt in disabledOpt:
    if opt.upper() in templateOptions:
      del templateOptions[opt.upper()]
  
  startupOpt = Options.STARTUP.upper()
  if not config[Options.STARTUP].isEnabled() and startupOpt in templateOptions:
    del templateOptions[startupOpt]
  
  # removes options if they match the tor defaults
  for opt in TOR_DEFAULTS:
    if templateOptions[opt.upper()] == TOR_DEFAULTS[opt]:
      del templateOptions[opt.upper()]
  
  return torConfig.renderTorrc(template, templateOptions)

def showConfirmationDialog(torrcContents, torrcLocation):
  """
  Shows a confirmation dialog with the given torrc contents, returning CANCEL,
  NEXT, or BACK based on the selection.
  
  Arguments:
    torrcContents - lines of torrc contents to be presented
    torrcLocation - path where the torrc will be placed
  """
  
  torrcLines = torrcContents.split("\n")
  options = ["Cancel", "Back to Setup", "Start Tor"]
  
  control = cli.controller.getController()
  screenHeight = control.getScreen().getmaxyx()[0]
  stickyHeight = sum([stickyPanel.getHeight() for stickyPanel in control.getStickyPanels()])
  isScrollbarVisible = len(torrcLines) + stickyHeight + 5 > screenHeight
  
  xOffset = 3 if isScrollbarVisible else 0
  popup, width, height = cli.popups.init(len(torrcLines) + 5, 84 + xOffset)
  if not popup: return False
  
  try:
    scroll, selection = 0, 2
    curses.cbreak()
    
    while True:
      popup.win.erase()
      popup.win.box()
      
      # renders the scrollbar
      if isScrollbarVisible:
        popup.addScrollBar(scroll, scroll + height - 5, len(torrcLines), 1, height - 4, 1)
      
      # shows the path where the torrc will be placed
      titleMsg = "The following will be placed at '%s':" % torrcLocation
      popup.addstr(0, 0, titleMsg, curses.A_STANDOUT)
      
      # renders the torrc contents
      for i in range(scroll, min(len(torrcLines), height - 5 + scroll)):
        # parses the argument and comment from options
        option, arg, comment = uiTools.cropStr(torrcLines[i], width - 4 - xOffset), "", ""
        
        div = option.find("#")
        if div != -1: option, comment = option[:div], option[div:]
        
        div = option.strip().find(" ")
        if div != -1: option, arg = option[:div], option[div:]
        
        drawX = 2 + xOffset
        popup.addstr(i + 1 - scroll, drawX, option, curses.A_BOLD | uiTools.getColor("green"))
        drawX += len(option)
        popup.addstr(i + 1 - scroll, drawX, arg, curses.A_BOLD | uiTools.getColor("cyan"))
        drawX += len(arg)
        popup.addstr(i + 1 - scroll, drawX, comment, uiTools.getColor("white"))
      
      # divider between the torrc and the options
      popup.addch(height - 4, 0, curses.ACS_LTEE)
      popup.addch(height - 4, width, curses.ACS_RTEE)
      popup.hline(height - 4, 1, width - 1)
      if isScrollbarVisible: popup.addch(height - 4, 2, curses.ACS_BTEE)
      
      # renders the selection options
      confirmationMsg = "Run tor with the above configuration?"
      popup.addstr(height - 3, width - len(confirmationMsg) - 1, confirmationMsg, uiTools.getColor("green") | curses.A_BOLD)
      
      drawX = width - 1
      for i in range(len(options) - 1, -1, -1):
        optionLabel = " %s " % options[i]
        drawX -= (len(optionLabel) + 4)
        
        selectionFormat = curses.A_STANDOUT if i == selection else curses.A_NORMAL
        popup.addstr(height - 2, drawX, "[", uiTools.getColor("green"))
        popup.addstr(height - 2, drawX + 1, optionLabel, uiTools.getColor("green") | selectionFormat | curses.A_BOLD)
        popup.addstr(height - 2, drawX + len(optionLabel) + 1, "]", uiTools.getColor("green"))
        
        drawX -= 1 # space gap between the options
      
      popup.win.refresh()
      key = cli.controller.getController().getScreen().getch()
      
      if key == curses.KEY_LEFT:
        selection = (selection - 1) % len(options)
      elif key == curses.KEY_RIGHT:
        selection = (selection + 1) % len(options)
      elif uiTools.isScrollKey(key):
        scroll = uiTools.getScrollPosition(key, scroll, height - 5, len(torrcLines))
      elif uiTools.isSelectionKey(key):
        if selection == 0: return CANCEL
        elif selection == 1: return BACK
        else: return NEXT
      elif key in (27, ord('q'), ord('Q')): return CANCEL
  finally:
    cli.popups.finalize()

def _splitStr(msg, width):
  """
  Splits a string into substrings of a given length.
  
  Arguments:
    msg   - string to be broken up
    width - max length of any returned substring
  """
  
  results = []
  while msg:
    msgSegment, msg = uiTools.cropStr(msg, width, None, endType = None, getRemainder = True)
    if not msgSegment: break # happens if the width is less than the first word
    results.append(msgSegment.strip())
  
  return results

def _isEmailAddress(address):
  """
  True if the input is an email address, false otherwise.
  """
  
  # just checks if there's an '@' and '.' in the input w/o whitespace
  emailMatcher = re.compile("\S*@\S*\.\S*")
  return emailMatcher.match(address)

def _obscureEmailAddress(address):
  """
  Makes some effort to obscure an email address while keeping it readable.
  
  Arguments:
    address - actual email address
  """
  
  address = _obscureChar(address, '@', (_randomCase("at"), ))
  address = _obscureChar(address, '.', (_randomCase("dot"), ))
  return address

def _randomCase(word):
  """
  Provides a word back with the case of its letters randomized.
  
  Arguments:
    word - word for which to randomize the case
  """
  
  result = []
  for letter in word:
    result.append(random.choice((letter.lower(), letter.upper())))
  
  return "".join(result)

def _obscureChar(inputText, target, options):
  """
  Obscures the given character from the input, replacing it with something
  from a set of options and bracketing the selection.
  
  Arguments:
    inputText - text to be obscured
    target    - character to be replaced
    options   - replacement options for the character
  """
  
  leftSpace = random.randint(0, 3)
  leftFill = random.choice((' ', '_', '-', '=', '<'))
  
  rightSpace = random.randint(0, 3)
  rightFill = random.choice((' ', '_', '-', '=', '>'))
  
  bracketLeft, bracketRight = random.choice(BRACKETS)
  optSelection = random.choice(options)
  replacement = "".join((bracketLeft, leftFill * leftSpace, optSelection, rightFill * rightSpace, bracketRight))
  
  return inputText.replace(target, replacement)

def _toggleEnabledAction(toggleOptions, option, value, invert = False):
  """
  Enables or disables custom exit policy options based on our selection.
  
  Arguments:
    toggleOptions - configuration options to be toggled to match our our
                    selection (ie, true -> enabled, false -> disabled)
    options       - our config option
    value         - the value we're being set to
    invert        - inverts selection if true
  """
  
  if invert: value = not value
  
  for opt in toggleOptions:
    opt.setEnabled(value)

def _relayRateValidator(option, value):
  if value.count(" ") != 1:
    msg = "This should be a rate measurement (for instance, \"5 MB/s\")"
    raise ValueError(msg)
  
  rate, units = value.split(" ", 1)
  acceptedUnits = ("KB/s", "MB/s", "GB/s")
  if not rate.isdigit():
    raise ValueError("'%s' isn't an integer" % rate)
  elif not units in acceptedUnits:
    msg = "'%s' is an invalid rate, options include \"%s\"" % (units, "\", \"".join(acceptedUnits))
    raise ValueError(msg)
  elif (int(rate) < 20 and units == "KB/s") or int(rate) < 1:
    raise ValueError("To be usable as a relay the rate must be at least 20 KB/s")

def _monthlyLimitValidator(option, value):
  if value.count(" ") != 1:
    msg = "This should be a traffic size (for instance, \"5 MB\")"
    raise ValueError(msg)
  
  rate, units = value.split(" ", 1)
  acceptedUnits = ("MB", "GB", "TB")
  if not rate.isdigit():
    raise ValueError("'%s' isn't an integer" % rate)
  elif not units in acceptedUnits:
    msg = "'%s' is an invalid unit, options include \"%s\"" % (units, "\", \"".join(acceptedUnits))
    raise ValueError(msg)
  elif (int(rate) < 50 and units == "MB") or int(rate) < 1:
    raise ValueError("To be usable as a relay's monthly limit should be at least 50 MB")

def _bridgeDestinationValidator(option, value):
  if value.count(":") != 1:
    raise ValueError("Bridges are of the form '<ip address>:<port>'")
  
  ipAddr, port = value.split(":", 1)
  if not connections.isValidIpAddress(ipAddr):
    raise ValueError("'%s' is not a valid ip address" % ipAddr)
  elif not port.isdigit() or int(port) < 0 or int(port) > 65535:
    raise ValueError("'%s' isn't a valid port number" % port)

def _circDurationValidator(option, value):
  if value.count(" ") != 1:
    msg = "This should be a time measurement (for instance, \"10 minutes\")"
    raise ValueError(msg)
  
  rate, units = value.split(" ", 1)
  acceptedUnits = ("minute", "minutes", "hour", "hours")
  if not rate.isdigit():
    raise ValueError("'%s' isn't an integer" % rate)
  elif not units in acceptedUnits:
    msg = "'%s' is an invalid rate, options include \"minutes\" or \"hours\""
    raise ValueError(msg)
  elif (int(rate) < 5 and units in ("minute", "minutes")) or int(rate) < 1:
    raise ValueError("This would cause high network load, don't set this to less than five minutes")

