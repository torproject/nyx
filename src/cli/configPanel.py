"""
Panel presenting the configuration state for tor or arm. Options can be edited
and the resulting configuration files saved.
"""

import curses
import threading

import popups

from util import conf, enum, panel, torTools, torConfig, uiTools

DEFAULT_CONFIG = {"features.config.selectionDetails.height": 6,
                  "features.config.state.showPrivateOptions": False,
                  "features.config.state.showVirtualOptions": False,
                  "features.config.state.colWidth.option": 25,
                  "features.config.state.colWidth.value": 15}

# TODO: The arm use cases are incomplete since they currently can't be
# modified, have their descriptions fetched, or even get a complete listing
# of what's available.
State = enum.Enum("TOR", "ARM") # state to be presented

# mappings of option categories to the color for their entries
CATEGORY_COLOR = {torConfig.Category.GENERAL: "green",
                  torConfig.Category.CLIENT: "blue",
                  torConfig.Category.RELAY: "yellow",
                  torConfig.Category.DIRECTORY: "magenta",
                  torConfig.Category.AUTHORITY: "red",
                  torConfig.Category.HIDDEN_SERVICE: "cyan",
                  torConfig.Category.TESTING: "white",
                  torConfig.Category.UNKNOWN: "white"}

# attributes of a ConfigEntry
Field = enum.Enum("CATEGORY", "OPTION", "VALUE", "TYPE", "ARG_USAGE",
                  "SUMMARY", "DESCRIPTION", "MAN_ENTRY", "IS_DEFAULT")
DEFAULT_SORT_ORDER = (Field.MAN_ENTRY, Field.OPTION, Field.IS_DEFAULT)
FIELD_ATTR = {Field.CATEGORY: ("Category", "red"),
              Field.OPTION: ("Option Name", "blue"),
              Field.VALUE: ("Value", "cyan"),
              Field.TYPE: ("Arg Type", "green"),
              Field.ARG_USAGE: ("Arg Usage", "yellow"),
              Field.SUMMARY: ("Summary", "green"),
              Field.DESCRIPTION: ("Description", "white"),
              Field.MAN_ENTRY: ("Man Page Entry", "blue"),
              Field.IS_DEFAULT: ("Is Default", "magenta")}

def getFieldFromLabel(fieldLabel):
  """
  Converts field labels back to their enumeration, raising a ValueError if it
  doesn't exist.
  """
  
  for entryEnum in FIELD_ATTR:
    if fieldLabel == FIELD_ATTR[entryEnum][0]:
      return entryEnum

