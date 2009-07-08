#!/usr/bin/env python
# controller.py -- arm interface (curses monitor for relay status)
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

"""
Curses (terminal) interface for the arm relay status monitor.
"""

import time
import curses
from threading import RLock
from TorCtl import TorCtl

import util
import headerPanel
import bandwidthPanel
import logPanel
import connPanel
import confPanel

REFRESH_RATE = 5        # seconds between redrawing screen
cursesLock = RLock()    # global curses lock (curses isn't thread safe and
                        # concurrency bugs produce especially sinister glitches)

# enums for message in control label
CTL_HELP, CTL_PAUSED = range(2)

# panel order per page
PAGE_S = ["header", "control", "popup"]    # sticky (ie, always available) page
PAGES = [
  ["bandwidth", "log"],
  ["conn"],
  ["torrc"]]
PAUSEABLE = ["header", "bandwidth", "log"]
PAGE_COUNT = 3 # all page numbering is internally represented as 0-indexed
# TODO: page for configuration information

class ControlPanel(util.Panel):
  """ Draws single line label for interface controls. """
  
  def __init__(self, lock):
    util.Panel.__init__(self, lock, 1)
    self.msgText = CTL_HELP           # message text to be displyed
    self.msgAttr = curses.A_NORMAL    # formatting attributes
    self.page = 1                     # page number currently being displayed
  
  def setMsg(self, msgText, msgAttr=curses.A_NORMAL):
    """
    Sets the message and display attributes. If msgType matches CTL_HELP or
    CTL_PAUSED then uses the default message for those statuses.
    """
    
    self.msgText = msgText
    self.msgAttr = msgAttr
  
  def redraw(self):
    if self.win:
      self.clear()
      
      msgText = self.msgText
      msgAttr = self.msgAttr
      
      if msgText == CTL_HELP:
        msgText = "page %i / %i - q: quit, p: pause, h: page help" % (self.page, PAGE_COUNT)
        msgAttr = curses.A_NORMAL
      elif msgText == CTL_PAUSED:
        msgText = "Paused"
        msgAttr = curses.A_STANDOUT
      
      self.addstr(0, 0, msgText, msgAttr)
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
  
  panels = {
    "header": headerPanel.HeaderPanel(cursesLock, conn),
    "control": ControlPanel(cursesLock),
    "popup": util.Panel(cursesLock, 9),
    "bandwidth": bandwidthPanel.BandwidthMonitor(cursesLock, conn),
    "log": logPanel.LogMonitor(cursesLock, loggedEvents),
    "conn": connPanel.ConnPanel(cursesLock, conn),
    "torrc": confPanel.ConfPanel(cursesLock, conn.get_info("config-file")["config-file"])}
  
  # listeners that update bandwidth and log panels with Tor status
  conn.add_event_listener(panels["log"])
  conn.add_event_listener(panels["bandwidth"])
  
  # tells Tor to listen to the events we're interested
  loggedEvents = setEventListening(loggedEvents, conn, panels["log"])
  panels["log"].loggedEvents = loggedEvents # strips any that couldn't be set
  
  oldY, oldX = -1, -1
  isUnresponsive = False    # true if it's been over five seconds since the last BW event (probably due to Tor closing)
  isPaused = False          # if true updates are frozen
  page = 0
  netstatRefresh = time.time()  # time of last netstat refresh
  
  while True:
    # tried only refreshing when the screen was resized but it caused a
    # noticeable lag when resizing and didn't have an appreciable effect
    # on system usage
    
    cursesLock.acquire()
    try:
      y, x = stdscr.getmaxyx()
      if x > oldX or y > oldY:
        # gives panels a chance to take advantage of the maximum bounds
        startY = 0
        for panelKey in PAGE_S[:2]:
          panels[panelKey].recreate(stdscr, startY)
          startY += panels[panelKey].height
        
        isChanged = panels["popup"].recreate(stdscr, startY, 80)
        
        for panelSet in PAGES:
          tmpStartY = startY
          
          for panelKey in panelSet:
            panels[panelKey].recreate(stdscr, tmpStartY)
            tmpStartY += panels[panelKey].height
      
      # if it's been at least five seconds since the last BW event Tor's probably done
      if not isUnresponsive and panels["log"].getHeartbeat() >= 5:
        isUnresponsive = True
        panels["log"].monitor_event("NOTICE", "Relay unresponsive (last heartbeat: %s)" % time.ctime(panels["log"].lastHeartbeat))
      elif isUnresponsive and panels["log"].getHeartbeat() < 5:
        # this really shouldn't happen - BW events happen every second...
        isUnresponsive = False
        panels["log"].monitor_event("WARN", "Relay resumed")
      
      # if it's been at least five seconds since the last refresh of connection listing, update
      currentTime = time.time()
      if currentTime - netstatRefresh >= 5:
        panels["conn"].reset()
        netstatRefresh = currentTime
      
      # I haven't the foggiest why, but doesn't work if redrawn out of order...
      for panelKey in (PAGE_S + PAGES[page]): panels[panelKey].redraw()
      oldY, oldX = y, x
      stdscr.refresh()
    finally:
      cursesLock.release()
    
    key = stdscr.getch()
    if key == 27 or key == ord('q') or key == ord('Q'): break # quits (also on esc)
    elif key == curses.KEY_LEFT or key == curses.KEY_RIGHT:
      # switch page
      if key == curses.KEY_LEFT: page = (page - 1) % PAGE_COUNT
      else: page = (page + 1) % PAGE_COUNT
      
      # pauses panels that aren't visible to prevent events from accumilating
      # (otherwise they'll wait on the curses lock which might get demanding)
      for key in PAUSEABLE: panels[key].setPaused(isPaused or (key not in PAGES[page] and key not in PAGE_S))
      
      panels["control"].page = page + 1
      panels["control"].refresh()
    elif key == ord('p') or key == ord('P'):
      # toggles update freezing
      cursesLock.acquire()
      try:
        isPaused = not isPaused
        for key in PAUSEABLE: panels[key].setPaused(isPaused or key not in PAGES[page])
        panels["control"].setMsg(CTL_PAUSED if isPaused else CTL_HELP)
      finally:
        cursesLock.release()
    elif key == ord('h') or key == ord('H'):
      # displays popup for current page's controls
      cursesLock.acquire()
      try:
        for key in PAUSEABLE: panels[key].setPaused(True)
        
        # lists commands
        popup = panels["popup"]
        popup.clear()
        popup.win.box()
        popup.addstr(0, 0, "Page %i Commands:" % (page + 1), util.LABEL_ATTR)
        
        if page == 0:
          bwVisibleLabel = "visible" if panels["bandwidth"].isVisible else "hidden"
          popup.addfstr(1, 2, "b: toggle <u>b</u>andwidth panel (<b>%s</b>)" % bwVisibleLabel)
          popup.addfstr(1, 41, "e: change logged <u>e</u>vents")
        if page == 1:
          popup.addstr(1, 2, "up arrow: scroll up a line")
          popup.addstr(1, 41, "down arrow: scroll down a line")
          popup.addstr(2, 2, "page up: scroll up a page")
          popup.addstr(2, 41, "page down: scroll down a page")
          #popup.addstr(3, 2, "s: sort ordering")
          #popup.addstr(4, 2, "r: resolve hostnames")
          #popup.addstr(4, 41, "R: hostname auto-resolution")
          #popup.addstr(5, 2, "h: show IP/hostnames")
          #popup.addstr(5, 41, "c: clear hostname cache")
        elif page == 2:
          popup.addstr(1, 2, "up arrow: scroll up a line")
          popup.addstr(1, 41, "down arrow: scroll down a line")
          popup.addstr(2, 2, "page up: scroll up a page")
          popup.addstr(2, 41, "page down: scroll down a page")
          
          strippingLabel = "on" if panels["torrc"].stripComments else "off"
          popup.addfstr(3, 2, "s: comment <u>s</u>tripping (<b>%s</b>)" % strippingLabel)
          
          lineNumLabel = "on" if panels["torrc"].showLineNum else "off"
          popup.addfstr(3, 41, "n: line <u>n</u>umbering (<b>%s</b>)" % lineNumLabel)
          
          popup.addfstr(4, 2, "r: <u>r</u>eload torrc")
        
        popup.addstr(7, 2, "Press any key...")
        
        popup.refresh()
        
        curses.cbreak()
        stdscr.getch()
        curses.halfdelay(REFRESH_RATE * 10)
        
        for key in PAUSEABLE: panels[key].setPaused(isPaused or key not in PAGES[page])
      finally:
        cursesLock.release()
    elif page == 0 and (key == ord('b') or key == ord('B')):
      # toggles bandwidth panel visability
      panels["bandwidth"].setVisible(not panels["bandwidth"].isVisible)
      oldY = -1 # force resize event
    elif page == 0 and (key == ord('e') or key == ord('E')):
      # allow user to enter new types of events to log - unchanged if left blank
      cursesLock.acquire()
      try:
        for key in PAUSEABLE: panels[key].setPaused(True)
        
        # provides prompt
        panels["control"].setMsg("Events to log: ")
        panels["control"].redraw()
        
        # makes cursor and typing visible
        try: curses.curs_set(1)
        except curses.error: pass
        curses.echo()
        
        # lists event types
        popup = panels["popup"]
        popup.clear()
        popup.addstr(0, 0, "Event Types:", util.LABEL_ATTR)
        lineNum = 1
        for line in logPanel.EVENT_LISTING.split("\n"):
          line = "  " + line.strip()
          popup.addstr(lineNum, 0, line)
          lineNum += 1
        popup.refresh()
        
        # gets user input (this blocks monitor updates)
        eventsInput = panels["control"].win.getstr(0, 15)
        eventsInput = eventsInput.replace(' ', '') # strips spaces
        
        # reverts visability settings
        try: curses.curs_set(0)
        except curses.error: pass
        curses.noecho()
        curses.halfdelay(REFRESH_RATE * 10) # evidenlty previous tweaks reset this...
        
        # TODO: it would be nice to quit on esc, but looks like this might not be possible...
        if eventsInput != "":
          try:
            expandedEvents = logPanel.expandEvents(eventsInput)
            loggedEvents = setEventListening(expandedEvents, conn, panels["log"])
            panels["log"].loggedEvents = loggedEvents
          except ValueError, exc:
            panels["control"].setMsg("Invalid flags: %s" % str(exc), curses.A_STANDOUT)
            panels["control"].redraw()
            time.sleep(2)
        
        panels["control"].setMsg(CTL_PAUSED if isPaused else CTL_HELP)
        for key in PAUSEABLE: panels[key].setPaused(isPaused or key not in PAGES[page])
      finally:
        cursesLock.release()
    elif page == 1 and (key == ord('s') or key == ord('S')):
      continue
      
      # set ordering for connection listing
      cursesLock.acquire()
      try:
        for key in PAUSEABLE: panels[key].setPaused(True)
        curses.cbreak() # wait indefinitely for key presses (no timeout)
        
        # lists event types
        popup = panels["popup"]
        selections = []    # new ordering
        cursorLoc = 0     # index of highlighted option
        
        # listing of inital ordering
        prevOrdering = "<b>Current Order: "
        for sort in panels["conn"].sortOrdering: prevOrdering += connPanel.getSortLabel(sort, True) + ", "
        prevOrdering = prevOrdering[:-2] + "</b>"
        
        # Makes listing of all options
        options = []
        for (type, label) in connPanel.SORT_TYPES: options.append(label)
        options.append("Cancel")
        
        while len(selections) < 3:
          popup.clear()
          popup.win.box()
          popup.addstr(0, 0, "Connection Ordering:", util.LABEL_ATTR)
          popup.addfstr(1, 2, prevOrdering)
          
          # provides new ordering
          newOrdering = "<b>New Order: "
          if selections:
            for sort in selections: newOrdering += connPanel.getSortLabel(sort, True) + ", "
            newOrdering = newOrdering[:-2] + "</b>"
          else: newOrdering += "</b>"
          popup.addfstr(2, 2, newOrdering)
          
          row, col, index = 4, 0, 0
          for option in options:
            popup.addstr(row, col * 19 + 2, option, curses.A_STANDOUT if cursorLoc == index else curses.A_NORMAL)
            col += 1
            index += 1
            if col == 4: row, col = row + 1, 0
          
          popup.refresh()
          
          key = stdscr.getch()
          if key == curses.KEY_LEFT: cursorLoc = max(0, cursorLoc - 1)
          elif key == curses.KEY_RIGHT: cursorLoc = min(len(options) - 1, cursorLoc + 1)
          elif key == curses.KEY_UP: cursorLoc = max(0, cursorLoc - 4)
          elif key == curses.KEY_DOWN: cursorLoc = min(len(options) - 1, cursorLoc + 4)
          elif key in (curses.KEY_ENTER, 10, ord(' ')):
            # selected entry (the ord of '10' seems needed to pick up enter)
            selection = options[cursorLoc]
            if selection == "Cancel": break
            else:
              selections.append(connPanel.getSortType(selection))
              options.remove(selection)
              cursorLoc = min(cursorLoc, len(options) - 1)
          
        if len(selections) == 3: panels["conn"].sortOrdering = selections
        curses.halfdelay(REFRESH_RATE * 10) # reset normal pausing behavior
      finally:
        cursesLock.release()
    elif page == 1:
      panels["conn"].handleKey(key)
    elif page == 2:
      panels["torrc"].handleKey(key)

def startTorMonitor(conn, loggedEvents):
  curses.wrapper(drawTorMonitor, conn, loggedEvents)

