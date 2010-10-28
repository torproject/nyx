"""
Panel presenting the configuration state for tor or arm. Options can be edited
and the resulting configuration files saved.
"""

import curses
import threading

from util import conf, panel, torTools, uiTools

DEFAULT_CONFIG = {"features.config.state.colWidth.option": 25,
                  "features.config.state.colWidth.value": 15}

TOR_STATE, ARM_STATE = range(1, 3) # state to be presented

class ConfigEntry():
  """
  Configuration option in the panel.
  """
  
  def __init__(self, option, type, description = "", isDefault = True):
    self.option = option
    self.type = type
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
    self.scroll = 0
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
        self.confContents.append(ConfigEntry(confOption, confType, "", not confOption in setOptions))
    elif self.configType == ARM_STATE:
      # loaded via the conf utility
      armConf = conf.getConfig("arm")
      for key in armConf.getKeys():
        self.confContents.append(ConfigEntry(key, ", ".join(armConf.getValue(key, [], True)), ""))
      #self.confContents.sort() # TODO: make contents sortable?
  
  def handleKey(self, key):
    self.valsLock.acquire()
    if uiTools.isScrollKey(key):
      pageHeight = self.getPreferredSize()[0] - 1
      newScroll = uiTools.getScrollPosition(key, self.scroll, pageHeight, len(self.confContents))
      
      if self.scroll != newScroll:
        self.scroll = newScroll
        self.redraw(True)
  
  def draw(self, subwindow, width, height):
    self.valsLock.acquire()
    
    # draws the top label
    sourceLabel = "Tor" if self.configType == TOR_STATE else "Arm"
    self.addstr(0, 0, "%s Config:" % sourceLabel, curses.A_STANDOUT)
    
    # draws left-hand scroll bar if content's longer than the height
    scrollOffset = 0
    if len(self.confContents) > height - 1:
      scrollOffset = 3
      self.addScrollBar(self.scroll, self.scroll + height - 1, len(self.confContents), 1)
    
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
    
    for lineNum in range(self.scroll, len(self.confContents)):
      entry = self.confContents[lineNum]
      drawLine = lineNum + 1 - self.scroll
      
      optionLabel = uiTools.cropStr(entry.option, optionColWidth)
      valueLabel = uiTools.cropStr(entryToValues[entry], valueColWidth)
      
      lineFormat = uiTools.getColor("green") if entry.isDefault else curses.A_BOLD | uiTools.getColor("yellow")
      
      self.addstr(drawLine, scrollOffset, optionLabel, lineFormat)
      self.addstr(drawLine, scrollOffset + optionColWidth + 1, valueLabel, lineFormat)
      self.addstr(drawLine, scrollOffset + optionColWidth + valueColWidth + 2, entry.type, lineFormat)
      
      if drawLine >= height: break
    
    self.valsLock.release()

