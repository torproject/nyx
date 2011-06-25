"""
Provides user prompts for setting up a new relay. This autogenerates a torrc
that's used by arm to run its own tor instance.
"""

import curses

import cli.popups
import cli.controller

from util import enum, uiTools

# basic configuration types we can run as
RelayType = enum.Enum("RELAY", "EXIT", "BRIDGE", "CLIENT")

# all options that can be configured
Options = enum.Enum("NICKNAME", "CONTACT", "NOTIFY", "BANDWIDTH", "LIMIT", "STARTUP")
RelayOptions = (Options.NICKNAME, Options.CONTACT, Options.NOTIFY, Options.BANDWIDTH, Options.LIMIT, Options.STARTUP)

# other options provided in the prompts
CANCEL, NEXT, BACK = "Cancel", "Next", "Back"

MSG_COLOR = "green"
OPTION_COLOR = "yellow"

CONFIG = {"wizard.message.role": "",
          "wizard.message.relay": "",
          "wizard.toggle": {},
          "wizard.default": {},
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
  
  def getKey(self):
    return self.key
  
  def getValue(self):
    return self.value
  
  def getDisplayValue(self):
    return self.value
  
  def setValue(self, value):
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
  def __init__(self, key, group, default, trueLabel, falseLabel):
    ConfigOption.__init__(self, key, group, default)
    self.trueLabel = trueLabel
    self.falseLabel = falseLabel
  
  def getDisplayValue(self):
    return self.trueLabel if self.value else self.falseLabel
  
  def toggle(self):
    self.value = not self.value

def showWizard():
  relayType, config = None, {}
  
  for option in Options.values():
    toggleValues = CONFIG["wizard.toggle"].get(option)
    default = CONFIG["wizard.default"].get(option, "")
    
    if toggleValues:
      if "," in toggleValues:
        trueLabel, falseLabel = toggleValues.split(",", 1)
      else: trueLabel, falseLabel = toggleValues, ""
      
      isSet = default.lower() == "true"
      config[option] = ToggleConfigOption(option, "opt", isSet, trueLabel.strip(), falseLabel.strip())
    else: config[option] = ConfigOption(option, "opt", default)
  
  while True:
    if relayType == None:
      selection = promptRelayType()
      
      if selection == CANCEL: break
      else: relayType = selection
    else:
      if relayType == RelayType.RELAY:
        selection = promptRelayOptions(config)
        
        if selection == BACK: relayType = None
        elif selection == NEXT: break # TODO: implement next screen
      else:
        break # TODO: other catagories not yet implemented
    
    # redraws screen to clear away the dialog we just showed
    cli.controller.getController().requestRedraw(True)

def promptRelayType():
  """
  Provides a prompt for selecting the general role we'd like Tor to run with.
  This returns a RelayType enumeration for the selection, or CANCEL if the
  dialog was canceled.
  """
  
  popup, _, _ = cli.popups.init(24, 58)
  if not popup: return
  control = cli.controller.getController()
  key, selection = 0, 0
  options = [ConfigOption(opt, "role", opt) for opt in RelayType.values()]
  options.append(ConfigOption(CANCEL, "general", CANCEL))
  
  try:
    popup.win.box()
    curses.cbreak()
    
    # provides the welcoming message
    topContent = _splitStr(CONFIG["wizard.message.role"], 54)
    for i in range(len(topContent)):
      popup.addstr(i + 1, 2, topContent[i], curses.A_BOLD | uiTools.getColor(MSG_COLOR))
    
    while True:
      y, offset = len(topContent) + 1, 0
      
      for i in range(len(options)):
        optionFormat = uiTools.getColor(MSG_COLOR)
        if i == selection: optionFormat |= curses.A_STANDOUT
        
        # Curses has a weird bug where there's a one-pixel alignment
        # difference between bold and regular text, so it looks better
        # to render the whitespace here as not being bold.
        
        offset += 1
        label = options[i].getLabel(" ")
        popup.addstr(y + offset, 2, label, optionFormat | curses.A_BOLD)
        popup.addstr(y + offset, 2 + len(label), " " * (54 - len(label)), optionFormat)
        offset += 1
        
        for line in options[i].getDescription(52, " "):
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

def promptRelayOptions(config):
  """
  Prompts the user for the configuration of an internal relay.
  """
  
  popup, _, _ = cli.popups.init(23, 58)
  if not popup: return
  control = cli.controller.getController()
  options = [config[opt] for opt in RelayOptions]
  options.append(ConfigOption(BACK, "general", "(to role selection)"))
  options.append(ConfigOption(NEXT, "general", "(to confirm options)"))
  key, selection = 0, 0
  
  try:
    curses.cbreak()
    
    while True:
      popup.win.erase()
      popup.win.box()
      
      # provides the description for internal relays
      topContent = _splitStr(CONFIG["wizard.message.relay"], 54)
      for i in range(len(topContent)):
        popup.addstr(i + 1, 2, topContent[i], curses.A_BOLD | uiTools.getColor(MSG_COLOR))
      
      y, offset = len(topContent) + 1, 0
      for i in range(len(options)):
        label = " %-30s%s" % (options[i].getLabel(), options[i].getDisplayValue())
        optionFormat = curses.A_BOLD | uiTools.getColor(OPTION_COLOR)
        if i == selection: optionFormat |= curses.A_STANDOUT
        
        offset += 1
        popup.addstr(y + offset, 2, uiTools.padStr(label, 54), optionFormat)
        
        # extra space to divide options/navigation
        if i == len(options) - 3: offset += 1
      
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
      
      if key == curses.KEY_UP: selection = (selection - 1) % len(options)
      elif key == curses.KEY_DOWN: selection = (selection + 1) % len(options)
      elif uiTools.isSelectionKey(key):
        if selection == len(options) - 2: return BACK # selected back
        elif selection == len(options) - 1: return NEXT # selected next
        elif isinstance(options[selection], ToggleConfigOption):
          options[selection].toggle()
        else:
          newValue = popup.getstr(y + selection + 1, 33, options[selection].getValue(), curses.A_STANDOUT | uiTools.getColor(OPTION_COLOR), 23)
          if newValue: options[selection].setValue(newValue.strip())
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
    results.append(msgSegment.strip())
  
  return results

