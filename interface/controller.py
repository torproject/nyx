# controller.py -- arm interface (curses monitor for relay status).
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

"""
Curses (terminal) interface for the arm relay status monitor.
"""

import os
import sys
import time
import curses

import eventLog
import bandwidthMonitor

from threading import RLock
from TorCtl import TorCtl

REFRESH_RATE = 5                    # seconds between redrawing screen
cursesLock = RLock()                # curses isn't thread safe and concurrency
                                    # bugs produce especially sinister glitches

# default formatting constants
LABEL_ATTR = curses.A_STANDOUT
SUMMARY_ATTR = curses.A_NORMAL

# colors curses can handle
COLOR_LIST = (("red", curses.COLOR_RED),
             ("green", curses.COLOR_GREEN),
             ("yellow", curses.COLOR_YELLOW),
             ("blue", curses.COLOR_BLUE),
             ("cyan", curses.COLOR_CYAN),
             ("magenta", curses.COLOR_MAGENTA),
             ("black", curses.COLOR_BLACK),
             ("white", curses.COLOR_WHITE))

# TODO: change COLOR_ATTR into something that can be imported via 'from'
# foreground color mappings (starts uninitialized - all colors associated with default white fg / black bg)
COLOR_ATTR_INITIALIZED = False
COLOR_ATTR = dict([(color[0], 0) for color in COLOR_LIST])

def getSizeLabel(bytes):
  """
  Converts byte count into label in its most significant units, for instance
  7500 bytes would return "7 KB".
  """
  
  if bytes >= 1073741824: return "%i GB" % (bytes / 1073741824)
  elif bytes >= 1048576: return "%i MB" % (bytes / 1048576)
  elif bytes >= 1024: return "%i KB" % (bytes / 1024)
  else: return "%i bytes" % bytes

def getStaticInfo(conn):
  """
  Provides mapping of static Tor settings and system information to their
  corresponding string values. Keys include:
  info - version, config-file, address, fingerprint
  sys - sys-name, sys-os, sys-version
  config - Nickname, ORPort, DirPort, ControlPort, ExitPolicy, BandwidthRate, BandwidthBurst
  config booleans - IsPasswordAuthSet, IsCookieAuthSet
  """
  
  vals = conn.get_info(["version", "config-file"])
  
  # gets parameters that throw errors if unavailable
  for param in ["address", "fingerprint"]:
    try:
      vals.update(conn.get_info(param))
    except TorCtl.ErrorReply:
      vals[param] = "Unknown"
  
  # populates with some basic system information
  unameVals = os.uname()
  vals["sys-name"] = unameVals[1]
  vals["sys-os"] = unameVals[0]
  vals["sys-version"] = unameVals[2]
  
  # parameters from the user's torrc
  configFields = ["Nickname", "ORPort", "DirPort", "ControlPort", "ExitPolicy", "BandwidthRate", "BandwidthBurst"]
  vals.update(dict([(key, conn.get_option(key)[0][1]) for key in configFields]))
  
  # simply keeps booleans for if authentication info is set
  vals["IsPasswordAuthSet"] = not conn.get_option("HashedControlPassword")[0][1] == None
  vals["IsCookieAuthSet"] = conn.get_option("CookieAuthentication")[0][1] == "1"
  
  return vals

