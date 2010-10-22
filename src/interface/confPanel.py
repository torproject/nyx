"""
Panel displaying the torrc and validation done against it.
"""

import math
import curses
import threading

from util import log, panel, torrc, uiTools

DEFAULT_CONFIG = {"features.torrc.validate": True,
                  "features.config.showScrollbars": True,
                  "features.config.maxLinesPerEntry": 5,
                  "log.confPanel.torrcReadFailed": log.WARN,
                  "log.torrcValidation.duplicateEntries": log.NOTICE,
                  "log.torrcValidation.torStateDiffers": log.NOTICE}

class ConfPanel(panel.Panel):
  """
  Presents torrc, armrc, or loaded settings with syntax highlighting in a
  scrollable area.
  """
  
  def __init__(self, stdscr, config=None):
    panel.Panel.__init__(self, stdscr, "conf", 0)
    
    self._config = dict(DEFAULT_CONFIG)
    if config:
      config.update(self._config, {"features.config.maxLinesPerEntry": 1})
    
    self.valsLock = threading.RLock()
    self.scroll = 0
    self.showLineNum = True
    self.stripComments = False
    self.confLocation = ""
    self.confContents = None # read torrc, None if it failed to load
    self.corrections = {}
    
    # height of the content when last rendered (the cached value is invalid if
    # _lastContentHeightArgs is None or differs from the current dimensions)
    self._lastContentHeight = 1
    self._lastContentHeightArgs = None
    
    self.reset()
  
  def reset(self, logErrors = True):
    """
    Reloads torrc contents and resets scroll height. Returns True if
    successful, else false.
    
    Arguments:
      logErrors - logs if unable to read the torrc or issues are found during
                  validation
    """
    
    self.valsLock.acquire()
    
    try:
      self.confLocation = torrc.getConfigLocation()
      confFile = open(self.confLocation, "r")
      self.confContents = confFile.readlines()
      confFile.close()
      self.scroll = 0
      
      # sets the content height to be something somewhat reasonable
      self._lastContentHeight = len(self.confContents)
      self._lastContentHeightArgs = None
    except IOError, exc:
      self.confContents = None
      msg = "Unable to load torrc (%s)" % exc
      if logErrors: log.log(self._config["log.confPanel.torrcReadFailed"], msg)
      self.valsLock.release()
      return False
    
    if self._config["features.torrc.validate"]:
      self.corrections = torrc.validate(self.confContents)
      
      if self.corrections and logErrors:
        # logs issues found during validation
        irrelevantLines, mismatchLines = [], []
        for lineNum in self.corrections:
          problem = self.corrections[lineNum][0]
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
    
    if self.confContents:
      # Restricts contents to be displayable characters:
      # - Tabs print as three spaces. Keeping them as tabs is problematic for
      #   the layout since it's counted as a single character, but occupies
      #   several cells.
      # - Strips control and unprintable characters.
      for lineNum in range(len(self.confContents)):
        lineText = self.confContents[lineNum]
        lineText = lineText.replace("\t", "   ")
        lineText = "".join([char for char in lineText if curses.ascii.isprint(char)])
        self.confContents[lineNum] = lineText
    
    self.redraw(True)
    self.valsLock.release()
    return True
  
  def handleKey(self, key):
    self.valsLock.acquire()
    if uiTools.isScrollKey(key) and self.confContents != None:
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
      self.scroll = 0
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
    
    # draws the top label
    locationLabel = " (%s)" % self.confLocation if self.confLocation else ""
    self.addstr(0, 0, "Tor Config%s:" % locationLabel, curses.A_STANDOUT)
    
    # restricts scroll location to valid bounds
    self.scroll = max(0, min(self.scroll, self._lastContentHeight - height + 1))
    
    renderedContents = self.confContents
    if self.confContents == None:
      renderedContents = ["### Unable to load torrc ###"]
    elif self.stripComments:
      renderedContents = torrc.stripComments(self.confContents)
    
    # offset to make room for the line numbers
    lineNumOffset = int(math.log10(len(renderedContents))) + 2 if self.showLineNum else 0
    
    # draws left-hand scroll bar if content's longer than the height
    scrollOffset = 0
    if self._config["features.config.showScrollbars"] and self._lastContentHeight > height - 1:
      scrollOffset = 3
      self.addScrollBar(self.scroll, self.scroll + height - 1, self._lastContentHeight, 1)
    
    displayLine = -self.scroll + 1 # line we're drawing on
    
    for lineNumber in range(0, len(renderedContents)):
      lineText = renderedContents[lineNumber]
      lineText = lineText.rstrip() # remove ending whitespace
      
      # blank lines are hidden when stripping comments
      hideLine = self.stripComments and not lineText
      
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
      if lineNumber in self.corrections:
        lineIssue, lineIssueMsg = self.corrections[lineNumber]
        
        if lineIssue == torrc.VAL_DUPLICATE:
          lineComp["option"][1] = curses.A_BOLD | uiTools.getColor("blue")
          lineComp["argument"][1] = curses.A_BOLD | uiTools.getColor("blue")
        elif lineIssue == torrc.VAL_MISMATCH:
          lineComp["argument"][1] = curses.A_BOLD | uiTools.getColor("red")
          lineComp["correction"][0] = " (%s)" % lineIssueMsg
      
      # draws the line number
      if self.showLineNum and not hideLine and displayLine < height and displayLine >= 1:
        lineNumStr = ("%%%ii" % (lineNumOffset - 1)) % (lineNumber + 1)
        self.addstr(displayLine, scrollOffset, lineNumStr, curses.A_BOLD | uiTools.getColor("yellow"))
      
      # draws the rest of the components with line wrap
      cursorLoc, lineOffset = lineNumOffset + scrollOffset, 0
      maxLinesPerEntry = self._config["features.config.maxLinesPerEntry"]
      displayQueue = [lineComp[entry] for entry in ("option", "argument", "correction", "comment")]
      
      while displayQueue:
        msg, format = displayQueue.pop(0)
        if hideLine: break
        
        maxMsgSize, includeBreak = width - cursorLoc, False
        if len(msg) >= maxMsgSize:
          # message is too long - break it up
          includeBreak = True
          if lineOffset == maxLinesPerEntry - 1:
            msg = uiTools.cropStr(msg, maxMsgSize)
          else:
            msg, remainder = uiTools.cropStr(msg, maxMsgSize, 4, 4, uiTools.END_WITH_HYPHEN, True)
            displayQueue.insert(0, (remainder.strip(), format))
        
        drawLine = displayLine + lineOffset
        if msg and drawLine < height and drawLine >= 1:
          self.addstr(drawLine, cursorLoc, msg, format)
        
        cursorLoc += len(msg)
        if includeBreak or not displayQueue:
          lineOffset += 1
          cursorLoc = lineNumOffset + scrollOffset
      
      displayLine += lineOffset
      
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

