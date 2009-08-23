#!/usr/bin/env python
# descriptorPopup.py -- popup panel used to show raw consensus data
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import math
import curses
from TorCtl import TorCtl

import connPanel
import util

# field keywords used to identify areas for coloring
LINE_NUM_COLOR = "yellow"
HEADER_COLOR = "cyan"
HEADER_PREFIX = ["ns/id/", "desc/id/"]

SIG_COLOR = "red"
SIG_START_KEYS = ["-----BEGIN RSA PUBLIC KEY-----", "-----BEGIN SIGNATURE-----"]
SIG_END_KEYS = ["-----END RSA PUBLIC KEY-----", "-----END SIGNATURE-----"]

UNRESOLVED_MSG = "No consensus data available"
ERROR_MSG = "Unable to retrieve data"

class PopupProperties:
  """
  State attributes of popup window for consensus descriptions.
  """
  
  def __init__(self, conn):
    self.conn = conn
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
      try:
        self.showLineNum = True
        nsCommand = "ns/id/%s" % fingerprint
        self.text.append(nsCommand)
        self.text = self.text + self.conn.get_info(nsCommand)[nsCommand].split("\n")
      except TorCtl.ErrorReply:
        self.text = self.text + [ERROR_MSG, ""]
      except TorCtl.TorCtlClosed:
        self.text = self.text + [ERROR_MSG, ""]
      
      try:
        descCommand = "desc/id/%s" % fingerprint
        self.text.append(descCommand)
        self.text = self.text + self.conn.get_info(descCommand)[descCommand].split("\n")
      except TorCtl.ErrorReply:
        self.text = self.text + [ERROR_MSG]
      except TorCtl.TorCtlClosed:
        self.text = self.text + [ERROR_MSG]
  
  def handleKey(self, key, height):
    if key == curses.KEY_UP: self.scroll = max(self.scroll - 1, 0)
    elif key == curses.KEY_DOWN: self.scroll = max(0, min(self.scroll + 1, len(self.text) - height))
    elif key == curses.KEY_PPAGE: self.scroll = max(self.scroll - height, 0)
    elif key == curses.KEY_NPAGE: self.scroll = max(0, min(self.scroll + height, len(self.text) - height))

def showDescriptorPopup(popup, stdscr, conn, connectionPanel):
  """
  Presents consensus descriptor in popup window with the following controls:
  Up, Down, Page Up, Page Down - scroll descriptor
  Right, Left - next / previous connection
  Enter, Space, d, D - close popup
  """
  
  properties = PopupProperties(conn)
  isVisible = True
  
  if not popup.lock.acquire(False): return
  try:
    while isVisible:
      selection = connectionPanel.cursorSelection
      if not selection or not connectionPanel.connections: break
      fingerprint = connectionPanel.getFingerprint(selection[connPanel.CONN_F_IP], selection[connPanel.CONN_F_PORT])
      entryColor = connPanel.TYPE_COLORS[selection[connPanel.CONN_TYPE]]
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
      
      popup._resetBounds()
      popup.height = min(len(properties.text) + height + 2, connectionPanel.maxY)
      popup.recreate(stdscr, popup.startY, width)
      
      while isVisible:
        draw(popup, properties)
        key = stdscr.getch()
        
        if key in (curses.KEY_ENTER, 10, ord(' '), ord('d'), ord('D')):
          # closes popup
          isVisible = False
        elif key in (curses.KEY_LEFT, curses.KEY_RIGHT):
          # navigation - pass on to connPanel and recreate popup
          connectionPanel.handleKey(curses.KEY_UP if key == curses.KEY_LEFT else curses.KEY_DOWN)
          break
        else: properties.handleKey(key, popup.height - 2)
    
    popup.height = 9
    popup.recreate(stdscr, popup.startY, 80)
  finally:
    popup.lock.release()

def draw(popup, properties):
  popup.clear()
  popup.win.box()
  xOffset = 2
  
  if properties.text:
    if properties.fingerprint: popup.addstr(0, 0, "Consensus Descriptor (%s):" % properties.fingerprint, util.LABEL_ATTR)
    else: popup.addstr(0, 0, "Consensus Descriptor:", util.LABEL_ATTR)
    
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
        popup.addstr(lineNum, xOffset, ("%%%ii" % numFieldWidth) % (i + 1), curses.A_BOLD | util.getColor(LINE_NUM_COLOR))
        numOffset = numFieldWidth + 1
      
      if lineText:
        keyword = lineText.split()[0]   # first word of line
        remainder = lineText[len(keyword):]
        keywordFormat = curses.A_BOLD | util.getColor(properties.entryColor)
        remainderFormat = util.getColor(properties.entryColor)
        
        if lineText.startswith(HEADER_PREFIX[0]) or lineText.startswith(HEADER_PREFIX[1]):
          keyword, remainder = lineText, ""
          keywordFormat = curses.A_BOLD | util.getColor(HEADER_COLOR)
        if lineText == UNRESOLVED_MSG or lineText == ERROR_MSG:
          keyword, remainder = lineText, ""
        if lineText in SIG_START_KEYS:
          keyword, remainder = lineText, ""
          isEncryption = True
          keywordFormat = curses.A_BOLD | util.getColor(SIG_COLOR)
        elif lineText in SIG_END_KEYS:
          keyword, remainder = lineText, ""
          isEncryption = False
          keywordFormat = curses.A_BOLD | util.getColor(SIG_COLOR)
        elif isEncryption:
          keyword, remainder = lineText, ""
          keywordFormat = util.getColor(SIG_COLOR)
        
        lineNum, xLoc = popup.addstr_wrap(lineNum, 0, keyword, keywordFormat, xOffset + numOffset, popup.maxX - 1, popup.maxY - 1)
        lineNum, xLoc = popup.addstr_wrap(lineNum, xLoc, remainder, remainderFormat, xOffset + numOffset, popup.maxX - 1, popup.maxY - 1)
      
      lineNum += 1
      if lineNum > pageHeight: break
      
  popup.refresh()

