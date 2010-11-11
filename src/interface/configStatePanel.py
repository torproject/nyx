"""
Panel presenting the configuration state for tor or arm. Options can be edited
and the resulting configuration files saved.
"""

import curses
import threading

from util import conf, panel, torTools, torConfig, uiTools

DEFAULT_CONFIG = {"features.config.showPrivateOptions": False,
                  "features.config.showVirtualOptions": False,
                  "features.config.state.colWidth.option": 25,
                  "features.config.state.colWidth.value": 15}

TOR_STATE, ARM_STATE = range(1, 3) # state to be presented

# mappings of option categories to the color for their entries
CATEGORY_COLOR = {torConfig.GENERAL: "green",
                  torConfig.CLIENT: "blue",
                  torConfig.SERVER: "yellow",
                  torConfig.DIRECTORY: "magenta",
                  torConfig.AUTHORITY: "red",
                  torConfig.HIDDEN_SERVICE: "cyan",
                  torConfig.TESTING: "white",
                  torConfig.UNKNOWN: "black"}

# attributes of a ConfigEntry
FIELD_CATEGORY, FIELD_OPTION, FIELD_VALUE, FIELD_TYPE, FIELD_ARG_USAGE, FIELD_DESCRIPTION, FIELD_MAN_ENTRY, FIELD_IS_DEFAULT = range(8)
DEFAULT_SORT_ORDER = (FIELD_CATEGORY, FIELD_MAN_ENTRY, FIELD_IS_DEFAULT)
FIELD_ATTR = {FIELD_CATEGORY: ("Category", "red"),
              FIELD_OPTION: ("Option Name", "blue"),
              FIELD_VALUE: ("Value", "cyan"),
              FIELD_TYPE: ("Arg Type", "green"),
              FIELD_ARG_USAGE: ("Arg Usage", "yellow"),
              FIELD_DESCRIPTION: ("Description", "white"),
              FIELD_MAN_ENTRY: ("Man Page Entry", "blue"),
              FIELD_IS_DEFAULT: ("Is Default", "magenta")}

class ConfigEntry():
  """
  Configuration option in the panel.
  """
  
  def __init__(self, option, type, isDefault, manEntry):
    self.fields = {}
    self.fields[FIELD_OPTION] = option
    self.fields[FIELD_TYPE] = type
    self.fields[FIELD_IS_DEFAULT] = isDefault
    
    if manEntry:
      self.fields[FIELD_MAN_ENTRY] = manEntry.index
      self.fields[FIELD_CATEGORY] = manEntry.category
      self.fields[FIELD_ARG_USAGE] = manEntry.argUsage
      self.fields[FIELD_DESCRIPTION] = manEntry.description
    else:
      self.fields[FIELD_MAN_ENTRY] = 99999 # sorts non-man entries last
      self.fields[FIELD_CATEGORY] = torConfig.UNKNOWN
      self.fields[FIELD_ARG_USAGE] = ""
      self.fields[FIELD_DESCRIPTION] = ""
  
  def get(self, field):
    """
    Provides back the value in the given field.
    
    Arguments:
      field - enum for the field to be provided back
    """
    
    if field == FIELD_VALUE: return self._getValue()
    else: return self.fields[field]
  
  def _getValue(self):
    """
    Provides the current value of the configuration entry, taking advantage of
    the torTools caching to effectively query the accurate value. This uses the
    value's type to provide a user friendly representation if able.
    """
    
    confValue = ", ".join(torTools.getConn().getOption(self.get(FIELD_OPTION), [], True))
    
    # provides nicer values for recognized types
    if not confValue: confValue = "<none>"
    elif self.get(FIELD_TYPE) == "Boolean" and confValue in ("0", "1"):
      confValue = "False" if confValue == "0" else "True"
    elif self.get(FIELD_TYPE) == "DataSize" and confValue.isdigit():
      confValue = uiTools.getSizeLabel(int(confValue))
    elif self.get(FIELD_TYPE) == "TimeInterval" and confValue.isdigit():
      confValue = uiTools.getTimeLabel(int(confValue), isLong = True)
    
    return confValue
  
  def getAttr(self, argTypes):
    """
    Provides back a list with the given parameters.
    
    Arguments:
      argTypes - list of enums for the arguments to be provided back
    """
    
    return [self.get(field) for field in argTypes]

