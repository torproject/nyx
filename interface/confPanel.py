#!/usr/bin/env python
# confPanel.py -- Presents torrc with syntax highlighting.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import math
import curses
from TorCtl import TorCtl

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
    elif key == ord('r') or key == ord('R'): self.reset()
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
        for i in range(self.scroll, min(len(displayText), self.scroll + pageHeight)):
          lineText = displayText[i].strip()
          endBreak = 0
          
          if self.showLineNum:
            self.addstr(i - self.scroll + 1, 0, ("%%%ii" % numFieldWidth) % (i + 1), curses.A_BOLD | util.getColor("yellow"))
            numOffset = numFieldWidth + 1
          else: numOffset = 0
          
          if not lineText: continue
          elif not lineText[0] == "#":
            ctlBreak = lineText.find(" ")
            endBreak = lineText.find("#")
            if endBreak == -1: endBreak = len(lineText)
            
            self.addstr(i - self.scroll + 1, numOffset, lineText[:ctlBreak], curses.A_BOLD | util.getColor("green"))
            self.addstr(i - self.scroll + 1, numOffset + ctlBreak, lineText[ctlBreak:endBreak], curses.A_BOLD | util.getColor("cyan"))
          self.addstr(i - self.scroll + 1, numOffset + endBreak, lineText[endBreak:], util.getColor("white"))
        
        self.refresh()
      finally:
        self.lock.release()

