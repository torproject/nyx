"""
Panel presenting the configuration state for tor or arm. Options can be edited
and the resulting configuration files saved.
"""

import curses
import threading

import cli.controller
import popups

from util import conf, enum, panel, sysTools, torConfig, torTools, uiTools

DEFAULT_CONFIG = {"features.config.selectionDetails.height": 6,
                  "features.config.prepopulateEditValues": True,
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
  
  def isUnset(self):
    """
    True if we have no value, false otherwise.
    """
    
    confValue = torTools.getConn().getOption(self.get(Field.OPTION), [], True)
    return not bool(confValue)
  
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
    panel.Panel.__init__(self, stdscr, "configuration", 0)
    
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
    self.confImportantContents = []
    self.scroller = uiTools.Scroller(True)
    self.valsLock = threading.RLock()
    
    # shows all configuration options if true, otherwise only the ones with
    # the 'important' flag are shown
    self.showAll = False
    
    # initializes config contents if we're connected
    conn = torTools.getConn()
    conn.addStatusListener(self.resetListener)
    if conn.isAlive(): self.resetListener(conn, torTools.State.INIT)
  
  def resetListener(self, conn, eventType):
    # fetches configuration options if a new instance, otherewise keeps our
    # current contents
    
    if eventType == torTools.State.INIT:
      self._loadConfigOptions()
  
  def _loadConfigOptions(self):
    """
    Fetches the configuration options available from tor or arm.
    """
    
    self.confContents = []
    self.confImportantContents = []
    
    if self.configType == State.TOR:
      conn, configOptionLines = torTools.getConn(), []
      customOptions = torConfig.getCustomOptions()
      configOptionQuery = conn.getInfo("config/names")
      
      if configOptionQuery:
        configOptionLines = configOptionQuery.strip().split("\n")
      
      for line in configOptionLines:
        # lines are of the form "<option> <type>[ <documentation>]", like:
        # UseEntryGuards Boolean
        # documentation is aparently only in older versions (for instance,
        # 0.2.1.25)
        lineComp = line.strip().split(" ")
        confOption, confType = lineComp[0], lineComp[1]
        
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
  
  def setFiltering(self, isFiltered):
    """
    Sets if configuration options are filtered or not.
    
    Arguments:
      isFiltered - if true then only relatively important options will be
                   shown, otherwise everything is shown
    """
    
    self.showAll = not isFiltered
  
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
  
  def showSortDialog(self):
    """
    Provides the sort dialog for our configuration options.
    """
    
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
    elif uiTools.isSelectionKey(key) and self._getConfigOptions():
      # Prompts the user to edit the selected configuration value. The
      # interface is locked to prevent updates between setting the value
      # and showing any errors.
      
      panel.CURSES_LOCK.acquire()
      try:
        selection = self.getSelection()
        configOption = selection.get(Field.OPTION)
        if selection.isUnset(): initialValue = ""
        else: initialValue = selection.get(Field.VALUE)
        
        promptMsg = "%s Value (esc to cancel): " % configOption
        isPrepopulated = self._config["features.config.prepopulateEditValues"]
        newValue = popups.inputPrompt(promptMsg, initialValue if isPrepopulated else "")
        
        if newValue != None and newValue != initialValue:
          try:
            if selection.get(Field.TYPE) == "Boolean":
              # if the value's a boolean then allow for 'true' and 'false' inputs
              if newValue.lower() == "true": newValue = "1"
              elif newValue.lower() == "false": newValue = "0"
            elif selection.get(Field.TYPE) == "LineList":
              # setOption accepts list inputs when there's multiple values
              newValue = newValue.split(",")
            
            torTools.getConn().setOption(configOption, newValue)
            
            # forces the label to be remade with the new value
            selection.labelCache = None
            
            # resets the isDefault flag
            customOptions = torConfig.getCustomOptions()
            selection.fields[Field.IS_DEFAULT] = not configOption in customOptions
            
            self.redraw(True)
          except Exception, exc:
            popups.showMsg("%s (press any key)" % exc)
      finally:
        panel.CURSES_LOCK.release()
    elif key == ord('a') or key == ord('A'):
      self.showAll = not self.showAll
      self.redraw(True)
    elif key == ord('s') or key == ord('S'):
      self.showSortDialog()
    elif key == ord('v') or key == ord('V'):
      self.showWriteDialog()
    else: isKeystrokeConsumed = False
    
    self.valsLock.release()
    return isKeystrokeConsumed
  
  def showWriteDialog(self):
    """
    Provies an interface to confirm if the configuration is saved and, if so,
    where.
    """
    
    # display a popup for saving the current configuration
    configLines = torConfig.getCustomOptions(True)
    popup, width, height = popups.init(len(configLines) + 2)
    if not popup: return
    
    try:
      # displayed options (truncating the labels if there's limited room)
      if width >= 30: selectionOptions = ("Save", "Save As...", "Cancel")
      else: selectionOptions = ("Save", "Save As", "X")
      
      # checks if we can show options beside the last line of visible content
      isOptionLineSeparate = False
      lastIndex = min(height - 2, len(configLines) - 1)
      
      # if we don't have room to display the selection options and room to
      # grow then display the selection options on its own line
      if width < (30 + len(configLines[lastIndex])):
        popup.setHeight(height + 1)
        popup.redraw(True) # recreates the window instance
        newHeight, _ = popup.getPreferredSize()
        
        if newHeight > height:
          height = newHeight
          isOptionLineSeparate = True
      
      key, selection = 0, 2
      while not uiTools.isSelectionKey(key):
        # if the popup has been resized then recreate it (needed for the
        # proper border height)
        newHeight, newWidth = popup.getPreferredSize()
        if (height, width) != (newHeight, newWidth):
          height, width = newHeight, newWidth
          popup.redraw(True)
        
        # if there isn't room to display the popup then cancel it
        if height <= 2:
          selection = 2
          break
        
        popup.win.erase()
        popup.win.box()
        popup.addstr(0, 0, "Configuration being saved:", curses.A_STANDOUT)
        
        visibleConfigLines = height - 3 if isOptionLineSeparate else height - 2
        for i in range(visibleConfigLines):
          line = uiTools.cropStr(configLines[i], width - 2)
          
          if " " in line:
            option, arg = line.split(" ", 1)
            popup.addstr(i + 1, 1, option, curses.A_BOLD | uiTools.getColor("green"))
            popup.addstr(i + 1, len(option) + 2, arg, curses.A_BOLD | uiTools.getColor("cyan"))
          else:
            popup.addstr(i + 1, 1, line, curses.A_BOLD | uiTools.getColor("green"))
        
        # draws selection options (drawn right to left)
        drawX = width - 1
        for i in range(len(selectionOptions) - 1, -1, -1):
          optionLabel = selectionOptions[i]
          drawX -= (len(optionLabel) + 2)
          
          # if we've run out of room then drop the option (this will only
          # occure on tiny displays)
          if drawX < 1: break
          
          selectionFormat = curses.A_STANDOUT if i == selection else curses.A_NORMAL
          popup.addstr(height - 2, drawX, "[")
          popup.addstr(height - 2, drawX + 1, optionLabel, selectionFormat | curses.A_BOLD)
          popup.addstr(height - 2, drawX + len(optionLabel) + 1, "]")
          
          drawX -= 1 # space gap between the options
        
        popup.win.refresh()
        
        key = cli.controller.getController().getScreen().getch()
        if key == curses.KEY_LEFT: selection = max(0, selection - 1)
        elif key == curses.KEY_RIGHT: selection = min(len(selectionOptions) - 1, selection + 1)
      
      if selection in (0, 1):
        loadedTorrc, promptCanceled = torConfig.getTorrc(), False
        try: configLocation = loadedTorrc.getConfigLocation()
        except IOError: configLocation = ""
        
        if selection == 1:
          # prompts user for a configuration location
          configLocation = popups.inputPrompt("Save to (esc to cancel): ", configLocation)
          if not configLocation: promptCanceled = True
        
        if not promptCanceled:
          try:
            torConfig.saveConf(configLocation, configLines)
            msg = "Saved configuration to %s" % configLocation
          except IOError, exc:
            msg = "Unable to save configuration (%s)" % sysTools.getFileErrorMsg(exc)
          
          popups.showMsg(msg, 2)
    finally: popups.finalize()
  
  def getHelp(self):
    options = []
    options.append(("up arrow", "scroll up a line", None))
    options.append(("down arrow", "scroll down a line", None))
    options.append(("page up", "scroll up a page", None))
    options.append(("page down", "scroll down a page", None))
    options.append(("enter", "edit configuration option", None))
    options.append(("v", "save configuration", None))
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
      
      if cursorSelection != None:
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
    
    # if the description column is overly long then use its space for the
    # value instead
    if descriptionWidth > 80:
      valueWidth += descriptionWidth - 80
      descriptionWidth = 80
    
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
        msg, remainder = uiTools.cropStr(lineContent, width - 3, 4, 4, uiTools.Ending.HYPHEN, True)
        descriptionContent = remainder.strip() + descriptionContent
      else:
        # this is the last line, end it with an ellipse
        msg = uiTools.cropStr(lineContent, width - 3, 4, 4)
      
      self.addstr(3 + i, 2, msg, selectionFormat)

