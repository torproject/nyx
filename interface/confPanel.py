#!/usr/bin/env python
# confPanel.py -- Presents torrc with syntax highlighting.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import math
import curses

import util

class ConfPanel(util.Panel):
  """
  Presents torrc with syntax highlighting in a scroll-able area.
  """
  
  def __init__(self, lock, confLocation):
    util.Panel.__init__(self, lock, -1)
    self.confLocation = confLocation
    self.showLineNum = True
    self.stripComments = False
    self.confContents = []
    self.scroll = 0
    self.reset()
  
  def reset(self):
    """
    Reloads torrc contents and resets scroll height.
    """
    try:
      confFile = open(self.confLocation, "r")
      self.confContents = confFile.readlines()
      confFile.close()
    except IOError:
      self.confContents = ["### Unable to load torrc ###"]
    self.scroll = 0
  
  def handleKey(self, key):
    self._resetBounds()
    pageHeight = self.maxY - 1
    if key == curses.KEY_UP: self.scroll = max(self.scroll - 1, 0)
    elif key == curses.KEY_DOWN: self.scroll = max(0, min(self.scroll + 1, len(self.confContents) - pageHeight))
    elif key == curses.KEY_PPAGE: self.scroll = max(self.scroll - pageHeight, 0)
    elif key == curses.KEY_NPAGE: self.scroll = max(0, min(self.scroll + pageHeight, len(self.confContents) - pageHeight))
    elif key == ord('n') or key == ord('N'): self.showLineNum = not self.showLineNum
    elif key == ord('s') or key == ord('S'):
      self.stripComments = not self.stripComments
      self.scroll = 0
    self.redraw()
  
  def redraw(self):
    if self.win:
      if not self.lock.acquire(False): return
      try:
        self.clear()
        self.addstr(0, 0, "Tor Config (%s):" % self.confLocation, util.LABEL_ATTR)
        
        if self.stripComments:
          displayText = []
          
          for line in self.confContents:
            commentStart = line.find("#")
            if commentStart != -1: line = line[:commentStart]
            
            line = line.strip()
            if line: displayText.append(line)
        else: displayText = self.confContents
        
        pageHeight = self.maxY - 1
        numFieldWidth = int(math.log10(len(displayText))) + 1
        lineNum = 1
        for i in range(self.scroll, min(len(displayText), self.scroll + pageHeight)):
          lineText = displayText[i].strip()
          
          numOffset = 0     # offset for line numbering
          if self.showLineNum:
            self.addstr(lineNum, 0, ("%%%ii" % numFieldWidth) % (i + 1), curses.A_BOLD | util.getColor("yellow"))
            numOffset = numFieldWidth + 1
          
          command, argument, comment = "", "", ""
          if not lineText: continue # no text
          elif lineText[0] == "#":
            # whole line is commented out
            comment = lineText
          else:
            # parse out command, argument, and possible comment
            ctlEnd = lineText.find(" ")   # end of command
            argEnd = lineText.find("#")   # end of argument (start of comment or end of line)
            if argEnd == -1: argEnd = len(lineText)
            
            command, argument, comment = lineText[:ctlEnd], lineText[ctlEnd:argEnd], lineText[argEnd:]
          
          xLoc = 0
          lineNum, xLoc = self.addstr_wrap(lineNum, xLoc, numOffset, command, curses.A_BOLD | util.getColor("green"))
          lineNum, xLoc = self.addstr_wrap(lineNum, xLoc, numOffset, argument, curses.A_BOLD | util.getColor("cyan"))
          lineNum, xLoc = self.addstr_wrap(lineNum, xLoc, numOffset, comment, util.getColor("white"))
          lineNum += 1
          
        self.refresh()
      finally:
        self.lock.release()
  
  def addstr_wrap(self, y, x, indent, text, formatting):
    """
    Writes text with word wrapping, returning the ending y/x coordinate.
    """
    
    if not text: return (y, x)        # nothing to write
    lineWidth = self.maxX - indent    # room for text
    while True:
      if len(text) > lineWidth - x - 1:
        chunkSize = text.rfind(" ", 0, lineWidth - x)
        writeText = text[:chunkSize]
        text = text[chunkSize:].strip()
        
        self.addstr(y, x + indent, writeText, formatting)
        y, x = y + 1, 0
      else:
        self.addstr(y, x + indent, text, formatting)
        return (y, x + len(text))

