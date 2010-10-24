"""
Panel displaying the torrc and validation done against it.
"""

import math
import curses
import threading

from util import conf, log, panel, torrc, torTools, uiTools

DEFAULT_CONFIG = {"features.config.type": 0,
                  "features.config.validate": True,
                  "features.config.showScrollbars": True,
                  "features.config.maxLinesPerEntry": 8,
                  "log.confPanel.torrcReadFailed": log.WARN,
                  "log.torrcValidation.duplicateEntries": log.NOTICE,
                  "log.torrcValidation.torStateDiffers": log.NOTICE}

# configurations that can be displayed
TOR_STATE, TORRC, ARM_STATE, ARMRC = range(4)
CONFIG_LABELS = {TORRC: "torrc", TOR_STATE: "tor state", ARMRC: "armrc", ARM_STATE: "arm state"}

class ConfPanel(panel.Panel):
  """
  Presents torrc, armrc, or loaded settings with syntax highlighting in a
  scrollable area.
  """
  
  def __init__(self, stdscr, config=None):
    panel.Panel.__init__(self, stdscr, "conf", 0)
    
    self._config = dict(DEFAULT_CONFIG)
    if config:
      config.update(self._config, {
        "features.config.type": (0, 3),
        "features.config.maxLinesPerEntry": 1})
    
    self.valsLock = threading.RLock()
    self.scroll = 0
    self.showLabel = True         # shows top label if true, hides otherwise
    self.showLineNum = True
    self.stripComments = False
    
    # type of config currently being displayed
    self.configType = self._config["features.config.type"]
    
    # Mappings of config types to tuples of:
    # (contents, corrections, confLocation)
    # This maps to None if they haven't been loaded yet or failed to load.
    self.configs = {TORRC: None, TOR_STATE: None, ARMRC: None, ARM_STATE: None}
    
    # height of the content when last rendered (the cached value is invalid if
    # _lastContentHeightArgs is None or differs from the current dimensions)
    self._lastContentHeight = 1
    self._lastContentHeightArgs = None
    
    self.loadConfig(TOR_STATE)
    self.loadConfig(TORRC)
  
  def loadConfig(self, configType = None, logErrors = True):
    """
    Reloads configuration or state contents and resets scroll height. Returns
    True if successful, else false.
    
    Arguments:
      configType - configuration type to load (displayed config type if None)
      logErrors  - logs if unable to read the torrc or issues are found during
                   validation
    """
    
    self.valsLock.acquire()
    if configType == None: configType = self.configType
    confContents, corrections, confLocation = [], {}, None
    
    if configType in (TORRC, ARMRC):
      # load configuration file
      try:
        if configType == TORRC: confLocation = torrc.getConfigLocation()
        else:
          confLocation = conf.getConfig("arm").path
          if not confLocation: raise IOError("no armrc has been loaded")
        
        confFile = open(confLocation, "r")
        confContents = confFile.readlines()
        confFile.close()
        self.scroll = 0
      except IOError, exc:
        self.configs[configType] = None
        msg = "Unable to load torrc (%s)" % exc
        if logErrors: log.log(self._config["log.confPanel.torrcReadFailed"], msg)
        self.valsLock.release()
        return False
      
      if configType == TORRC and self._config["features.config.validate"]:
        # TODO: add armrc validation
        corrections = torrc.validate(confContents)
        
        if corrections and logErrors:
          # logs issues found during validation
          irrelevantLines, mismatchLines = [], []
          for lineNum in corrections:
            problem = corrections[lineNum][0]
            if problem == torrc.VAL_DUPLICATE: irrelevantLines.append(lineNum)
            elif problem == torrc.VAL_MISMATCH: mismatchLines.append(lineNum)
          
          if irrelevantLines:
            irrelevantLines.sort()
            
            if len(irrelevantLines) > 1: first, second, third = "Entries", "are", ", including lines"
            else: first, second, third = "Entry", "is", " on line"
            msgStart = "%s in your torrc %s ignored due to duplication%s" % (first, second, third)
            msgLines = ", ".join([str(val + 1) for val in irrelevantLines])
            msg = "%s: %s (highlighted in blue)" % (msgStart, msgLines)
            log.log(self._config["log.torrcValidation.duplicateEntries"], msg)
          
          if mismatchLines:
            mismatchLines.sort()
            msgStart = "Tor's state differs from loaded torrc on line%s" % ("s" if len(mismatchLines) > 1 else "")
            msgLines = ", ".join([str(val + 1) for val in mismatchLines])
            msg = "%s: %s" % (msgStart, msgLines)
            log.log(self._config["log.torrcValidation.torStateDiffers"], msg)
      
      if confContents:
        # Restricts contents to be displayable characters:
        # - Tabs print as three spaces. Keeping them as tabs is problematic for
        #   the layout since it's counted as a single character, but occupies
        #   several cells.
        # - Strips control and unprintable characters.
        for lineNum in range(len(confContents)):
          lineText = confContents[lineNum]
          lineText = lineText.replace("\t", "   ")
          lineText = "".join([char for char in lineText if curses.ascii.isprint(char)])
          confContents[lineNum] = lineText
    elif configType == TOR_STATE:
      # for all recognized tor config options, provide their current value
      conn = torTools.getConn()
      configOptionQuery = conn.getInfo("config/names", "").strip().split("\n")
      
      for lineNum in range(len(configOptionQuery)):
        # lines are of the form "<option> <type>", like:
        # UseEntryGuards Boolean
        line = configOptionQuery[lineNum]
        confOption, confType = line.strip().split(" ", 1)
        confValue = ", ".join(conn.getOption(confOption, [], True))
        
        # provides nicer values for recognized types
        if not confValue: confValue = "<none>"
        elif confType == "Boolean" and confValue in ("0", "1"):
          confValue = "False" if confValue == "0" else "True"
        elif confType == "DataSize" and confValue.isdigit():
          confValue = uiTools.getSizeLabel(int(confValue))
        elif confType == "TimeInterval" and confValue.isdigit():
          confValue = uiTools.getTimeLabel(int(confValue), isLong = True)
        
        confContents.append("%s %s\n" % (confOption, confValue))
        
        # hijacks the correction field to display the value's type
        corrections[lineNum] = (None, confType)
    elif configType == ARM_STATE:
      # loaded via the conf utility
      armConf = conf.getConfig("arm")
      for key in armConf.getKeys():
        confContents.append("%s %s\n" % (key, ", ".join(armConf.getValue(key, [], True))))
      confContents.sort()
    
    self.configs[configType] = (confContents, corrections, confLocation)
    
    # sets the content height to be something somewhat reasonable
    self._lastContentHeight = len(confContents)
    self._lastContentHeightArgs = None
    
    self.valsLock.release()
    return True
  
  def handleKey(self, key):
    self.valsLock.acquire()
    if uiTools.isScrollKey(key) and self.configs[self.configType] != None:
      pageHeight = self.getPreferredSize()[0] - 1
      newScroll = uiTools.getScrollPosition(key, self.scroll, pageHeight, self._lastContentHeight)
      
      if self.scroll != newScroll:
        self.scroll = newScroll
        self.redraw(True)
    elif key == ord('n') or key == ord('N'):
      self.showLineNum = not self.showLineNum
      self._lastContentHeightArgs = None
      self.redraw(True)
    elif key == ord('s') or key == ord('S'):
      self.stripComments = not self.stripComments
      self._lastContentHeightArgs = None
      self.redraw(True)
    
    self.valsLock.release()
  
  def setConfigType(self, configType):
    """
    Sets the type of configuration to be displayed. If the configuration isn't
    already loaded then this fetches it.
    
    Arguments
      configType - enum representing the type of configuration to be loaded
    """
    
    if self.configType != configType or not self.configs[configType]:
      self.valsLock.acquire()
      self.configType = configType
      
      if not self.configs[configType]: self.loadConfig()
      
      self._lastContentHeightArgs = None
      self.redraw(True)
      self.valsLock.release()
  
  def draw(self, subwindow, width, height):
    self.valsLock.acquire()
    
    # If true, we assume that the cached value in self._lastContentHeight is
    # still accurate, and stop drawing when there's nothing more to display.
    # Otherwise the self._lastContentHeight is suspect, and we'll process all
    # the content to check if it's right (and redraw again with the corrected
    # height if not).
    trustLastContentHeight = self._lastContentHeightArgs == (width, height)
    
    # restricts scroll location to valid bounds
    self.scroll = max(0, min(self.scroll, self._lastContentHeight - height + 1))
    
    renderedContents, corrections, confLocation = None, {}, None
    if self.configs[self.configType]:
      renderedContents, corrections, confLocation = self.configs[self.configType]
    
    if renderedContents == None:
      renderedContents = ["### Unable to load the %s ###" % CONFIG_LABELS[self.configType]]
    elif self.stripComments:
      renderedContents = torrc.stripComments(renderedContents)
    
    # offset to make room for the line numbers
    lineNumOffset = int(math.log10(len(renderedContents))) + 2 if self.showLineNum else 0
    
    # draws left-hand scroll bar if content's longer than the height
    scrollOffset = 0
    if self._config["features.config.showScrollbars"] and self._lastContentHeight > height - 1:
      scrollOffset = 3
      self.addScrollBar(self.scroll, self.scroll + height - 1, self._lastContentHeight, 1)
    
    displayLine = -self.scroll + 1 # line we're drawing on
    
    # draws the top label
    if self.showLabel:
      sourceLabel = "Tor" if self.configType in (TORRC, TOR_STATE) else "Arm"
      typeLabel = "Config" if self.configType in (TORRC, ARMRC) else "State"
      locationLabel = " (%s)" % confLocation if confLocation else ""
      self.addstr(0, 0, "%s %s%s:" % (sourceLabel, typeLabel, locationLabel), curses.A_STANDOUT)
    
    for lineNumber in range(0, len(renderedContents)):
      lineText = renderedContents[lineNumber]
      lineText = lineText.rstrip() # remove ending whitespace
      
      # blank lines are hidden when stripping comments, and undefined
      # values are dropped if showing tor's state
      if self.stripComments:
        if not lineText: continue
        elif self.configType == TOR_STATE and "<none>" in lineText: continue
      
      # splits the line into its component (msg, format) tuples
      lineComp = {"option": ["", curses.A_BOLD | uiTools.getColor("green")],
                  "argument": ["", curses.A_BOLD | uiTools.getColor("cyan")],
                  "correction": ["", curses.A_BOLD | uiTools.getColor("cyan")],
                  "comment": ["", uiTools.getColor("white")]}
      
      # parses the comment
      commentIndex = lineText.find("#")
      if commentIndex != -1:
        lineComp["comment"][0] = lineText[commentIndex:]
        lineText = lineText[:commentIndex]
      
      # splits the option and argument, preserving any whitespace around them
      strippedLine = lineText.strip()
      optionIndex = strippedLine.find(" ")
      if optionIndex == -1:
        lineComp["option"][0] = lineText # no argument provided
      else:
        optionText = strippedLine[:optionIndex]
        optionEnd = lineText.find(optionText) + len(optionText)
        lineComp["option"][0] = lineText[:optionEnd]
        lineComp["argument"][0] = lineText[optionEnd:]
      
      # gets the correction
      if lineNumber in corrections:
        lineIssue, lineIssueMsg = corrections[lineNumber]
        
        if lineIssue == torrc.VAL_DUPLICATE:
          lineComp["option"][1] = curses.A_BOLD | uiTools.getColor("blue")
          lineComp["argument"][1] = curses.A_BOLD | uiTools.getColor("blue")
        elif lineIssue == torrc.VAL_MISMATCH:
          lineComp["argument"][1] = curses.A_BOLD | uiTools.getColor("red")
          lineComp["correction"][0] = " (%s)" % lineIssueMsg
        else:
          # For some types of configs the correction field is simply used to
          # provide extra data (for instance, the type for tor state fields).
          lineComp["correction"][0] = " (%s)" % lineIssueMsg
          lineComp["correction"][1] = curses.A_BOLD | uiTools.getColor("magenta")
      
      # draws the line number
      if self.showLineNum and displayLine < height and displayLine >= 1:
        lineNumStr = ("%%%ii" % (lineNumOffset - 1)) % (lineNumber + 1)
        self.addstr(displayLine, scrollOffset, lineNumStr, curses.A_BOLD | uiTools.getColor("yellow"))
      
      # draws the rest of the components with line wrap
      cursorLoc, lineOffset = lineNumOffset + scrollOffset, 0
      maxLinesPerEntry = self._config["features.config.maxLinesPerEntry"]
      displayQueue = [lineComp[entry] for entry in ("option", "argument", "correction", "comment")]
      
      while displayQueue:
        msg, format = displayQueue.pop(0)
        
        maxMsgSize, includeBreak = width - cursorLoc, False
        if len(msg) >= maxMsgSize:
          # message is too long - break it up
          if lineOffset == maxLinesPerEntry - 1:
            msg = uiTools.cropStr(msg, maxMsgSize)
          else:
            includeBreak = True
            msg, remainder = uiTools.cropStr(msg, maxMsgSize, 4, 4, uiTools.END_WITH_HYPHEN, True)
            displayQueue.insert(0, (remainder.strip(), format))
        
        drawLine = displayLine + lineOffset
        if msg and drawLine < height and drawLine >= 1:
          self.addstr(drawLine, cursorLoc, msg, format)
        
        # If we're done, and have added content to this line, then start
        # further content on the next line.
        cursorLoc += len(msg)
        includeBreak |= not displayQueue and cursorLoc != lineNumOffset + scrollOffset
        
        if includeBreak:
          lineOffset += 1
          cursorLoc = lineNumOffset + scrollOffset
      
      displayLine += max(lineOffset, 1)
      
      if trustLastContentHeight and displayLine >= height: break
    
    if not trustLastContentHeight:
      self._lastContentHeightArgs = (width, height)
      newContentHeight = displayLine + self.scroll - 1
      
      if self._lastContentHeight != newContentHeight:
        self._lastContentHeight = newContentHeight
        self.redraw(True)
    
    self.valsLock.release()
  
  def redraw(self, forceRedraw=False, block=False):
    panel.Panel.redraw(self, forceRedraw, block)