def drawSummary(screen, vals, maxX, maxY):
  """
  Draws top area containing static information.
  
  arm - <System Name> (<OS> <Version>)     Tor <Tor Version>
  <Relay Nickname> - <IP Addr>:<ORPort>, [Dir Port: <DirPort>, ]Control Port (<open, password, cookie>): <ControlPort>
  Fingerprint: <Fingerprint>
  Config: <Config>
  Exit Policy: <ExitPolicy>
  
  Example:
  arm - odin (Linux 2.6.24-24-generic)     Tor 0.2.0.34 (r18423)
  odin - 76.104.132.98:9001, Dir Port: 9030, Control Port (cookie): 9051
  Fingerprint: BDAD31F6F318E0413833E8EBDA956F76E4D66788
  Config: /home/atagar/.vidalia/torrc
  Exit Policy: reject *:*
  """
  
  # extra erase/refresh is needed to avoid internal caching screwing up and
  # refusing to redisplay content in the case of graphical glitches - probably
  # an obscure curses bug...
  screen.erase()
  screen.refresh()
  
  screen.erase()
  
  # Line 1
  if maxY >= 1:
    screen.addstr(0, 0, ("arm - %s (%s %s)" % (vals["sys-name"], vals["sys-os"], vals["sys-version"]))[:maxX - 1], SUMMARY_ATTR)
    if 45 < maxX: screen.addstr(0, 45, ("Tor %s" % vals["version"])[:maxX - 46], SUMMARY_ATTR)
  
  # Line 2 (authentication label red if open, green if credentials required)
  if maxY >= 2:
    dirPortLabel = "Dir Port: %s, " % vals["DirPort"] if not vals["DirPort"] == None else ""
    
    # TODO: if both cookie and password are set then which takes priority?
    if vals["IsPasswordAuthSet"]: controlPortAuthLabel = "password"
    elif vals["IsCookieAuthSet"]: controlPortAuthLabel = "cookie"
    else: controlPortAuthLabel = "open"
    controlPortAuthColor = "red" if controlPortAuthLabel == "open" else "green"
    
    labelStart = "%s - %s:%s, %sControl Port (" % (vals["Nickname"], vals["address"], vals["ORPort"], dirPortLabel)
    screen.addstr(1, 0, labelStart[:maxX - 1], SUMMARY_ATTR)
    xLoc = len(labelStart)
    if xLoc < maxX: screen.addstr(1, xLoc, controlPortAuthLabel[:maxX - xLoc - 1], COLOR_ATTR[controlPortAuthColor] | SUMMARY_ATTR)
    xLoc += len(controlPortAuthLabel)
    if xLoc < maxX: screen.addstr(1, xLoc, ("): %s" % vals["ControlPort"])[:maxX - xLoc - 1], SUMMARY_ATTR)
    
  # Lines 3-5
  if maxY >= 3: screen.addstr(2, 0, ("Fingerprint: %s" % vals["fingerprint"])[:maxX - 1], SUMMARY_ATTR)
  if maxY >= 4: screen.addstr(3, 0, ("Config: %s" % vals["config-file"])[:maxX - 1], SUMMARY_ATTR)
  if maxY >= 5:
    exitPolicy = vals["ExitPolicy"]
    
    # adds note when default exit policy is appended
    if exitPolicy == None: exitPolicy = "<default>"
    elif not exitPolicy.endswith("accept *:*") and not exitPolicy.endswith("reject *:*"):
      exitPolicy += ", <default>"
    screen.addstr(4, 0, ("Exit Policy: %s" % exitPolicy)[:maxX - 1], SUMMARY_ATTR)
  
  screen.refresh()

