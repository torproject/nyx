"""
Provides user prompts for setting up a new relay. This autogenerates a torrc
that's used by arm to run its own tor instance.
"""

import functools
import curses

import cli.popups
import cli.controller

from util import enum, connections, uiTools

# basic configuration types we can run as
RelayType = enum.Enum("RELAY", "EXIT", "BRIDGE", "CLIENT")

# all options that can be configured
Options = enum.Enum("DIVIDER", "NICKNAME", "CONTACT", "NOTIFY", "BANDWIDTH", "LIMIT", "CLIENT", "PORTFORWARD", "STARTUP", "NOTICE", "POLICY", "WEBSITES", "EMAIL", "IM", "MISC", "PLAINTEXT", "DISTRIBUTE", "BRIDGED", "BRIDGE1", "BRIDGE2", "BRIDGE3", "REUSE")
RelayOptions = {RelayType.RELAY:   (Options.NICKNAME,
                                    Options.CONTACT,
                                    Options.NOTIFY,
                                    Options.BANDWIDTH,
                                    Options.LIMIT,
                                    Options.CLIENT,
                                    Options.PORTFORWARD,
                                    Options.STARTUP),
                RelayType.EXIT:    (Options.NICKNAME,
                                    Options.CONTACT,
                                    Options.NOTIFY,
                                    Options.BANDWIDTH,
                                    Options.LIMIT,
                                    Options.CLIENT,
                                    Options.PORTFORWARD,
                                    Options.STARTUP,
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
                                    Options.PORTFORWARD,
                                    Options.STARTUP
                                   ),
                RelayType.CLIENT:  (Options.BRIDGED,
                                    Options.BRIDGE1,
                                    Options.BRIDGE2,
                                    Options.BRIDGE3,
                                    Options.REUSE)}

# option sets
CUSTOM_POLICIES = (Options.WEBSITES, Options.EMAIL, Options.IM, Options.MISC, Options.PLAINTEXT)
BRIDGE_ENTRIES = (Options.BRIDGE1, Options.BRIDGE2, Options.BRIDGE3)

# other options provided in the prompts
CANCEL, NEXT, BACK = "Cancel", "Next", "Back"

DESC_SIZE = 5 # height of the description field
MSG_COLOR = "green"
OPTION_COLOR = "yellow"
DISABLED_COLOR = "cyan"

CONFIG = {"wizard.message.role": "",
          "wizard.message.relay": "",
          "wizard.message.exit": "",
          "wizard.message.bridge": "",
          "wizard.message.client": "",
          "wizard.toggle": {},
          "wizard.suboptions": [],
          "wizard.default": {},
          "wizard.blankValue": {},
          "wizard.label.general": {},
          "wizard.label.role": {},
          "wizard.label.opt": {},
          "wizard.description.general": {},
          "wizard.description.role": {},
          "wizard.description.opt": {}}

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
  config[Options.BRIDGE1].setValidator(_bridgeDestinationValidator)
  config[Options.BRIDGE2].setValidator(_bridgeDestinationValidator)
  config[Options.BRIDGE3].setValidator(_bridgeDestinationValidator)
  
  # enables custom policies when 'custom' is selected and disables otherwise
  policyOpt = config[Options.POLICY]
  customPolicies = [config[opt] for opt in CUSTOM_POLICIES]
  policyOpt.setValidator(functools.partial(_toggleEnabledAction, customPolicies))
  _toggleEnabledAction(customPolicies, policyOpt, policyOpt.getValue())
  
  # enables bridge entries when "Use Bridges" is set and disables otherwise
  useBridgeOpt = config[Options.BRIDGED]
  bridgeEntries = [config[opt] for opt in BRIDGE_ENTRIES]
  useBridgeOpt.setValidator(functools.partial(_toggleEnabledAction, bridgeEntries))
  _toggleEnabledAction(bridgeEntries, useBridgeOpt, useBridgeOpt.getValue())
  
  # remembers the last selection made on the type prompt page
  relaySelection = RelayType.RELAY
  
  while True:
    if relayType == None:
      selection = promptRelayType(relaySelection)
      
      if selection == CANCEL: break
      else: relayType, relaySelection = selection, selection
    else:
      selection = promptConfigOptions(relayType, config)
      
      if selection == BACK: relayType = None
      elif selection == NEXT: break # TODO: implement next screen
    
    # redraws screen to clear away the dialog we just showed
    cli.controller.getController().requestRedraw(True)

def promptRelayType(initialSelection):
  """
  Provides a prompt for selecting the general role we'd like Tor to run with.
  This returns a RelayType enumeration for the selection, or CANCEL if the
  dialog was canceled.
  """
  
  popup, _, _ = cli.popups.init(25, 58)
  if not popup: return
  control = cli.controller.getController()
  options = [ConfigOption(opt, "role", opt) for opt in RelayType.values()]
  options.append(ConfigOption(CANCEL, "general", CANCEL))
  selection = RelayType.indexOf(initialSelection)
  
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
      elif key == 27: return CANCEL # esc - cancel
  finally:
    cli.popups.finalize()

def promptConfigOptions(relayType, config):
  """
  Prompts the user for the configuration of an internal relay.
  """
  
  topContent = _splitStr(CONFIG.get("wizard.message.%s" % relayType.lower(), ""), 54)
  
  options = [config[opt] for opt in RelayOptions[relayType]]
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
              cli.popups.showMsg(str(exc), 2)
              cli.controller.getController().requestRedraw(True)
      elif key == 27: selection, key = -1, curses.KEY_ENTER # esc - cancel
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

def _toggleEnabledAction(toggleOptions, option, value):
  """
  Enables or disables custom exit policy options based on our selection.
  
  Arguments:
    toggleOptions - configuration options to be toggled to match our our
                    selection (ie, true -> enabled, false -> disabled)
    options       - our config option
    value         - the value we're being set to
  """
  
  for opt in toggleOptions:
    opt.setEnabled(value)

def _bridgeDestinationValidator(option, value):
  if value.count(":") != 1:
    raise ValueError("Bridges are of the form '<ip address>:<port>'")
  
  ipAddr, port = value.split(":", 1)
  if not connections.isValidIpAddress(ipAddr):
    raise ValueError("'%s' is not a valid ip address" % ipAddr)
  elif not port.isdigit() or int(port) < 0 or int(port) > 65535:
    raise ValueError("'%s' isn't a valid port number" % port)

