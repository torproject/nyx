#!/usr/bin/env python
# fileDescriptorPopup.py -- provides open file descriptor stats and listing
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import os
import curses

from util import panel, uiTools

class PopupProperties:
  """
  State attributes of popup window for file descriptors. Any problem in system
  calls will cause 'errorMsg' to be set (providing the notice rather than
  displaying data). Under systems other than Solaris there's no way for a
  process (other than tor itself) to know its file descriptor limit, so this
  estimates.
  """
  
  def __init__(self, torPid):
    self.fdFile, self.fdConn, self.fdMisc = [], [], []
    self.fdLimit = 0
    self.errorMsg = ""
    self.scroll = 0
    
    try:
      ulimitCall = None
      
      # retrieves list of open files, options are:
      # n = no dns lookups, p = by pid, -F = show fields (L = login name, n = opened files)
      lsofCall = os.popen3("lsof -np %s -F Ln 2> /dev/null" % torPid)
      results = lsofCall[1].readlines()
      errResults = lsofCall[2].readlines()
      
      # checks if lsof was unavailable
      if "not found" in "".join(errResults):
        raise Exception("error: lsof is unavailable")
      
      # if we didn't get any results then tor's probably closed (keep defaults)
      if len(results) == 0: return
      
      torUser = results[1][1:]
      results = results[2:] # skip first couple lines (pid listing and user)
      
      # splits descriptors into buckets according to their type
      descriptors = [entry[1:].strip() for entry in results] # strips off first character (always an 'n')
      
      # checks if read failed due to permission issues
      isPermissionDenied = True
      for desc in descriptors:
        if "Permission denied" not in desc:
          isPermissionDenied = False
          break
      
      if isPermissionDenied:
        raise Exception("lsof error: Permission denied")
      
      for desc in descriptors:
        if os.path.exists(desc): self.fdFile.append(desc)
        elif desc[0] != "/" and ":" in desc: self.fdConn.append(desc)
        else: self.fdMisc.append(desc)
      
      self.fdFile.sort()
      self.fdConn.sort()
      self.fdMisc.sort()
      
      # This is guessing the open file limit. Unfortunately there's no way
      # (other than "/usr/proc/bin/pfiles pid | grep rlimit" under Solaris) to
      # get the file descriptor limit for an arbitrary process. What we need is
      # for the tor process to provide the return value of the "getrlimit"
      # function via a GET_INFO call.
      if torUser == "debian-tor":
        # probably loaded via /etc/init.d/tor which changes descriptor limit
        self.fdLimit = 8192
      else:
        # uses ulimit to estimate (-H is for hard limit, which is what tor uses)
        ulimitCall = os.popen("ulimit -Hn 2> /dev/null")
        results = ulimitCall.readlines()
        if len(results) == 0: raise Exception("error: ulimit is unavailable")
        self.fdLimit = int(results[0])
    except Exception, exc:
      # problem arose in calling or parsing lsof or ulimit calls
      self.errorMsg = str(exc)
    finally:
      lsofCall[0].close()
      lsofCall[1].close()
      lsofCall[2].close()
      if ulimitCall: ulimitCall.close()
  
  def handleKey(self, key, height):
    totalEntries = len(self.fdFile) + len(self.fdConn) + len(self.fdMisc)
    
    if key == curses.KEY_UP: self.scroll = max(self.scroll - 1, 0)
    elif key == curses.KEY_DOWN: self.scroll = max(0, min(self.scroll + 1, totalEntries - height))
    elif key == curses.KEY_PPAGE: self.scroll = max(self.scroll - height, 0)
    elif key == curses.KEY_NPAGE: self.scroll = max(0, min(self.scroll + height, totalEntries - height))

def showFileDescriptorPopup(popup, stdscr, torPid):
  """
  Presents open file descriptors in popup window with the following controls:
  Up, Down, Page Up, Page Down - scroll descriptors
  Any other key - close popup
  """
  
  properties = PopupProperties(torPid)
  
  if not panel.CURSES_LOCK.acquire(False): return
  try:
    if properties.errorMsg:
      popupWidth = len(properties.errorMsg) + 4
      popupHeight = 3
    else:
      # uses longest entry to determine popup width
      popupWidth = 40 # minimum width
      for entry in properties.fdFile + properties.fdConn + properties.fdMisc:
        popupWidth = max(popupWidth, len(entry) + 4)
      
      popupHeight = len(properties.fdFile) + len(properties.fdConn) + len(properties.fdMisc) + 4
    
    popup._resetBounds()
    popup.height = popupHeight
    popup.recreate(stdscr, popupWidth)
    
    while True:
      draw(popup, properties)
      key = stdscr.getch()
      
      if key in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE, curses.KEY_NPAGE):
        # navigation - tweak properties and recreate popup
        properties.handleKey(key, popup.maxY - 4)
      else:
        # closes popup
        break
    
    popup.height = 9
    popup.recreate(stdscr, 80)
  finally:
    panel.CURSES_LOCK.release()

def draw(popup, properties):
  popup.clear()
  popup.win.box()
  
  # top label
  popup.addstr(0, 0, "Open File Descriptors:", curses.A_STANDOUT)
  
  if properties.errorMsg:
    popup.addstr(1, 2, properties.errorMsg, curses.A_BOLD | uiTools.getColor("red"))
  else:
    # text with file descriptor count and limit
    fdCount = len(properties.fdFile) + len(properties.fdConn) + len(properties.fdMisc)
    fdCountPer = 100 * fdCount / max(properties.fdLimit, 1)
    
    statsColor = "green"
    if fdCountPer >= 90: statsColor = "red"
    elif fdCountPer >= 50: statsColor = "yellow"
    
    countMsg = "%i / %i (%i%%)" % (fdCount, properties.fdLimit, fdCountPer)
    popup.addstr(1, 2, countMsg, curses.A_BOLD | uiTools.getColor(statsColor))
    
    # provides a progress bar reflecting the stats
    barWidth = popup.maxX - len(countMsg) - 6 # space between "[ ]" in progress bar
    barProgress = barWidth * fdCountPer / 100 # filled cells
    if fdCount > 0: barProgress = max(1, barProgress) # ensures one cell is filled unless really zero
    popup.addstr(1, len(countMsg) + 3, "[", curses.A_BOLD)
    popup.addstr(1, len(countMsg) + 4, " " * barProgress, curses.A_STANDOUT | uiTools.getColor(statsColor))
    popup.addstr(1, len(countMsg) + 4 + barWidth, "]", curses.A_BOLD)
    
    popup.win.hline(2, 1, curses.ACS_HLINE, popup.maxX - 2)
    
    # scrollable file descriptor listing
    lineNum = 3
    entryNum = properties.scroll
    while lineNum <= popup.maxY - 2:
      if entryNum < len(properties.fdFile):
        line = properties.fdFile[entryNum]
        color = "green"
      elif entryNum < len(properties.fdFile) + len(properties.fdMisc):
        line = properties.fdMisc[entryNum - len(properties.fdFile)]
        color = "cyan"
      else:
        line = properties.fdConn[entryNum - len(properties.fdFile) - len(properties.fdMisc)]
        color = "blue"
      
      popup.addstr(lineNum, 2, line, curses.A_BOLD | uiTools.getColor(color))
      lineNum += 1
      entryNum += 1
  
  popup.refresh()

