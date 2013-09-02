"""
Popup providing the raw descriptor and consensus information for a relay.
"""

import math
import curses

import cli.popups
import cli.connections.connEntry

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

def showDescriptorPopup(connPanel):
  """
  Presents consensus descriptor in popup window with the following controls:
  Up, Down, Page Up, Page Down - scroll descriptor
  Right, Left - next / previous connection
  Enter, Space, d, D - close popup
  
  Arguments:
    connPanel - connection panel providing the dialog
  """
  
  # hides the title of the connection panel
  connPanel.setTitleVisible(False)
  connPanel.redraw(True)
  
  control = cli.controller.getController()
  panel.CURSES_LOCK.acquire()
  isDone = False
  
  try:
    while not isDone:
      selection = connPanel.getSelection()
      if not selection: break
      
      fingerprint = selection.foreign.getFingerprint()
      if fingerprint == "UNKNOWN": fingerprint = None
      
      displayText = getDisplayText(fingerprint)
      displayColor = cli.connections.connEntry.CATEGORY_COLOR[selection.getType()]
      showLineNumber = fingerprint != None
      
      # determines the maximum popup size the displayText can fill
      pHeight, pWidth = getPreferredSize(displayText, connPanel.maxX, showLineNumber)
      
      popup, _, height = cli.popups.init(pHeight, pWidth)
      if not popup: break
      scroll, isChanged = 0, True
      
      try:
        while not isDone:
          if isChanged:
            draw(popup, fingerprint, displayText, displayColor, scroll, showLineNumber)
            isChanged = False
          
          key = control.getScreen().getch()
          
          if uiTools.isScrollKey(key):
            # TODO: This is a bit buggy in that scrolling is by displayText
            # lines rather than the displayed lines, causing issues when
            # content wraps. The result is that we can't have a scrollbar and
            # can't scroll to the bottom if there's a multi-line being
            # displayed. However, trying to correct this introduces a big can
            # of worms and after hours decided that this isn't worth the
            # effort...
            
            newScroll = uiTools.getScrollPosition(key, scroll, height - 2, len(displayText))
            
            if scroll != newScroll:
              scroll, isChanged = newScroll, True
          elif uiTools.isSelectionKey(key) or key in (ord('d'), ord('D')):
            isDone = True # closes popup
          elif key in (curses.KEY_LEFT, curses.KEY_RIGHT):
            # navigation - pass on to connPanel and recreate popup
            connPanel.handleKey(curses.KEY_UP if key == curses.KEY_LEFT else curses.KEY_DOWN)
            break
      finally: cli.popups.finalize()
  finally:
    connPanel.setTitleVisible(True)
    connPanel.redraw(True)
    panel.CURSES_LOCK.release()

def getDisplayText(fingerprint):
  """
  Provides the descriptor and consensus entry for a relay. This is a list of
  lines to be displayed by the dialog.
  """
  
  if not fingerprint: return [UNRESOLVED_MSG]
  conn, description = torTools.getConn(), []
  
  description.append("ns/id/%s" % fingerprint)
  consensusEntry = conn.getConsensusEntry(fingerprint)
  
  if consensusEntry: description += consensusEntry.split("\n")
  else: description += [ERROR_MSG, ""]
  
  description.append("desc/id/%s" % fingerprint)
  descriptorEntry = conn.getDescriptorEntry(fingerprint)
  
  if descriptorEntry: description += descriptorEntry.split("\n")
  else: description += [ERROR_MSG]
  
  return description

def getPreferredSize(text, maxWidth, showLineNumber):
  """
  Provides the (height, width) tuple for the preferred size of the given text.
  """
  
  width, height = 0, len(text) + 2
  lineNumWidth = int(math.log10(len(text))) + 1
  for line in text:
    # width includes content, line number field, and border
    lineWidth = len(line) + 5
    if showLineNumber: lineWidth += lineNumWidth
    width = max(width, lineWidth)
    
    # tracks number of extra lines that will be taken due to text wrap
    height += (lineWidth - 2) / maxWidth
  
  return (height, width)

