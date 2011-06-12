#!/usr/bin/env python
# descriptorPopup.py -- popup panel used to show raw consensus data
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import math
import curses

import controller
import connections.connEntry
import popups
from util import panel, torTools, uiTools

# field keywords used to identify areas for coloring
LINE_NUM_COLOR = "yellow"
HEADER_COLOR = "cyan"
HEADER_PREFIX = ["ns/id/", "desc/id/"]

SIG_COLOR = "red"
SIG_START_KEYS = ["-----BEGIN RSA PUBLIC KEY-----", "-----BEGIN SIGNATURE-----"]
SIG_END_KEYS = ["-----END RSA PUBLIC KEY-----", "-----END SIGNATURE-----"]

UNRESOLVED_MSG = "No consensus data available"
ERROR_MSG = "Unable to retrieve data"

def addstr_wrap(panel, y, x, text, formatting, startX = 0, endX = -1, maxY = -1):
  """
  Writes text with word wrapping, returning the ending y/x coordinate.
  y: starting write line
  x: column offset from startX
  text / formatting: content to be written
  startX / endX: column bounds in which text may be written
  """
  
  # moved out of panel (trying not to polute new code!)
  # TODO: unpleaseantly complex usage - replace with something else when
  # rewriting confPanel and descriptorPopup (the only places this is used)
  if not text: return (y, x)          # nothing to write
  if endX == -1: endX = panel.maxX     # defaults to writing to end of panel
  if maxY == -1: maxY = panel.maxY + 1 # defaults to writing to bottom of panel
  lineWidth = endX - startX           # room for text
  while True:
    if len(text) > lineWidth - x - 1:
      chunkSize = text.rfind(" ", 0, lineWidth - x)
      writeText = text[:chunkSize]
      text = text[chunkSize:].strip()
      
      panel.addstr(y, x + startX, writeText, formatting)
      y, x = y + 1, 0
      if y >= maxY: return (y, x)
    else:
      panel.addstr(y, x + startX, text, formatting)
      return (y, x + len(text))

class PopupProperties:
  """
  State attributes of popup window for consensus descriptions.
  """
  
  def __init__(self):
    self.fingerprint = ""
    self.entryColor = "white"
    self.text = []
    self.scroll = 0
    self.showLineNum = True
  
  def reset(self, fingerprint, entryColor):
    self.fingerprint = fingerprint
    self.entryColor = entryColor
    self.text = []
    self.scroll = 0
    
    if fingerprint == "UNKNOWN":
      self.fingerprint = None
      self.showLineNum = False
      self.text.append(UNRESOLVED_MSG)
    else:
      conn = torTools.getConn()
      self.showLineNum = True
      
      self.text.append("ns/id/%s" % fingerprint)
      consensusEntry = conn.getConsensusEntry(fingerprint)
      
      if consensusEntry: self.text += consensusEntry.split("\n")
      else: self.text = self.text + [ERROR_MSG, ""]
      
      self.text.append("desc/id/%s" % fingerprint)
      descriptorEntry = conn.getDescriptorEntry(fingerprint)
      
      if descriptorEntry: self.text += descriptorEntry.split("\n")
      else: self.text = self.text + [ERROR_MSG]
  
  def handleKey(self, key, height):
    if key == curses.KEY_UP: self.scroll = max(self.scroll - 1, 0)
    elif key == curses.KEY_DOWN: self.scroll = max(0, min(self.scroll + 1, len(self.text) - height))
    elif key == curses.KEY_PPAGE: self.scroll = max(self.scroll - height, 0)
    elif key == curses.KEY_NPAGE: self.scroll = max(0, min(self.scroll + height, len(self.text) - height))

