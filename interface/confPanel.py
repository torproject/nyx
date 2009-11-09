#!/usr/bin/env python
# confPanel.py -- Presents torrc with syntax highlighting.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import math
import curses

import util

# torrc parameters that can be defined multiple times without overwriting
# from src/or/config.c (entries with LINELIST or LINELIST_S)
# last updated for tor version 0.2.1.19
MULTI_LINE_PARAM = ["AlternateBridgeAuthority", "AlternateDirAuthority", "AlternateHSAuthority", "AuthDirBadDir", "AuthDirBadExit", "AuthDirInvalid", "AuthDirReject", "Bridge", "ControlListenAddress", "ControlSocket", "DirListenAddress", "DirPolicy", "DirServer", "DNSListenAddress", "ExitPolicy", "HashedControlPassword", "HiddenServiceDir", "HiddenServiceOptions", "HiddenServicePort", "HiddenServiceVersion", "HiddenServiceAuthorizeClient", "HidServAuth", "Log", "MapAddress", "NatdListenAddress", "NodeFamily", "ORListenAddress", "ReachableAddresses", "ReachableDirAddresses", "ReachableORAddresses", "RecommendedVersions", "RecommendedClientVersions", "RecommendedServerVersions", "SocksListenAddress", "SocksPolicy", "TransListenAddress", "__HashedControlSessionPassword"]

# size modifiers allowed by config.c
LABEL_KB = ["kb", "kbyte", "kbytes", "kilobyte", "kilobytes"]
LABEL_MB = ["m", "mb", "mbyte", "mbytes", "megabyte", "megabytes"]
LABEL_GB = ["gb", "gbyte", "gbytes", "gigabyte", "gigabytes"]
LABEL_TB = ["tb", "terabyte", "terabytes"]

# time modifiers allowed by config.c
LABEL_MIN = ["minute", "minutes"]
LABEL_HOUR = ["hour", "hours"]
LABEL_DAY = ["day", "days"]
LABEL_WEEK = ["week", "weeks"]