def drawPauseLabel(screen, isPaused, maxX):
  """ Draws single line label for interface controls. """
  # TODO: possibly include 'h: help' if the project grows much
  screen.erase()
  if isPaused: screen.addstr(0, 0, "Paused"[:maxX - 1], LABEL_ATTR)
  else: screen.addstr(0, 0, "q: quit, e: change events, p: pause"[:maxX - 1])
  screen.refresh()

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
        logListener.registerEvent("ARM-ERR", "Unsupported event type: %s" % eventType, "red")
      else:
        raise exc
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
  
  global COLOR_ATTR_INITIALIZED
  
  # use terminal defaults to allow things like semi-transparent backgrounds
  curses.use_default_colors()
  
  # initializes color mappings if able
  if curses.has_colors() and not COLOR_ATTR_INITIALIZED:
    COLOR_ATTR_INITIALIZED = True
    colorpair = 0
    
    for name, fgColor in COLOR_LIST:
      colorpair += 1
      curses.init_pair(colorpair, fgColor, -1) # -1 allows for default (possibly transparent) background
      COLOR_ATTR[name] = curses.color_pair(colorpair)
  
  curses.halfdelay(REFRESH_RATE * 10)   # uses getch call as timer for REFRESH_RATE seconds
  staticInfo = getStaticInfo(conn)
  y, x = stdscr.getmaxyx()
  oldX, oldY = -1, -1
  
  # attempts to make the cursor invisible (not supported in all terminals)
  try: curses.curs_set(0)
  except curses.error: pass
  
  # note: subwindows need a character buffer (either in the x or y direction)
  # from actual content to prevent crash when shrank
  # max/min calls are to allow the program to initialize if the screens too small
  summaryScreen = stdscr.subwin(min(y, 6), x, 0, 0)                   # top static content
  pauseLabel = stdscr.subwin(1, x, min(y - 1, 6), 0)                  # line concerned with user interface
  bandwidthLabel = stdscr.subwin(1, x, min(y - 1, 7), 0)              # bandwidth section label
  bandwidthScreen = stdscr.subwin(min(y - 8, 8), x, min(y - 2, 8), 0) # bandwidth measurements / graph
  logLabel = stdscr.subwin(1, x, min(y - 1, 16), 0)                   # message log label
  logScreen = stdscr.subwin(max(1, y - 17), x, min(y - 1, 17), 0)     # uses all remaining space for message log
  
  # listeners that update bandwidthScreen and logScreen with Tor statuses
  logListener = eventLog.LogMonitor(logScreen, "BW" in loggedEvents, "UNKNOWN" in loggedEvents, cursesLock)
  conn.add_event_listener(logListener)
  
  bandwidthListener = bandwidthMonitor.BandwidthMonitor(bandwidthScreen, cursesLock)
  conn.add_event_listener(bandwidthListener)
  
  loggedEvents = setEventListening(loggedEvents, conn, logListener)
  eventsListing = ", ".join(loggedEvents)
  
  bandwidthScreen.refresh()
  logScreen.refresh()
  isPaused = False
  tick = -1 # loop iteration
  
  while True:
    tick += 1
    y, x = stdscr.getmaxyx()
    
    if x != oldX or y != oldY or tick % 5 == 0:
      # resized - redraws content
      # occasionally refreshes anyway to help make resilient against an
      # occasional graphical glitch - currently only known cause of this is 
      # displaced subwindows overwritting static content when resized to be 
      # very small
      
      # note: Having this refresh only occure after this resize is detected 
      # (getmaxyx changes) introduces a noticeable lag in screen updates. If 
      # it's done every pass through the loop resize repaint responsiveness is 
      # perfect, but this is much more demanding in the common case (no resizing).
      cursesLock.acquire()
      
      try:
        if y > oldY:
          # screen height increased - recreates subwindows that are able to grow
          # I'm not sure if this is some sort of memory leak but the Python curses
          # bindings seem to lack all of the following:
          # - subwindow deletion (to tell curses to free the memory)
          # - subwindow moving/resizing (to restore the displaced windows)
          # so this is the only option (besides removing subwindows entirly which 
          # would mean more complicated code and no more selective refreshing)
          
          # TODO: BUG - this fails if doing maximize operation (relies on gradual growth/shrink - 
          # fix would be to eliminate elif and consult new y)
          if oldY < 6: summaryScreen = stdscr.subwin(y, x, 0, 0)
          elif oldY < 7: pauseLabel = stdscr.subwin(1, x, 6, 0)
          elif oldY < 8: bandwidthLabel = stdscr.subwin(1, x, 7, 0)
          elif oldY < 16:
            bandwidthScreen = stdscr.subwin(y - 8, x, 8, 0)
            bandwidthListener.bandwidthScreen = bandwidthScreen
          elif oldY < 17: logLabel = stdscr.subwin(1, x, 16, 0)
          else:
            logScreen = stdscr.subwin(y - 17, x, 17, 0)
            logListener.logScreen = logScreen
        
        drawSummary(summaryScreen, staticInfo, x, y)
        drawPauseLabel(pauseLabel, isPaused, x)
        bandwidthMonitor.drawBandwidthLabel(bandwidthLabel, staticInfo, x)
        bandwidthListener.refreshDisplay()
        eventLog.drawEventLogLabel(logLabel, eventsListing, x)
        logListener.refreshDisplay()
        oldX, oldY = x, y
        stdscr.refresh()
      finally:
        cursesLock.release()
    
    key = stdscr.getch()
    if key == ord('q') or key == ord('Q'): break # quits
    elif key == ord('e') or key == ord('E'):
      # allow user to enter new types of events to log - blank leaves unchanged
      
      cursesLock.acquire()
      try:
        # pauses listeners so events can still be handed (otherwise they wait
        # on curses lock which might get demanding if the user takes their time)
        isBwPaused = bandwidthListener.isPaused
        isLogPaused = logListener.isPaused
        bandwidthListener.setPaused(True)
        logListener.setPaused(True)
        
        # provides prompt
        pauseLabel.erase()
        pauseLabel.addstr(0, 0, "Events to log: "[:x - 1])
        pauseLabel.refresh()
        
        # makes cursor and typing visible
        try: curses.curs_set(1)
        except curses.error: pass
        curses.echo()
        
        # switches bandwidth area to list event types
        bandwidthLabel.erase()
        bandwidthLabel.addstr(0, 0, "Event Types:"[:x - 1], LABEL_ATTR)
        bandwidthLabel.refresh()
        
        bandwidthScreen.erase()
        bandwidthScreenMaxY, tmp = bandwidthScreen.getmaxyx()
        lineNum = 0
        for line in eventLog.EVENT_LISTING.split("\n"):
          line = line.strip()
          if bandwidthScreenMaxY <= lineNum: break
          bandwidthScreen.addstr(lineNum, 0, line[:x - 1])
          lineNum += 1
        bandwidthScreen.refresh()
        
        # gets user input (this blocks monitor updates)
        eventsInput = pauseLabel.getstr(0, 15)
        
        # strips spaces
        eventsInput = eventsInput.replace(' ', '')
        
        # reverts visability settings
        try: curses.curs_set(0)
        except curses.error: pass
        curses.noecho()
        
        # TODO: it would be nice to quit on esc, but looks like this might not be possible...
        if eventsInput != "":
          try:
            expandedEvents = eventLog.expandEvents(eventsInput)
            logListener.includeBW = "BW" in expandedEvents
            logListener.includeUnknown = "UNKNOWN" in expandedEvents
            
            loggedEvents = setEventListening(expandedEvents, conn, logListener)
            eventsListing = ", ".join(loggedEvents)
          except ValueError, exc:
            pauseLabel.erase()
            pauseLabel.addstr(0, 0, ("Invalid flags: %s" % str(exc))[:x - 1], curses.A_STANDOUT)
            pauseLabel.refresh()
            time.sleep(2)
        
        # returns listeners to previous pause status
        bandwidthListener.setPaused(isBwPaused)
        logListener.setPaused(isLogPaused)
        
        oldX = -1 # forces refresh (by spoofing a resize)
      finally:
        cursesLock.release()
      
      
    elif key == ord('p') or key == ord('P'):
      # toggles update freezing
      cursesLock.acquire()
      try:
        isPaused = not isPaused
        logListener.setPaused(isPaused)
        bandwidthListener.setPaused(isPaused)
        drawPauseLabel(pauseLabel, isPaused, x)
      finally:
        cursesLock.release()

def startTorMonitor(conn, loggedEvents):
  curses.wrapper(drawTorMonitor, conn, loggedEvents)