def draw(popup, fingerprint, displayText, displayColor, scroll, showLineNumber):
  popup.win.erase()
  popup.win.box()
  xOffset = 2
  
  if fingerprint: title = "Consensus Descriptor (%s):" % fingerprint
  else: title = "Consensus Descriptor:"
  popup.addstr(0, 0, title, curses.A_STANDOUT)
  
  lineNumWidth = int(math.log10(len(displayText))) + 1
  isEncryptionBlock = False   # flag indicating if we're currently displaying a key
  
  # checks if first line is in an encryption block
  for i in range(0, scroll):
    lineText = displayText[i].strip()
    if lineText in SIG_START_KEYS: isEncryptionBlock = True
    elif lineText in SIG_END_KEYS: isEncryptionBlock = False
  
  drawLine, pageHeight = 1, popup.maxY - 2
  for i in range(scroll, scroll + pageHeight):
    lineText = displayText[i].strip()
    xOffset = 2
    
    if showLineNumber:
      lineNumLabel = ("%%%ii" % lineNumWidth) % (i + 1)
      lineNumFormat = curses.A_BOLD | uiTools.getColor(LINE_NUM_COLOR)
      
      popup.addstr(drawLine, xOffset, lineNumLabel, lineNumFormat)
      xOffset += lineNumWidth + 1
    
    # Most consensus and descriptor lines are keyword/value pairs. Both are
    # shown with the same color, but the keyword is bolded.
    
    keyword, value = lineText, ""
    drawFormat = uiTools.getColor(displayColor)
    
    if lineText.startswith(HEADER_PREFIX[0]) or lineText.startswith(HEADER_PREFIX[1]):
      keyword, value = lineText, ""
      drawFormat = uiTools.getColor(HEADER_COLOR)
    elif lineText == UNRESOLVED_MSG or lineText == ERROR_MSG:
      keyword, value = lineText, ""
    elif lineText in SIG_START_KEYS:
      keyword, value = lineText, ""
      isEncryptionBlock = True
      drawFormat = uiTools.getColor(SIG_COLOR)
    elif lineText in SIG_END_KEYS:
      keyword, value = lineText, ""
      isEncryptionBlock = False
      drawFormat = uiTools.getColor(SIG_COLOR)
    elif isEncryptionBlock:
      keyword, value = "", lineText
      drawFormat = uiTools.getColor(SIG_COLOR)
    elif " " in lineText:
      divIndex = lineText.find(" ")
      keyword, value = lineText[:divIndex], lineText[divIndex:]
    
    displayQueue = [(keyword, drawFormat | curses.A_BOLD), (value, drawFormat)]
    cursorLoc = xOffset
    
    while displayQueue:
      msg, format = displayQueue.pop(0)
      if not msg: continue
      
      maxMsgSize = popup.maxX - 1 - cursorLoc
      if len(msg) >= maxMsgSize:
        # needs to split up the line
        msg, remainder = uiTools.cropStr(msg, maxMsgSize, None, endType = None, getRemainder = True)
        
        if xOffset == cursorLoc and msg == "":
          # first word is longer than the line
          msg = uiTools.cropStr(remainder, maxMsgSize)
          
          if " " in remainder:
            remainder = remainder.split(" ", 1)[1]
          else: remainder = ""
        
        popup.addstr(drawLine, cursorLoc, msg, format)
        cursorLoc = xOffset
        
        if remainder:
          displayQueue.insert(0, (remainder.strip(), format))
          drawLine += 1
      else:
        popup.addstr(drawLine, cursorLoc, msg, format)
        cursorLoc += len(msg)
      
      if drawLine > pageHeight: break
    
    drawLine += 1
    if drawLine > pageHeight: break
  
  popup.win.refresh()

