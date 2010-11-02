"""
Panel presenting the configuration state for tor or arm. Options can be edited
and the resulting configuration files saved.
"""

import curses
import threading

from util import conf, panel, torTools, torConfig, uiTools

DEFAULT_CONFIG = {"features.config.state.colWidth.option": 25,
                  "features.config.state.colWidth.value": 15}

TOR_STATE, ARM_STATE = range(1, 3) # state to be presented

# mappings of option categories to the color for their entries
CATEGORY_COLOR = {torConfig.GENERAL: "green",
                  torConfig.CLIENT: "blue",
                  torConfig.SERVER: "yellow",
                  torConfig.DIRECTORY: "magenta",
                  torConfig.AUTHORITY: "red",
                  torConfig.HIDDEN_SERVICE: "cyan",
                  torConfig.TESTING: "white"}

class ConfigEntry():
  """
  Configuration option in the panel.
  """
  
  def __init__(self, category, option, type, argumentUsage, description, isDefault):
    self.category = category
    self.option = option
    self.type = type
    self.argumentUsage = argumentUsage
    self.description = description
    self.isDefault = isDefault
  
  def getValue(self):
    """
    Provides the current value of the configuration entry, taking advantage of
    the torTools caching to effectively query the accurate value. This uses the
    value's type to provide a user friendly representation if able.
    """
    
    confValue = ", ".join(torTools.getConn().getOption(self.option, [], True))
    
    # provides nicer values for recognized types
    if not confValue: confValue = "<none>"
    elif self.type == "Boolean" and confValue in ("0", "1"):
      confValue = "False" if confValue == "0" else "True"
    elif self.type == "DataSize" and confValue.isdigit():
      confValue = uiTools.getSizeLabel(int(confValue))
    elif self.type == "TimeInterval" and confValue.isdigit():
      confValue = uiTools.getTimeLabel(int(confValue), isLong = True)
    
    return confValue

class ConfigStatePanel(panel.Panel):
  """
  Renders a listing of the tor or arm configuration state, allowing options to
  be selected and edited.
  """
  
  def __init__(self, stdscr, configType, config=None):
    panel.Panel.__init__(self, stdscr, "configState", 0)
    
    self._config = dict(DEFAULT_CONFIG)
    if config: config.update(self._config, {
      "features.config.state.colWidth.option": 5,
      "features.config.state.colWidth.value": 5})
    
    self.configType = configType
    self.confContents = []
    self.scroller = uiTools.Scroller(True)
    self.valsLock = threading.RLock()
    
    # TODO: this will need to be able to listen for SETCONF events (arg!)
    
    if self.configType == TOR_STATE:
      conn = torTools.getConn()
      
      # gets options that differ from their default
      setOptions = set()
      configTextQuery = conn.getInfo("config-text", "").strip().split("\n")
      for entry in configTextQuery: setOptions.add(entry[:entry.find(" ")])
      
      # for all recognized tor config options, provide their current value
      configOptionQuery = conn.getInfo("config/names", "").strip().split("\n")
      
      for lineNum in range(len(configOptionQuery)):
        # lines are of the form "<option> <type>", like:
        # UseEntryGuards Boolean
        line = configOptionQuery[lineNum]
        confOption, confType = line.strip().split(" ", 1)
        
        cat, arg, desc = None, "", ""
        descriptionComp = torConfig.getConfigDescription(confOption)
        if descriptionComp: cat, arg, desc = descriptionComp
        
        self.confContents.append(ConfigEntry(cat, confOption, confType, arg, desc, not confOption in setOptions))
    elif self.configType == ARM_STATE:
      # loaded via the conf utility
      armConf = conf.getConfig("arm")
      for key in armConf.getKeys():
        self.confContents.append(ConfigEntry("", key, ", ".join(armConf.getValue(key, [], True)), "", "", True))
      #self.confContents.sort() # TODO: make contents sortable?
  
  def handleKey(self, key):
    self.valsLock.acquire()
    if uiTools.isScrollKey(key):
      pageHeight = self.getPreferredSize()[0] - 1
      isChanged = self.scroller.handleKey(key, self.confContents, pageHeight)
      if isChanged: self.redraw(True)
  
  def draw(self, subwindow, width, height):
    self.valsLock.acquire()
    
    # draws the top label
    sourceLabel = "Tor" if self.configType == TOR_STATE else "Arm"
    self.addstr(0, 0, "%s Config:" % sourceLabel, curses.A_STANDOUT)
    
    scrollLoc = self.scroller.getScrollLoc(self.confContents, height - 1)
    cursorSelection = self.scroller.getCursorSelection(self.confContents)
    
    # draws left-hand scroll bar if content's longer than the height
    scrollOffset = 0
    if len(self.confContents) > height - 1:
      scrollOffset = 3
      self.addScrollBar(scrollLoc, scrollLoc + height - 1, len(self.confContents), 1)
    
    # determines the width for the columns
    optionColWidth, valueColWidth, typeColWidth = 0, 0, 0
    
    # constructs a mapping of entries to their current values
    entryToValues = {}
    for entry in self.confContents:
      entryToValues[entry] = entry.getValue()
      optionColWidth = max(optionColWidth, len(entry.option))
      valueColWidth = max(valueColWidth, len(entryToValues[entry]))
      typeColWidth = max(typeColWidth, len(entry.type))
    
    optionColWidth = min(self._config["features.config.state.colWidth.option"], optionColWidth)
    valueColWidth = min(self._config["features.config.state.colWidth.value"], valueColWidth)
    descriptionColWidth = max(0, width - scrollOffset - optionColWidth - valueColWidth - typeColWidth - 3)
    
    for lineNum in range(scrollLoc, len(self.confContents)):
      entry = self.confContents[lineNum]
      drawLine = lineNum + 1 - scrollLoc
      
      # TODO: need to cut off description at the first newline
      optionLabel = uiTools.cropStr(entry.option, optionColWidth)
      valueLabel = uiTools.cropStr(entryToValues[entry], valueColWidth)
      descriptionLabel = uiTools.cropStr(entry.description, descriptionColWidth, None)
      
      lineFormat = curses.A_NORMAL if entry.isDefault else curses.A_BOLD
      if entry.category: lineFormat |= uiTools.getColor(CATEGORY_COLOR[entry.category])
      #lineFormat = uiTools.getColor("green") if entry.isDefault else curses.A_BOLD | uiTools.getColor("yellow")
      if entry == cursorSelection: lineFormat |= curses.A_STANDOUT
      
      lineTextLayout = "%%-%is %%-%is %%-%is %%-%is" % (optionColWidth, valueColWidth, typeColWidth, descriptionColWidth)
      lineText = lineTextLayout % (optionLabel, valueLabel, entry.type, descriptionLabel)
      self.addstr(drawLine, scrollOffset, lineText, lineFormat)
      
      if drawLine >= height: break
    
    self.valsLock.release()

