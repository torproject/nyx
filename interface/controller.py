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
                        # concurrency bugs produce especially sinister glitches)

# enums for message in control label
CTL_HELP, CTL_PAUSED, CTL_EVENT_INPUT, CTL_EVENT_ERR = range(4)

# panel order per page
PAGE_1 = ["summary", "control", "bandwidth", "log"]
# TODO: page 2: configuration information
# TODO: page 3: current connections

class ControlPanel(util.Panel):
  """ Draws single line label for interface controls. """
  
  def __init__(self, lock):
    util.Panel.__init__(self, lock, 1)
    self.msgType = CTL_HELP
    self.arg = ""
  
  def setMsg(self, msgType, arg=""):
    self.msgType = msgType
    self.arg = arg
  
  def redraw(self):
    if self.win:
      self.clear()
      
      if self.msgType == CTL_HELP: self.addstr(0, 0, "q: quit, e: change events, p: pause")
      elif self.msgType == CTL_PAUSED: self.addstr(0, 0, "Paused", curses.A_STANDOUT)
      elif self.msgType == CTL_EVENT_INPUT: self.addstr(0, 0, "Events to log: ")
      elif self.msgType == CTL_EVENT_ERR: self.addstr(0, 0, self.arg, curses.A_STANDOUT)
      else:
        assert False, "Unrecognized event type for control label: " + str(self.msgType)
      
      self.refresh()

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
  
  staticInfo = staticPanel.getStaticInfo(conn)
  panels = {
    "summary": staticPanel.SummaryPanel(cursesLock, staticInfo),
    "control": ControlPanel(cursesLock),
    "bandwidth": bandwidthPanel.BandwidthMonitor(cursesLock, conn),
    "log": logPanel.LogMonitor(cursesLock, loggedEvents)}
  
  # listeners that update bandwidth and log panels with Tor status
  conn.add_event_listener(panels["log"])
  conn.add_event_listener(panels["bandwidth"])
  
  # tells Tor to listen to the events we're interested
  loggedEvents = setEventListening(loggedEvents, conn, panels["log"])
  panels["log"].loggedEvents = loggedEvents # strips any that couldn't be set
  
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
      if y > oldY:
        # gives panels a chance to take advantage of the maximum bounds
        startY = 0
        for panelKey in PAGE_1:
          panels[panelKey].recreate(stdscr, startY)
          startY += panels[panelKey].height
      
      # if it's been at least five seconds since the last BW event Tor's probably done
      if not isUnresponsive and panels["log"].getHeartbeat() >= 5:
        isUnresponsive = True
        panels["log"].monitor_event("NOTICE", "Relay unresponsive (last heartbeat: %s)" % time.ctime(panels["log"].lastHeartbeat))
      elif isUnresponsive and panels["log"].getHeartbeat() < 5:
        # this really shouldn't happen - BW events happen every second...
        isUnresponsive = False
        panels["log"].monitor_event("WARN", "Relay resumed")
      
      # I haven't the foggiest why, but doesn't work if redrawn out of order...
      for panelKey in PAGE_1: panels[panelKey].redraw()
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
        isBwPaused = panels["bandwidth"].isPaused
        isLogPaused = panels["log"].isPaused
        panels["bandwidth"].setPaused(True)
        panels["log"].setPaused(True)
        
        # provides prompt
        panels["control"].setMsg(CTL_EVENT_INPUT)
        panels["control"].redraw()
        
        # makes cursor and typing visible
        try: curses.curs_set(1)
        except curses.error: pass
        curses.echo()
        
        # switches bandwidth area to list event types
        bwPanel = panels["bandwidth"]
        bwPanel.clear()
        bwPanel.addstr(0, 0, "Event Types:", util.LABEL_ATTR)
        lineNum = 1
        for line in logPanel.EVENT_LISTING.split("\n"):
          line = line.strip()
          bwPanel.addstr(lineNum, 0, line[:x - 1])
          lineNum += 1
        bwPanel.refresh()
        
        # gets user input (this blocks monitor updates)
        eventsInput = panels["control"].win.getstr(0, 15)
        eventsInput = eventsInput.replace(' ', '') # strips spaces
        
        # reverts visability settings
        try: curses.curs_set(0)
        except curses.error: pass
        curses.noecho()
        
        # TODO: it would be nice to quit on esc, but looks like this might not be possible...
        if eventsInput != "":
          try:
            expandedEvents = logPanel.expandEvents(eventsInput)
            loggedEvents = setEventListening(expandedEvents, conn, panels["log"])
            panels["log"].loggedEvents = loggedEvents
          except ValueError, exc:
            panels["control"].setMsg(CTL_EVENT_ERR, "Invalid flags: %s" % str(exc))
            panels["control"].redraw()
            time.sleep(2)
        
        msgType = CTL_PAUSED if isPaused else CTL_HELP
        panels["control"].setMsg(msgType)
        
        # returns listeners to previous pause status
        panels["bandwidth"].setPaused(isBwPaused)
        panels["log"].setPaused(isLogPaused)
      finally:
        cursesLock.release()
    elif key == ord('p') or key == ord('P'):
      # toggles update freezing
      cursesLock.acquire()
      try:
        isPaused = not isPaused
        panels["log"].setPaused(isPaused)
        panels["bandwidth"].setPaused(isPaused)
        msgType = CTL_PAUSED if isPaused else CTL_HELP
        panels["control"].setMsg(msgType)
      finally:
        cursesLock.release()

def startTorMonitor(conn, loggedEvents):
  curses.wrapper(drawTorMonitor, conn, loggedEvents)

