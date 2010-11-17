"""
Panel presenting the configuration state for tor or arm. Options can be edited
and the resulting configuration files saved.
"""

import curses
import threading

from util import conf, panel, torTools, torConfig, uiTools

DEFAULT_CONFIG = {"features.config.selectionDetails.height": 6,
                  "features.config.state.showPrivateOptions": False,
                  "features.config.state.showVirtualOptions": False,
                  "features.config.state.colWidth.option": 25,
                  "features.config.state.colWidth.value": 10}

TOR_STATE, ARM_STATE = range(1, 3) # state to be presented

# mappings of option categories to the color for their entries
CATEGORY_COLOR = {torConfig.GENERAL: "green",
                  torConfig.CLIENT: "blue",
                  torConfig.SERVER: "yellow",
                  torConfig.DIRECTORY: "magenta",
                  torConfig.AUTHORITY: "red",
                  torConfig.HIDDEN_SERVICE: "cyan",
                  torConfig.TESTING: "white",
                  torConfig.UNKNOWN: "white"}

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
        "features.config.selectionDetails.height": 0,
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
        if not self._config["features.config.state.showPrivateOptions"] and confOption.startswith("__"):
          continue
        elif not self._config["features.config.state.showVirtualOptions"] and confType == "Virtual":
          continue
        
        manEntry = torConfig.getConfigDescription(confOption)
        self.confContents.append(ConfigEntry(confOption, confType, not confOption in setOptions, manEntry))
      
      
      self.setSortOrder() # initial sorting of the contents
    elif self.configType == ARM_STATE:
      # loaded via the conf utility
      armConf = conf.getConfig("arm")
      for key in armConf.getKeys():
        self.confContents.append(ConfigEntry("", key, ", ".join(armConf.getValue(key, [], True)), "", "", True))
  
  def getSelection(self):
    """
    Provides the currently selected entry.
    """
    
    return self.scroller.getCursorSelection(self.confContents)
  
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
      detailPanelHeight = self._config["features.config.selectionDetails.height"]
      if detailPanelHeight > 0 and detailPanelHeight + 2 <= pageHeight:
        pageHeight -= (detailPanelHeight + 1)
      
      isChanged = self.scroller.handleKey(key, self.confContents, pageHeight)
      if isChanged: self.redraw(True)
    self.valsLock.release()
  
  def draw(self, subwindow, width, height):
    self.valsLock.acquire()
    
    # draws the top label
    titleLabel = "%s Configuration:" % ("Tor" if self.configType == TOR_STATE else "Arm")
    self.addstr(0, 0, titleLabel, curses.A_STANDOUT)
    
    # panel with details for the current selection
    detailPanelHeight = self._config["features.config.selectionDetails.height"]
    if detailPanelHeight == 0 or detailPanelHeight + 2 >= height:
      # no detail panel
      detailPanelHeight = 0
      scrollLoc = self.scroller.getScrollLoc(self.confContents, height - 1)
      cursorSelection = self.scroller.getCursorSelection(self.confContents)
    else:
      # Shrink detail panel if there isn't sufficient room for the whole
      # thing. The extra line is for the bottom border.
      detailPanelHeight = min(height - 1, detailPanelHeight + 1)
      scrollLoc = self.scroller.getScrollLoc(self.confContents, height - 1 - detailPanelHeight)
      cursorSelection = self.scroller.getCursorSelection(self.confContents)
      
      self._drawSelectionPanel(cursorSelection, width, detailPanelHeight, titleLabel)
    
    # draws left-hand scroll bar if content's longer than the height
    scrollOffset = 0
    if len(self.confContents) > height - detailPanelHeight - 1:
      scrollOffset = 3
      self.addScrollBar(scrollLoc, scrollLoc + height - detailPanelHeight - 1, len(self.confContents), 1 + detailPanelHeight)
    
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
      drawLine = lineNum + detailPanelHeight + 1 - scrollLoc
      
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
  
  def _drawSelectionPanel(self, cursorSelection, width, detailPanelHeight, titleLabel):
    """
    Renders a panel for the selected configuration option.
    """
    
    # border (top)
    if width >= len(titleLabel):
      self.win.hline(0, len(titleLabel), curses.ACS_HLINE, width - len(titleLabel))
      self.win.vline(0, width, curses.ACS_URCORNER, 1)
    
    # border (sides)
    self.win.vline(1, 0, curses.ACS_VLINE, detailPanelHeight - 1)
    self.win.vline(1, width, curses.ACS_VLINE, detailPanelHeight - 1)
    
    # border (bottom)
    self.win.vline(detailPanelHeight, 0, curses.ACS_LLCORNER, 1)
    if width >= 2: self.win.vline(detailPanelHeight, 1, curses.ACS_TTEE, 1)
    if width >= 3: self.win.hline(detailPanelHeight, 2, curses.ACS_HLINE, width - 2)
    self.win.vline(detailPanelHeight, width, curses.ACS_LRCORNER, 1)
    
    selectionFormat = curses.A_BOLD | uiTools.getColor(CATEGORY_COLOR[cursorSelection.get(FIELD_CATEGORY)])
    
    # first entry:
    # <option> (<category> Option)
    optionLabel =" (%s Option)" % torConfig.OPTION_CATEGORY_STR[cursorSelection.get(FIELD_CATEGORY)]
    self.addstr(1, 2, cursorSelection.get(FIELD_OPTION) + optionLabel, selectionFormat)
    
    # second entry:
    # Value: <value> ([default|custom], <type>, usage: <argument usage>)
    if detailPanelHeight >= 3:
      valueAttr = []
      valueAttr.append("default" if cursorSelection.get(FIELD_IS_DEFAULT) else "custom")
      valueAttr.append(cursorSelection.get(FIELD_TYPE))
      valueAttr.append("usage: %s" % (cursorSelection.get(FIELD_ARG_USAGE)))
      valueAttrLabel = ", ".join(valueAttr)
      
      valueLabelWidth = width - 12 - len(valueAttrLabel)
      valueLabel = uiTools.cropStr(cursorSelection.get(FIELD_VALUE), valueLabelWidth)
      
      self.addstr(2, 2, "Value: %s (%s)" % (valueLabel, valueAttrLabel), selectionFormat)
    
    # remainder is filled with the man page description
    descriptionHeight = max(0, detailPanelHeight - 3)
    descriptionContent = "Description: " + cursorSelection.get(FIELD_DESCRIPTION)
    
    for i in range(descriptionHeight):
      # checks if we're done writing the description
      if not descriptionContent: break
      
      # there's a leading indent after the first line
      if i > 0: descriptionContent = "  " + descriptionContent
      
      # we only want to work with content up until the next newline
      if "\n" in descriptionContent:
        lineContent, descriptionContent = descriptionContent.split("\n", 1)
      else: lineContent, descriptionContent = descriptionContent, ""
      
      if i != descriptionHeight - 1:
        # there's more lines to display
        msg, remainder = uiTools.cropStr(lineContent, width - 2, 4, 4, uiTools.END_WITH_HYPHEN, True)
        descriptionContent = remainder.strip() + descriptionContent
      else:
        # this is the last line, end it with an ellipse
        msg = uiTools.cropStr(lineContent, width - 2, 4, 4)
      
      self.addstr(3 + i, 2, msg, selectionFormat)