class ConfPanel(util.Panel):
  """
  Presents torrc with syntax highlighting in a scroll-able area.
  """
  
  def __init__(self, lock, confLocation, conn, logPanel):
    util.Panel.__init__(self, lock, -1)
    self.confLocation = confLocation
    self.showLineNum = True
    self.stripComments = False
    self.confContents = []
    self.scroll = 0
    
    # lines that don't matter due to duplicates
    self.irrelevantLines = []
    
    # used to check consistency with tor's actual values - corrections mapping
    # is of line numbers (one-indexed) to tor's actual values
    self.corrections = {}
    self.conn = conn
    self.logger = logPanel
    
    self.reset()
  
  def reset(self):
    """
    Reloads torrc contents and resets scroll height.
    """
    try:
      confFile = open(self.confLocation, "r")
      self.confContents = confFile.readlines()
      confFile.close()
      
      # checks if torrc differs from get_option data
      self.irrelevantLines = []
      self.corrections = {}
      parsedCommands = {}       # mapping of parsed commands to line numbers
      
      for lineNumber in range(len(self.confContents)):
        lineText = self.confContents[lineNumber].strip()
        
        if lineText and lineText[0] != "#":
          # relevant to tor (not blank nor comment)
          ctlEnd = lineText.find(" ")   # end of command
          argEnd = lineText.find("#")   # end of argument (start of comment or end of line)
          if argEnd == -1: argEnd = len(lineText)
          command, argument = lineText[:ctlEnd], lineText[ctlEnd:argEnd].strip()
          
          # expands value if it's a size or time
          comp = argument.strip().lower().split(" ")
          if len(comp) > 1:
            size = 0
            if comp[1] in LABEL_KB: size = int(comp[0]) * 1024
            elif comp[1] in LABEL_MB: size = int(comp[0]) * 1048576
            elif comp[1] in LABEL_GB: size = int(comp[0]) * 1073741824
            elif comp[1] in LABEL_TB: size = int(comp[0]) * 1099511627776
            elif comp[1] in LABEL_MIN: size = int(comp[0]) * 60
            elif comp[1] in LABEL_HOUR: size = int(comp[0]) * 3600
            elif comp[1] in LABEL_DAY: size = int(comp[0]) * 86400
            elif comp[1] in LABEL_WEEK: size = int(comp[0]) * 604800
            if size != 0: argument = str(size)
              
          # most parameters are overwritten if defined multiple times, if so
          # it's erased from corrections and noted as duplicate instead
          if not command in MULTI_LINE_PARAM and command in parsedCommands.keys():
            previousLineNum = parsedCommands[command]
            self.irrelevantLines.append(previousLineNum)
            if previousLineNum in self.corrections.keys(): del self.corrections[previousLineNum]
          
          parsedCommands[command] = lineNumber + 1
          
          # check validity against tor's actual state
          try:
            actualValues = []
            for key, val in self.conn.get_option(command):
              actualValues.append(val)
            
            if not argument in actualValues:
              self.corrections[lineNumber + 1] = argument + " - " + ", ".join(actualValues)
          except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed):
            pass # unable to load tor parameter to validate... weird
      
      # logs issues that arose
      if self.irrelevantLines:
        if len(self.irrelevantLines) > 1: first, second, third = "Entries", "are", ", including lines"
        else: first, second, third = "Entry", "is", " on line"
        baseMsg = "%s in your torrc %s ignored due to duplication%s" % (first, second, third)
        
        self.logger.monitor_event("NOTICE", "%s: %s (highlighted in blue)" % (baseMsg, ", ".join([str(val) for val in self.irrelevantLines])))
      if self.corrections:
        self.logger.monitor_event("WARN", "Tor's state differs from loaded torrc")
    except IOError, exc:
      self.confContents = ["### Unable to load torrc ###"]
      self.logger.monitor_event("WARN", "Unable to load torrc (%s)" % str(exc))
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
        
        pageHeight = self.maxY - 1
        numFieldWidth = int(math.log10(len(self.confContents))) + 1
        lineNum, displayLineNum = self.scroll + 1, 1 # lineNum corresponds to torrc, displayLineNum concerns what's presented
        
        for i in range(self.scroll, min(len(self.confContents), self.scroll + pageHeight)):
          lineText = self.confContents[i].strip()
          skipLine = False # true if we're not presenting line due to stripping
          
          command, argument, correction, comment = "", "", "", ""
          commandColor, argumentColor, correctionColor, commentColor = "green", "cyan", "cyan", "white"
          
          if not lineText:
            # no text
            if self.stripComments: skipLine = True
          elif lineText[0] == "#":
            # whole line is commented out
            comment = lineText
            if self.stripComments: skipLine = True
          else:
            # parse out command, argument, and possible comment
            ctlEnd = lineText.find(" ")   # end of command
            argEnd = lineText.find("#")   # end of argument (start of comment or end of line)
            if argEnd == -1: argEnd = len(lineText)
            
            command, argument, comment = lineText[:ctlEnd], lineText[ctlEnd:argEnd], lineText[argEnd:]
            
            # changes presentation if value's incorrect or irrelevant
            if lineNum in self.corrections.keys():
              argumentColor = "red"
              correction = " (%s)" % self.corrections[lineNum]
            elif lineNum in self.irrelevantLines:
              commandColor = "blue"
              argumentColor = "blue"
          
          if not skipLine:
            numOffset = 0     # offset for line numbering
            if self.showLineNum:
              self.addstr(displayLineNum, 0, ("%%%ii" % numFieldWidth) % lineNum, curses.A_BOLD | util.getColor("yellow"))
              numOffset = numFieldWidth + 1
            
            xLoc = 0
            displayLineNum, xLoc = self.addstr_wrap(displayLineNum, xLoc, command, curses.A_BOLD | util.getColor(commandColor), numOffset)
            displayLineNum, xLoc = self.addstr_wrap(displayLineNum, xLoc, argument, curses.A_BOLD | util.getColor(argumentColor), numOffset)
            displayLineNum, xLoc = self.addstr_wrap(displayLineNum, xLoc, correction, curses.A_BOLD | util.getColor(correctionColor), numOffset)
            displayLineNum, xLoc = self.addstr_wrap(displayLineNum, xLoc, comment, util.getColor(commentColor), numOffset)
            
            displayLineNum += 1
          
          lineNum += 1
          
        self.refresh()
      finally:
        self.lock.release()

