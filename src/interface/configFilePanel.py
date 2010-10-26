"""
Panel displaying the torrc or armrc with the validation done against it.
"""

import math
import curses
import threading

from util import conf, panel, torrc, uiTools

DEFAULT_CONFIG = {"features.config.showScrollbars": True,
                  "features.config.maxLinesPerEntry": 8}

TORRC, ARMRC = range(1, 3) # configuration file types that can  be displayed

class ConfigFilePanel(panel.Panel):
  """
  Renders the current torrc or armrc with syntax highlighting in a scrollable
  area.
  """
  
  def __init__(self, stdscr, configType, config=None):
    panel.Panel.__init__(self, stdscr, "conf", 0)
    
    self._config = dict(DEFAULT_CONFIG)
    if config:
      config.update(self._config, {"features.config.maxLinesPerEntry": 1})
    
    self.valsLock = threading.RLock()
    self.configType = configType
    self.scroll = 0
    self.showLabel = True       # shows top label (hides otherwise)
    self.showLineNum = True     # shows left aligned line numbers
    self.stripComments = False  # drops comments and extra whitespace
    
    # height of the content when last rendered (the cached value is invalid if
    # _lastContentHeightArgs is None or differs from the current dimensions)
    self._lastContentHeight = 1
    self._lastContentHeightArgs = None
  
  def handleKey(self, key):
    self.valsLock.acquire()
    if uiTools.isScrollKey(key):
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
    if self.configType == TORRC:
      loadedTorrc = torrc.getTorrc()
      loadedTorrc.getLock().acquire()
      confLocation = loadedTorrc.getConfigLocation()
      
      if not loadedTorrc.isLoaded():
        renderedContents = ["### Unable to load the torrc ###"]
      else:
        renderedContents = loadedTorrc.getDisplayContents(self.stripComments)
        corrections = loadedTorrc.getCorrections()
      
      loadedTorrc.getLock().release()
    else:
      # TODO: The armrc use case is incomplete. There should be equivilant
      # reloading and validation capabilities to the torrc.
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
    if self._config["features.config.showScrollbars"] and self._lastContentHeight > height - 1:
      scrollOffset = 3
      self.addScrollBar(self.scroll, self.scroll + height - 1, self._lastContentHeight, 1)
    
    displayLine = -self.scroll + 1 # line we're drawing on
    
    # draws the top label
    if self.showLabel:
      sourceLabel = "Tor" if self.configType == TORRC else "Arm"
      locationLabel = " (%s)" % confLocation if confLocation else ""
      self.addstr(0, 0, "%s Config%s:" % (sourceLabel, locationLabel), curses.A_STANDOUT)
    
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