class ConfigEntry():
  """
  Configuration option in the panel.
  """
  
  def __init__(self, option, type, isDefault):
    self.fields = {}
    self.fields[Field.OPTION] = option
    self.fields[Field.TYPE] = type
    self.fields[Field.IS_DEFAULT] = isDefault
    
    # Fetches extra infromation from external sources (the arm config and tor
    # man page). These are None if unavailable for this config option.
    summary = torConfig.getConfigSummary(option)
    manEntry = torConfig.getConfigDescription(option)
    
    if manEntry:
      self.fields[Field.MAN_ENTRY] = manEntry.index
      self.fields[Field.CATEGORY] = manEntry.category
      self.fields[Field.ARG_USAGE] = manEntry.argUsage
      self.fields[Field.DESCRIPTION] = manEntry.description
    else:
      self.fields[Field.MAN_ENTRY] = 99999 # sorts non-man entries last
      self.fields[Field.CATEGORY] = torConfig.Category.UNKNOWN
      self.fields[Field.ARG_USAGE] = ""
      self.fields[Field.DESCRIPTION] = ""
    
    # uses the full man page description if a summary is unavailable
    self.fields[Field.SUMMARY] = summary if summary != None else self.fields[Field.DESCRIPTION]
    
    # cache of what's displayed for this configuration option
    self.labelCache = None
    self.labelCacheArgs = None
  
  def get(self, field):
    """
    Provides back the value in the given field.
    
    Arguments:
      field - enum for the field to be provided back
    """
    
    if field == Field.VALUE: return self._getValue()
    else: return self.fields[field]
  
  def getAll(self, fields):
    """
    Provides back a list with the given field values.
    
    Arguments:
      field - enums for the fields to be provided back
    """
    
    return [self.get(field) for field in fields]
  
  def getLabel(self, optionWidth, valueWidth, summaryWidth):
    """
    Provides display string of the configuration entry with the given
    constraints on the width of the contents.
    
    Arguments:
      optionWidth  - width of the option column
      valueWidth   - width of the value column
      summaryWidth - width of the summary column
    """
    
    # Fetching the display entries is very common so this caches the values.
    # Doing this substantially drops cpu usage when scrolling (by around 40%).
    
    argSet = (optionWidth, valueWidth, summaryWidth)
    if not self.labelCache or self.labelCacheArgs != argSet:
      optionLabel = uiTools.cropStr(self.get(Field.OPTION), optionWidth)
      valueLabel = uiTools.cropStr(self.get(Field.VALUE), valueWidth)
      summaryLabel = uiTools.cropStr(self.get(Field.SUMMARY), summaryWidth, None)
      lineTextLayout = "%%-%is %%-%is %%-%is" % (optionWidth, valueWidth, summaryWidth)
      self.labelCache = lineTextLayout % (optionLabel, valueLabel, summaryLabel)
      self.labelCacheArgs = argSet
    
    return self.labelCache
  
  def _getValue(self):
    """
    Provides the current value of the configuration entry, taking advantage of
    the torTools caching to effectively query the accurate value. This uses the
    value's type to provide a user friendly representation if able.
    """
    
    confValue = ", ".join(torTools.getConn().getOption(self.get(Field.OPTION), [], True))
    
    # provides nicer values for recognized types
    if not confValue: confValue = "<none>"
    elif self.get(Field.TYPE) == "Boolean" and confValue in ("0", "1"):
      confValue = "False" if confValue == "0" else "True"
    elif self.get(Field.TYPE) == "DataSize" and confValue.isdigit():
      confValue = uiTools.getSizeLabel(int(confValue))
    elif self.get(Field.TYPE) == "TimeInterval" and confValue.isdigit():
      confValue = uiTools.getTimeLabel(int(confValue), isLong = True)
    
    return confValue

