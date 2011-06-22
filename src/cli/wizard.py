"""
Provides user prompts for setting up a new relay. This autogenerates a torrc
that's used by arm to start its tor instance.
"""

import curses

import cli.popups
import cli.controller

from util import enum, uiTools

# basic configuration types we can run as
RunType = enum.Enum("RELAY", "EXIT", "BRIDGE", "CLIENT")

# other options provided in the prompts
CANCEL, BACK = "Cancel", "Back"

CONFIG = {"wizard.role.message": "",
          "wizard.role.option.label": {},
          "wizard.role.option.description": {}}

def loadConfig(config):
  config.update(CONFIG)

def showWizard():
  myRole = promptRunType()

def promptRunType():
  """
  Provides a prompt for selecting the general role we'd like Tor to run with.
  This returns a RunType enumeration for the selection, or None if the dialog
  was canceled.
  """
  
  popup, _, _ = cli.popups.init(23, 58)
  if not popup: return
  control = cli.controller.getController()
  key, selection = 0, 0
  
  # constructs (enum, label, [description lines]) tuples for our options
  options = []
  
  for runType in RunType.values() + [CANCEL]:
    label = CONFIG["wizard.role.option.label"].get(runType, "")
    descRemainder = CONFIG["wizard.role.option.description"].get(runType, "")
    descLines = []
    
    while descRemainder:
      descLine, descRemainder = uiTools.cropStr(descRemainder, 54, None, endType = None, getRemainder = True)
      descLines.append(descLine.strip())
    
    options.append((runType, label, descLines))
  
  try:
    popup.win.box()
    curses.cbreak()
    format = uiTools.getColor("green")
    y, msgRemainder = 1, CONFIG["wizard.role.message"]
    
    # provides the welcoming message
    while msgRemainder:
      msg, msgRemainder = uiTools.cropStr(msgRemainder, 54, None, endType = None, getRemainder = True)
      popup.addstr(y, 2, msg.strip(), format | curses.A_BOLD)
      y += 1
    
    while not uiTools.isSelectionKey(key):
      offset = 0
      
      for i in range(len(options)):
        _, label, lines = options[i]
        optionFormat = format | curses.A_STANDOUT if i == selection else format
        
        # Curses has a weird bug where there's a one-pixel alignment
        # difference between bold and regular text, so it looks better
        # to render the whitespace here as not being bold.
        
        offset += 1
        popup.addstr(y + offset, 2, label, optionFormat | curses.A_BOLD)
        popup.addstr(y + offset, 2 + len(label), " " * (54 - len(label)), optionFormat)
        offset += 1
        
        for line in lines:
          popup.addstr(y + offset, 2, uiTools.padStr(line, 54), optionFormat)
          offset += 1
      
      popup.win.refresh()
      key = control.getScreen().getch()
      
      if key == curses.KEY_UP: selection = max(0, selection - 1)
      elif key == curses.KEY_DOWN: selection = min(len(options) - 1, selection + 1)
      elif key == 27: selection, key = -1, curses.KEY_ENTER # esc - cancel
  finally:
    cli.popups.finalize()
  
  selectedOption = options[selection][0]
  return None if selectedOption == CANCEL else selectedOption