class ConfigStatePanel(panel.Panel):
  """
  Renders a listing of the tor or arm configuration state, allowing options to
  be selected and edited.
  """
  
  def __init__(self, stdscr, configType, config=None):
    panel.Panel.__init__(self, stdscr, "configState", 0)
    
    self.sortOrdering = DEFAULT_SORT_ORDER
    self._config = dict(DEFAULT_CONFIG)
    if config:
      config.update(self._config, {
        "features.config.state.colWidth.option": 5,
        "features.config.state.colWidth.value": 5})
      
      self.sortOrdering = config.getIntCSV("features.config.order", self.sortOrdering, 3, 0, 6)
    
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
        
        # skips private and virtual entries if not set to show them
        if not self._config["features.config.showPrivateOptions"] and confOption.startswith("__"):
          continue
        elif not self._config["features.config.showVirtualOptions"] and confType == "Virtual":
          continue
        
        manEntry = torConfig.getConfigDescription(confOption)
        self.confContents.append(ConfigEntry(confOption, confType, not confOption in setOptions, manEntry))
      
      
      self.setSortOrder() # initial sorting of the contents
    elif self.configType == ARM_STATE:
      # loaded via the conf utility
      armConf = conf.getConfig("arm")
      for key in armConf.getKeys():
        self.confContents.append(ConfigEntry("", key, ", ".join(armConf.getValue(key, [], True)), "", "", True))
  
  def setSortOrder(self, ordering = None):
    """
    Sets the configuration attributes we're sorting by and resorts the
    contents. If the ordering isn't defined then this resorts based on the
    last set ordering.
    """
    
    self.valsLock.acquire()
    if ordering: self.sortOrdering = ordering
    self.confContents.sort(key=lambda i: (i.getAttr(self.sortOrdering)))
    self.valsLock.release()
  
  def getSortOrder(self):
    """
    Provides the current configuration attributes we're sorting by.
    """
    
    return self.sortOrdering
  
  def handleKey(self, key):
    self.valsLock.acquire()
    if uiTools.isScrollKey(key):
      pageHeight = self.getPreferredSize()[0] - 1
      isChanged = self.scroller.handleKey(key, self.confContents, pageHeight)
      if isChanged: self.redraw(True)
    self.valsLock.release()
  
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
    optionColWidth, valueColWidth = 0, 0
    
    # constructs a mapping of entries to their current values
    entryToValues = {}
    for entry in self.confContents:
      entryToValues[entry] = entry.get(FIELD_VALUE)
      optionColWidth = max(optionColWidth, len(entry.get(FIELD_OPTION)))
      valueColWidth = max(valueColWidth, len(entryToValues[entry]))
    
    optionColWidth = min(self._config["features.config.state.colWidth.option"], optionColWidth)
    valueColWidth = min(self._config["features.config.state.colWidth.value"], valueColWidth)
    descriptionColWidth = max(0, width - scrollOffset - optionColWidth - valueColWidth - 2)
    
    for lineNum in range(scrollLoc, len(self.confContents)):
      entry = self.confContents[lineNum]
      drawLine = lineNum + 1 - scrollLoc
      
      # TODO: need to cut off description at the first newline
      optionLabel = uiTools.cropStr(entry.get(FIELD_OPTION), optionColWidth)
      valueLabel = uiTools.cropStr(entryToValues[entry], valueColWidth)
      descriptionLabel = uiTools.cropStr(entry.get(FIELD_DESCRIPTION), descriptionColWidth, None)
      
      lineFormat = curses.A_NORMAL if entry.get(FIELD_IS_DEFAULT) else curses.A_BOLD
      if entry.get(FIELD_CATEGORY): lineFormat |= uiTools.getColor(CATEGORY_COLOR[entry.get(FIELD_CATEGORY)])
      #lineFormat = uiTools.getColor("green") if entry.isDefault else curses.A_BOLD | uiTools.getColor("yellow")
      if entry == cursorSelection: lineFormat |= curses.A_STANDOUT
      
      lineTextLayout = "%%-%is %%-%is %%-%is" % (optionColWidth, valueColWidth, descriptionColWidth)
      lineText = lineTextLayout % (optionLabel, valueLabel, descriptionLabel)
      self.addstr(drawLine, scrollOffset, lineText, lineFormat)
      
      if drawLine >= height: break
    
    self.valsLock.release()