def showDescriptorPopup(connectionPanel):
  """
  Presents consensus descriptor in popup window with the following controls:
  Up, Down, Page Up, Page Down - scroll descriptor
  Right, Left - next / previous connection
  Enter, Space, d, D - close popup
  """
  
  # hides the title of the first panel on the page
  control = controller.getController()
  topPanel = control.getDisplayPanels(includeSticky = False)[0]
  topPanel.setTitleVisible(False)
  topPanel.redraw(True)
  
  properties = PopupProperties()
  isVisible = True
  
  panel.CURSES_LOCK.acquire()
  
  try:
    while isVisible:
      selection = connectionPanel._scroller.getCursorSelection(connectionPanel._entryLines)
      if not selection: break
      fingerprint = selection.foreign.getFingerprint()
      entryColor = connections.connEntry.CATEGORY_COLOR[selection.getType()]
      properties.reset(fingerprint, entryColor)
      
      # constrains popup size to match text
      width, height = 0, 0
      for line in properties.text:
        # width includes content, line number field, and border
        lineWidth = len(line) + 5
        if properties.showLineNum: lineWidth += int(math.log10(len(properties.text))) + 1
        width = max(width, lineWidth)
        
        # tracks number of extra lines that will be taken due to text wrap
        height += (lineWidth - 2) / connectionPanel.maxX
      
      while isVisible:
        popupHeight = min(len(properties.text) + height + 2, connectionPanel.maxY)
        popup, _, _ = popups.init(popupHeight, width)
        if not popup: break
        
        try:
          draw(popup, properties)
          key = control.getScreen().getch()
          
          if uiTools.isSelectionKey(key) or key in (ord('d'), ord('D')):
            # closes popup
            isVisible = False
          elif key in (curses.KEY_LEFT, curses.KEY_RIGHT):
            # navigation - pass on to connPanel and recreate popup
            connectionPanel.handleKey(curses.KEY_UP if key == curses.KEY_LEFT else curses.KEY_DOWN)
            break
          else: properties.handleKey(key, popup.height - 2)
        finally: popups.finalize()
  finally: panel.CURSES_LOCK.release()
  
  topPanel.setTitleVisible(True)

def draw(popup, properties):
  popup.win.erase()
  popup.win.box()
  xOffset = 2
  
  if properties.text:
    if properties.fingerprint: popup.addstr(0, 0, "Consensus Descriptor (%s):" % properties.fingerprint, curses.A_STANDOUT)
    else: popup.addstr(0, 0, "Consensus Descriptor:", curses.A_STANDOUT)
    
    isEncryption = False          # true if line is part of an encryption block
    
    # checks if first line is in an encryption block
    for i in range(0, properties.scroll):
      lineText = properties.text[i].strip()
      if lineText in SIG_START_KEYS: isEncryption = True
      elif lineText in SIG_END_KEYS: isEncryption = False
    
    pageHeight = popup.maxY - 2
    numFieldWidth = int(math.log10(len(properties.text))) + 1
    lineNum = 1
    for i in range(properties.scroll, min(len(properties.text), properties.scroll + pageHeight)):
      lineText = properties.text[i].strip()
      
      numOffset = 0     # offset for line numbering
      if properties.showLineNum:
        popup.addstr(lineNum, xOffset, ("%%%ii" % numFieldWidth) % (i + 1), curses.A_BOLD | uiTools.getColor(LINE_NUM_COLOR))
        numOffset = numFieldWidth + 1
      
      if lineText:
        keyword = lineText.split()[0]   # first word of line
        remainder = lineText[len(keyword):]
        keywordFormat = curses.A_BOLD | uiTools.getColor(properties.entryColor)
        remainderFormat = uiTools.getColor(properties.entryColor)
        
        if lineText.startswith(HEADER_PREFIX[0]) or lineText.startswith(HEADER_PREFIX[1]):
          keyword, remainder = lineText, ""
          keywordFormat = curses.A_BOLD | uiTools.getColor(HEADER_COLOR)
        if lineText == UNRESOLVED_MSG or lineText == ERROR_MSG:
          keyword, remainder = lineText, ""
        if lineText in SIG_START_KEYS:
          keyword, remainder = lineText, ""
          isEncryption = True
          keywordFormat = curses.A_BOLD | uiTools.getColor(SIG_COLOR)
        elif lineText in SIG_END_KEYS:
          keyword, remainder = lineText, ""
          isEncryption = False
          keywordFormat = curses.A_BOLD | uiTools.getColor(SIG_COLOR)
        elif isEncryption:
          keyword, remainder = lineText, ""
          keywordFormat = uiTools.getColor(SIG_COLOR)
        
        lineNum, xLoc = addstr_wrap(popup, lineNum, 0, keyword, keywordFormat, xOffset + numOffset, popup.maxX - 1, popup.maxY - 1)
        lineNum, xLoc = addstr_wrap(popup, lineNum, xLoc, remainder, remainderFormat, xOffset + numOffset, popup.maxX - 1, popup.maxY - 1)
      
      lineNum += 1
      if lineNum > pageHeight: break
      
  popup.win.refresh()

