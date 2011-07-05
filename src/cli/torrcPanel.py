"""
Panel displaying the torrc or armrc with the validation done against it.
"""

import math
import curses
import threading

import popups

from util import conf, enum, panel, torConfig, torTools, uiTools

DEFAULT_CONFIG = {"features.config.file.showScrollbars": True,
                  "features.config.file.maxLinesPerEntry": 8}

# TODO: The armrc use case is incomplete. There should be equivilant reloading
# and validation capabilities to the torrc.
Config = enum.Enum("TORRC", "ARMRC") # configuration file types that can be displayed

class TorrcPanel(panel.Panel):
  """
  Renders the current torrc or armrc with syntax highlighting in a scrollable
  area.
  """
  
  def __init__(self, stdscr, configType, config=None):
    panel.Panel.__init__(self, stdscr, "torrc", 0)
    
    self._config = dict(DEFAULT_CONFIG)
    if config:
      config.update(self._config, {"features.config.file.maxLinesPerEntry": 1})
    
    self.valsLock = threading.RLock()
    self.configType = configType
    self.scroll = 0
    self.showLineNum = True     # shows left aligned line numbers
    self.stripComments = False  # drops comments and extra whitespace
    
    # height of the content when last rendered (the cached value is invalid if
    # _lastContentHeightArgs is None or differs from the current dimensions)
    self._lastContentHeight = 1
    self._lastContentHeightArgs = None
    
    # listens for tor reload (sighup) events
    conn = torTools.getConn()
    conn.addStatusListener(self.resetListener)
    if conn.isAlive(): self.resetListener(conn, torTools.State.INIT)
  
  def resetListener(self, conn, eventType):
    """
    Reloads and displays the torrc on tor reload (sighup) events.
    
    Arguments:
      conn      - tor controller
      eventType - type of event detected
    """
    
    if eventType == torTools.State.INIT:
      # loads the torrc and provides warnings in case of validation errors
      try:
        loadedTorrc = torConfig.getTorrc()
        loadedTorrc.load(True)
        loadedTorrc.logValidationIssues()
        self.redraw(True)
      except: pass
    elif eventType == torTools.State.RESET:
      try:
        torConfig.getTorrc().load(True)
        self.redraw(True)
      except: pass
  
  def setCommentsVisible(self, isVisible):
    """
    Sets if comments and blank lines are shown or stripped.
    
    Arguments:
      isVisible - displayed comments and blank lines if true, strips otherwise
    """
    
    self.stripComments = not isVisible
    self._lastContentHeightArgs = None
    self.redraw(True)
  
  def setLineNumberVisible(self, isVisible):
    """
    Sets if line numbers are shown or hidden.
    
    Arguments:
      isVisible - displays line numbers if true, hides otherwise
    """
    
    self.showLineNum = isVisible
    self._lastContentHeightArgs = None
    self.redraw(True)
  
  def reloadTorrc(self):
    """
    Reloads the torrc, displaying an indicator of success or failure.
    """
    
    try:
      torConfig.getTorrc().load()
      self._lastContentHeightArgs = None
      self.redraw(True)
      resultMsg = "torrc reloaded"
    except IOError:
      resultMsg = "failed to reload torrc"
    
    self._lastContentHeightArgs = None
    self.redraw(True)
    popups.showMsg(resultMsg, 1)
  
  def handleKey(self, key):
    self.valsLock.acquire()
    isKeystrokeConsumed = True
    if uiTools.isScrollKey(key):
      pageHeight = self.getPreferredSize()[0] - 1
      newScroll = uiTools.getScrollPosition(key, self.scroll, pageHeight, self._lastContentHeight)
      
      if self.scroll != newScroll:
        self.scroll = newScroll
        self.redraw(True)
    elif key == ord('n') or key == ord('N'):
      self.setLineNumberVisible(not self.showLineNum)
    elif key == ord('s') or key == ord('S'):
      self.setCommentsVisible(self.stripComments)
    elif key == ord('r') or key == ord('R'):
      self.reloadTorrc()
    else: isKeystrokeConsumed = False
    
    self.valsLock.release()
    return isKeystrokeConsumed
  
  def setVisible(self, isVisible):
    if not isVisible:
      self._lastContentHeightArgs = None # redraws when next displayed
    
    panel.Panel.setVisible(self, isVisible)
  
  def getHelp(self):
    options = []
    options.append(("up arrow", "scroll up a line", None))
    options.append(("down arrow", "scroll down a line", None))
    options.append(("page up", "scroll up a page", None))
    options.append(("page down", "scroll down a page", None))
    options.append(("s", "comment stripping", "on" if self.stripComments else "off"))
    options.append(("n", "line numbering", "on" if self.showLineNum else "off"))
    options.append(("r", "reload torrc", None))
    options.append(("x", "reset tor (issue sighup)", None))
    return options
  
  def draw(self, width, height):
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
    if self.configType == Config.TORRC:
      loadedTorrc = torConfig.getTorrc()
      loadedTorrc.getLock().acquire()
      confLocation = loadedTorrc.getConfigLocation()
      
      if not loadedTorrc.isLoaded():
        renderedContents = ["### Unable to load the torrc ###"]
      else:
        renderedContents = loadedTorrc.getDisplayContents(self.stripComments)
        
        # constructs a mapping of line numbers to the issue on it
        corrections = dict((lineNum, (issue, msg)) for lineNum, issue, msg in loadedTorrc.getCorrections())
      
      loadedTorrc.getLock().release()
    else:
      loadedArmrc = conf.getConfig("arm")
      confLocation = loadedArmrc.path
      renderedContents = list(loadedArmrc.rawContents)
    
    # offset to make room for the line numbers
    lineNumOffset = 0
    if self.showLineNum:
      if len(renderedContents) == 0: lineNumOffset = 2
      else: lineNumOffset = int(math.log10(len(renderedContents))) + 2
    
    # draws left-hand scroll bar if content's longer than the height
    scrollOffset = 0
    if self._config["features.config.file.showScrollbars"] and self._lastContentHeight > height - 1:
      scrollOffset = 3
      self.addScrollBar(self.scroll, self.scroll + height - 1, self._lastContentHeight, 1)
    
    displayLine = -self.scroll + 1 # line we're drawing on
    
    # draws the top label
    if self.isTitleVisible():
      sourceLabel = "Tor" if self.configType == Config.TORRC else "Arm"
      locationLabel = " (%s)" % confLocation if confLocation else ""
      self.addstr(0, 0, "%s Configuration File%s:" % (sourceLabel, locationLabel), curses.A_STANDOUT)
    
    isMultiline = False # true if we're in the middle of a multiline torrc entry
    for lineNumber in range(0, len(renderedContents)):
      lineText = renderedContents[lineNumber]
      lineText = lineText.rstrip() # remove ending whitespace
      
      # blank lines are hidden when stripping comments
      if self.stripComments and not lineText: continue
      
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
      if isMultiline:
        # part of a multiline entry started on a previous line so everything
        # is part of the argument
        lineComp["argument"][0] = lineText
      elif optionIndex == -1:
        # no argument provided
        lineComp["option"][0] = lineText
      else:
        optionText = strippedLine[:optionIndex]
        optionEnd = lineText.find(optionText) + len(optionText)
        lineComp["option"][0] = lineText[:optionEnd]
        lineComp["argument"][0] = lineText[optionEnd:]
      
      # flags following lines as belonging to this multiline entry if it ends
      # with a slash
      if strippedLine: isMultiline = strippedLine.endswith("\\")
      
      # gets the correction
      if lineNumber in corrections:
        lineIssue, lineIssueMsg = corrections[lineNumber]
        
        if lineIssue in (torConfig.ValidationError.DUPLICATE, torConfig.ValidationError.IS_DEFAULT):
          lineComp["option"][1] = curses.A_BOLD | uiTools.getColor("blue")
          lineComp["argument"][1] = curses.A_BOLD | uiTools.getColor("blue")
        elif lineIssue == torConfig.ValidationError.MISMATCH:
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
      maxLinesPerEntry = self._config["features.config.file.maxLinesPerEntry"]
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
            msg, remainder = uiTools.cropStr(msg, maxMsgSize, 4, 4, uiTools.Ending.HYPHEN, True)
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

