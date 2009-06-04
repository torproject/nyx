#!/usr/bin/env python
# controller.py -- arm interface (curses monitor for relay status).
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

"""
Curses (terminal) interface for the arm relay status monitor.
"""

import time
import curses
from threading import RLock
from TorCtl import TorCtl

import util
import staticPanel
import bandwidthPanel
import logPanel

REFRESH_RATE = 5        # seconds between redrawing screen
cursesLock = RLock()    # global curses lock (curses isn't thread safe and
                        # concurrency bugs produce especially sinister glitches

CTL_HELP, CTL_PAUSED, CTL_EVENT_INPUT, CTL_EVENT_ERR = range(4) # enums for message in control label

# mapping of panels to (height, start y), -1 if unlimited
PANEL_INFO = {
  "summary":        (6, 0),     # top static content
  "control":        (1, 6),     # line for user input
  "bandwidthLabel": (1, 7),     # bandwidth section label
  "bandwidth":      (8, 8),     # bandwidth measurements / graph
  "logLabel":       (1, 16),    # message log label
  "log":            (-1, 17)}   # uses all remaining space for message log

def drawControlLabel(scr, msgType, arg=""):
  """ Draws single line label for interface controls. """
  scr.clear()
  
  if msgType == CTL_HELP: scr.addstr(0, 0, "q: quit, e: change events, p: pause")
  elif msgType == CTL_PAUSED: scr.addstr(0, 0, "Paused", curses.A_STANDOUT)
  elif msgType == CTL_EVENT_INPUT: scr.addstr(0, 0, "Events to log: ")
  elif msgType == CTL_EVENT_ERR: scr.addstr(0, 0, arg, curses.A_STANDOUT)
  else:
    assert False, "Unrecognized event type for control label: " + str(msgType)
  
  scr.refresh()

def setEventListening(loggedEvents, conn, logListener):
  """
  Tries to set events being listened for, displaying error for any event
  types that aren't supported (possibly due to version issues). This returns 
  a list of event types that were successfully set.
  """
  eventsSet = False
  
  while not eventsSet:
    try:
      # adds BW events if not already included (so bandwidth monitor will work)
      # removes UNKNOWN since not an actual event type
      connEvents = loggedEvents.union(set(["BW"]))
      connEvents.discard("UNKNOWN")
      conn.set_events(connEvents)
      eventsSet = True
    except TorCtl.ErrorReply, exc:
      msg = str(exc)
      if "Unrecognized event" in msg:
        # figure out type of event we failed to listen for
        start = msg.find("event \"") + 7
        end = msg.rfind("\"")
        eventType = msg[start:end]
        if eventType == "BW": raise exc # bandwidth monitoring won't work - best to crash
        
        # removes and notes problem
        loggedEvents.remove(eventType)
        logListener.monitor_event("WARN", "Unsupported event type: %s" % eventType)
      else:
        raise exc
    except TorCtl.TorCtlClosed:
      return []
  
  loggedEvents = list(loggedEvents)
  loggedEvents.sort() # alphabetizes
  return loggedEvents

def refreshSubwindows(stdscr, panels={}):
  """
  Creates constituent parts of the display. Any subwindows that have been
  displaced are recreated to take advantage of the maximum bounds.
  """
  
  y, x = stdscr.getmaxyx()
  
  if panels == {}:
    # initializes subwindows - upper left must be a valid coordinate
    for panelName, (maxHeight, startY) in PANEL_INFO.items():
      if maxHeight == -1: height = max(1, y - startY)
      else: height = max(1, min(maxHeight, y - startY))
      startY = min(startY, y - 1)
      panels[panelName] = util.TermSubwindow(stdscr.subwin(height, x, startY, 0), cursesLock, startY)
  else:
    # I'm not sure if recreating subwindows is some sort of memory leak but the
    # Python curses bindings seem to lack all of the following:
    # - subwindow deletion (to tell curses to free the memory)
    # - subwindow moving/resizing (to restore the displaced windows)
    # so this is the only option (besides removing subwindows entirly which 
    # would mean more complicated code and no more selective refreshing)
    
    for panelName, (maxHeight, startY) in PANEL_INFO.items():
      if startY > y: continue # out of bounds - ignore
      panelSrc = panels[panelName]
      currentY, currentX = panelSrc.win.getparyx()
      currentHeight, currentWidth = panelSrc.win.getmaxyx()
      
      # figure out panel can grow - if so recreate
      if maxHeight == -1: height = max(1, y - startY)
      else: height = max(1, min(maxHeight, y - startY))
      startY = min(startY, y - 1)
      
      if currentY < startY or currentHeight < height:
        panels[panelName].win = stdscr.subwin(height, x, startY, 0)
  
  return panels