class ConfigPanel(panel.Panel):
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
      
      sortFields = Field.values()
      customOrdering = config.getIntCSV("features.config.order", None, 3, 0, len(sortFields))
      
      if customOrdering:
        self.sortOrdering = [sortFields[i] for i in customOrdering]
    
    self.configType = configType
    self.confContents = []
    self.scroller = uiTools.Scroller(True)
    self.valsLock = threading.RLock()
    
    # shows all configuration options if true, otherwise only the ones with
    # the 'important' flag are shown
    self.showAll = False
    
    if self.configType == State.TOR:
      conn = torTools.getConn()
      customOptions = torConfig.getCustomOptions()
      configOptionLines = conn.getInfo("config/names", "").strip().split("\n")
      
      for line in configOptionLines:
        # lines are of the form "<option> <type>", like:
        # UseEntryGuards Boolean
        confOption, confType = line.strip().split(" ", 1)
        
        # skips private and virtual entries if not configured to show them
        if not self._config["features.config.state.showPrivateOptions"] and confOption.startswith("__"):
          continue
        elif not self._config["features.config.state.showVirtualOptions"] and confType == "Virtual":
          continue
        
        self.confContents.append(ConfigEntry(confOption, confType, not confOption in customOptions))
    elif self.configType == State.ARM:
      # loaded via the conf utility
      armConf = conf.getConfig("arm")
      for key in armConf.getKeys():
        pass # TODO: implement
    
    # mirror listing with only the important configuration options
    self.confImportantContents = []
    for entry in self.confContents:
      if torConfig.isImportant(entry.get(Field.OPTION)):
        self.confImportantContents.append(entry)
    
    # if there aren't any important options then show everything
    if not self.confImportantContents:
      self.confImportantContents = self.confContents
    
    self.setSortOrder() # initial sorting of the contents
  
  def getSelection(self):
    """
    Provides the currently selected entry.
    """
    
    return self.scroller.getCursorSelection(self._getConfigOptions())
  
  def setSortOrder(self, ordering = None):
    """
    Sets the configuration attributes we're sorting by and resorts the
    contents.
    
    Arguments:
      ordering - new ordering, if undefined then this resorts with the last
                 set ordering
    """
    
    self.valsLock.acquire()
    if ordering: self.sortOrdering = ordering
    self.confContents.sort(key=lambda i: (i.getAll(self.sortOrdering)))
    self.confImportantContents.sort(key=lambda i: (i.getAll(self.sortOrdering)))
    self.valsLock.release()
  
  def handleKey(self, key):
    self.valsLock.acquire()
    isKeystrokeConsumed = True
    if uiTools.isScrollKey(key):
      pageHeight = self.getPreferredSize()[0] - 1
      detailPanelHeight = self._config["features.config.selectionDetails.height"]
      if detailPanelHeight > 0 and detailPanelHeight + 2 <= pageHeight:
        pageHeight -= (detailPanelHeight + 1)
      
      isChanged = self.scroller.handleKey(key, self._getConfigOptions(), pageHeight)
      if isChanged: self.redraw(True)
    elif key == ord('a') or key == ord('A'):
      self.showAll = not self.showAll
      self.redraw(True)
    elif key == ord('s') or key == ord('S'):
      # set ordering for config options
      titleLabel = "Config Option Ordering:"
      options = [FIELD_ATTR[field][0] for field in Field.values()]
      oldSelection = [FIELD_ATTR[field][0] for field in self.sortOrdering]
      optionColors = dict([FIELD_ATTR[field] for field in Field.values()])
      results = popups.showSortDialog(titleLabel, options, oldSelection, optionColors)
      
      if results:
        # converts labels back to enums
        resultEnums = [getFieldFromLabel(label) for label in results]
        self.setSortOrder(resultEnums)
    else: isKeystrokeConsumed = False
    
    self.valsLock.release()
    return isKeystrokeConsumed
  
  def getHelp(self):
    options = []
    options.append(("up arrow", "scroll up a line", None))
    options.append(("down arrow", "scroll down a line", None))
    options.append(("page up", "scroll up a page", None))
    options.append(("page down", "scroll down a page", None))
    options.append(("enter", "edit configuration option", None))
    options.append(("w", "save configuration", None))
    options.append(("a", "toggle option filtering", None))
    options.append(("s", "sort ordering", None))
    return options
  
  def draw(self, width, height):
    self.valsLock.acquire()
    
    # panel with details for the current selection
    detailPanelHeight = self._config["features.config.selectionDetails.height"]
    isScrollbarVisible = False
    if detailPanelHeight == 0 or detailPanelHeight + 2 >= height:
      # no detail panel
      detailPanelHeight = 0
      scrollLoc = self.scroller.getScrollLoc(self._getConfigOptions(), height - 1)
      cursorSelection = self.getSelection()
      isScrollbarVisible = len(self._getConfigOptions()) > height - 1
    else:
      # Shrink detail panel if there isn't sufficient room for the whole
      # thing. The extra line is for the bottom border.
      detailPanelHeight = min(height - 1, detailPanelHeight + 1)
      scrollLoc = self.scroller.getScrollLoc(self._getConfigOptions(), height - 1 - detailPanelHeight)
      cursorSelection = self.getSelection()
      isScrollbarVisible = len(self._getConfigOptions()) > height - detailPanelHeight - 1
      
      self._drawSelectionPanel(cursorSelection, width, detailPanelHeight, isScrollbarVisible)
    
    # draws the top label
    if self.isTitleVisible():
      configType = "Tor" if self.configType == State.TOR else "Arm"
      hiddenMsg = "press 'a' to hide most options" if self.showAll else "press 'a' to show all options"
      titleLabel = "%s Configuration (%s):" % (configType, hiddenMsg)
      self.addstr(0, 0, titleLabel, curses.A_STANDOUT)
    
    # draws left-hand scroll bar if content's longer than the height
    scrollOffset = 1
    if isScrollbarVisible:
      scrollOffset = 3
      self.addScrollBar(scrollLoc, scrollLoc + height - detailPanelHeight - 1, len(self._getConfigOptions()), 1 + detailPanelHeight)
    
    optionWidth = self._config["features.config.state.colWidth.option"]
    valueWidth = self._config["features.config.state.colWidth.value"]
    descriptionWidth = max(0, width - scrollOffset - optionWidth - valueWidth - 2)
    
    for lineNum in range(scrollLoc, len(self._getConfigOptions())):
      entry = self._getConfigOptions()[lineNum]
      drawLine = lineNum + detailPanelHeight + 1 - scrollLoc
      
      lineFormat = curses.A_NORMAL if entry.get(Field.IS_DEFAULT) else curses.A_BOLD
      if entry.get(Field.CATEGORY): lineFormat |= uiTools.getColor(CATEGORY_COLOR[entry.get(Field.CATEGORY)])
      if entry == cursorSelection: lineFormat |= curses.A_STANDOUT
      
      lineText = entry.getLabel(optionWidth, valueWidth, descriptionWidth)
      self.addstr(drawLine, scrollOffset, lineText, lineFormat)
      
      if drawLine >= height: break
    
    self.valsLock.release()
  
  def _getConfigOptions(self):
    return self.confContents if self.showAll else self.confImportantContents
  
  def _drawSelectionPanel(self, selection, width, detailPanelHeight, isScrollbarVisible):
    """
    Renders a panel for the selected configuration option.
    """
    
    # This is a solid border unless the scrollbar is visible, in which case a
    # 'T' pipe connects the border to the bar.
    uiTools.drawBox(self, 0, 0, width, detailPanelHeight + 1)
    if isScrollbarVisible: self.addch(detailPanelHeight, 1, curses.ACS_TTEE)
    
    selectionFormat = curses.A_BOLD | uiTools.getColor(CATEGORY_COLOR[selection.get(Field.CATEGORY)])
    
    # first entry:
    # <option> (<category> Option)
    optionLabel =" (%s Option)" % selection.get(Field.CATEGORY)
    self.addstr(1, 2, selection.get(Field.OPTION) + optionLabel, selectionFormat)
    
    # second entry:
    # Value: <value> ([default|custom], <type>, usage: <argument usage>)
    if detailPanelHeight >= 3:
      valueAttr = []
      valueAttr.append("default" if selection.get(Field.IS_DEFAULT) else "custom")
      valueAttr.append(selection.get(Field.TYPE))
      valueAttr.append("usage: %s" % (selection.get(Field.ARG_USAGE)))
      valueAttrLabel = ", ".join(valueAttr)
      
      valueLabelWidth = width - 12 - len(valueAttrLabel)
      valueLabel = uiTools.cropStr(selection.get(Field.VALUE), valueLabelWidth)
      
      self.addstr(2, 2, "Value: %s (%s)" % (valueLabel, valueAttrLabel), selectionFormat)
    
    # remainder is filled with the man page description
    descriptionHeight = max(0, detailPanelHeight - 3)
    descriptionContent = "Description: " + selection.get(Field.DESCRIPTION)
    
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
        msg, remainder = uiTools.cropStr(lineContent, width - 2, 4, 4, uiTools.Ending.HYPHEN, True)
        descriptionContent = remainder.strip() + descriptionContent
      else:
        # this is the last line, end it with an ellipse
        msg = uiTools.cropStr(lineContent, width - 2, 4, 4)
      
      self.addstr(3 + i, 2, msg, selectionFormat)