def drawTorMonitor(stdscr, conn, loggedEvents):
  """
  Starts arm interface reflecting information on provided control port.
  
  stdscr - curses window
  conn - active Tor control port connection
  loggedEvents - types of events to be logged (plus an optional "UNKNOWN" for
    otherwise unrecognized events)
  """
  
  curses.use_default_colors()           # allows things like semi-transparent backgrounds
  util.initColors()                     # initalizes color pairs for colored text
  curses.halfdelay(REFRESH_RATE * 10)   # uses getch call as timer for REFRESH_RATE seconds
  
  # attempts to make the cursor invisible (not supported in all terminals)
  try: curses.curs_set(0)
  except curses.error: pass
  
  panels = refreshSubwindows(stdscr)
  staticInfo = staticPanel.getStaticInfo(conn)
  
  # listeners that update bandwidth and log panels with Tor status
  logListener = logPanel.LogMonitor(panels["log"], "BW" in loggedEvents, "UNKNOWN" in loggedEvents)
  conn.add_event_listener(logListener)
  
  bandwidthListener = bandwidthPanel.BandwidthMonitor(panels["bandwidth"])
  conn.add_event_listener(bandwidthListener)
  
  loggedEvents = setEventListening(loggedEvents, conn, logListener)
  eventsListing = ", ".join(loggedEvents)
  oldY, oldX = -1, -1
  isUnresponsive = False    # true if it's been over five seconds since the last BW event (probably due to Tor closing)
  isPaused = False          # if true updates are frozen
  
  while True:
    # tried only refreshing when the screen was resized but it caused a
    # noticeable lag when resizing and didn't have an appreciable effect
    # on system usage
    
    cursesLock.acquire()
    try:
      y, x = stdscr.getmaxyx()
      if y > oldY: panels = refreshSubwindows(stdscr, panels)
      
      # if it's been at least five seconds since the last BW event Tor's probably done
      if not isUnresponsive and logListener.getHeartbeat() >= 5:
        isUnresponsive = True
        logListener.monitor_event("NOTICE", "Relay unresponsive (last heartbeat: %s)" % time.ctime(logListener.lastHeartbeat))
      elif isUnresponsive and logListener.getHeartbeat() < 5:
        # this really shouldn't happen - BW events happen every second...
        isUnresponsive = False
        logListener.monitor_event("WARN", "Relay resumed")
      
      staticPanel.drawSummary(panels["summary"], staticInfo)
      
      msgType = CTL_PAUSED if isPaused else CTL_HELP
      drawControlLabel(panels["control"], msgType)
      
      bandwidthPanel.drawBandwidthLabel(panels["bandwidthLabel"], staticInfo)
      bandwidthListener.refreshDisplay()
      
      logPanel.drawEventLogLabel(panels["logLabel"], eventsListing)
      logListener.refreshDisplay()
      
      oldY, oldX = y, x
      stdscr.refresh()
    finally:
      cursesLock.release()
    
    key = stdscr.getch()
    if key == 27 or key == ord('q') or key == ord('Q'): break # quits (also on esc)
    elif key == ord('e') or key == ord('E'):
      # allow user to enter new types of events to log - unchanged if left blank
      
      cursesLock.acquire()
      try:
        # pauses listeners so events can still be handed (otherwise they wait
        # on curses lock which might get demanding if the user takes their time)
        isBwPaused = bandwidthListener.isPaused
        isLogPaused = logListener.isPaused
        bandwidthListener.setPaused(True)
        logListener.setPaused(True)
        
        # provides prompt
        drawControlLabel(panels["control"], CTL_EVENT_INPUT)
        
        # makes cursor and typing visible
        try: curses.curs_set(1)
        except curses.error: pass
        curses.echo()
        
        # switches bandwidth area to list event types
        panels["bandwidthLabel"].clear()
        panels["bandwidthLabel"].addstr(0, 0, "Event Types:", util.LABEL_ATTR)
        panels["bandwidthLabel"].refresh()
        
        panels["bandwidth"].clear()
        lineNum = 0
        for line in logPanel.EVENT_LISTING.split("\n"):
          line = line.strip()
          panels["bandwidth"].addstr(lineNum, 0, line[:x - 1])
          lineNum += 1
        panels["bandwidth"].refresh()
        
        # gets user input (this blocks monitor updates)
        eventsInput = panels["control"].win.getstr(0, 15)
        
        # strips spaces
        eventsInput = eventsInput.replace(' ', '')
        
        # reverts visability settings
        try: curses.curs_set(0)
        except curses.error: pass
        curses.noecho()
        
        # TODO: it would be nice to quit on esc, but looks like this might not be possible...
        if eventsInput != "":
          try:
            expandedEvents = logPanel.expandEvents(eventsInput)
            logListener.includeBW = "BW" in expandedEvents
            logListener.includeUnknown = "UNKNOWN" in expandedEvents
            
            loggedEvents = setEventListening(expandedEvents, conn, logListener)
            eventsListing = ", ".join(loggedEvents)
          except ValueError, exc:
            drawControlLabel(panels["control"], CTL_EVENT_ERR, "Invalid flags: %s" % str(exc))
            time.sleep(2)
        
        # returns listeners to previous pause status
        bandwidthListener.setPaused(isBwPaused)
        logListener.setPaused(isLogPaused)
      finally:
        cursesLock.release()
    elif key == ord('p') or key == ord('P'):
      # toggles update freezing
      cursesLock.acquire()
      try:
        isPaused = not isPaused
        logListener.setPaused(isPaused)
        bandwidthListener.setPaused(isPaused)
        msgType = CTL_PAUSED if isPaused else CTL_HELP
        drawControlLabel(panels["control"], msgType)
      finally:
        cursesLock.release()

def startTorMonitor(conn, loggedEvents):
  curses.wrapper(drawTorMonitor, conn, loggedEvents)

