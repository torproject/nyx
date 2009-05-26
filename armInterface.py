# armInterface.py -- arm interface (curses monitor for relay status).
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

"""
Curses (terminal) interface for the arm relay status monitor.
"""

import os
import sys
import time
import curses
from threading import Lock
from TorCtl import TorCtl

REFRESH_RATE = 5                    # seconds between redrawing screen
BANDWIDTH_GRAPH_SAMPLES = 5         # seconds of data used for bar in graph
BANDWIDTH_GRAPH_COL = 30            # columns of data in graph
BANDWIDTH_GRAPH_COLOR_DL = "green"  # download section color
BANDWIDTH_GRAPH_COLOR_UL = "cyan"   # upload section color
MAX_LOG_ENTRIES = 80                # size of log buffer (max number of entries)

# default formatting constants
LABEL_ATTR = curses.A_STANDOUT
SUMMARY_ATTR = curses.A_NORMAL
LOG_ATTR = curses.A_NORMAL

# colors curses can handle
COLOR_LIST = (("red", curses.COLOR_RED),
             ("green", curses.COLOR_GREEN),
             ("yellow", curses.COLOR_YELLOW),
             ("blue", curses.COLOR_BLUE),
             ("cyan", curses.COLOR_CYAN),
             ("magenta", curses.COLOR_MAGENTA),
             ("black", curses.COLOR_BLACK),
             ("white", curses.COLOR_WHITE))

# foreground color mappings (starts uninitialized - all colors associated with default white fg / black bg)
COLOR_ATTR_INITIALIZED = False
COLOR_ATTR = dict([(color[0], 0) for color in COLOR_LIST])

# color coding for runlevel events
RUNLEVEL_EVENT_COLOR = {"DEBUG": "magenta", "INFO": "blue", "NOTICE": "green", "WARN": "yellow", "ERR": "red"}

class LogMonitor(TorCtl.PostEventListener):
  """
  Tor event listener, noting messages, the time, and their type in a curses
  subwindow.
  """
  
  def __init__(self, logScreen, includeBW, includeUnknown):
    TorCtl.PostEventListener.__init__(self)
    self.msgLog = []                # tuples of (isMsgFirstLine, logText, color)
    self.logScreen = logScreen      # curses window where log's displayed
    self.isPaused = False
    self.pauseBuffer = []           # location where messages are buffered if paused
    self.msgLogLock = Lock()        # haven't noticed any concurrency errors but better safe...
    self.includeBW = includeBW      # true if we're supposed to listen for BW events
    self.includeUnknown = includeUnknown    # true if registering unrecognized events
  
  # Listens for all event types and redirects to registerEvent
  # TODO: not sure how to stimulate all event types - should be tried before
  # implemented to see what's the best formatting, what information is
  # important, and to make sure of variable's types so we don't get exceptions.
  
  def circ_status_event(self, event):
    self.registerEvent("CIRC", "<STUB>", "white") # TODO: implement - variables: event.circ_id, event.status, event.path, event.purpose, event.reason, event.remote_reason
  
  def stream_status_event(self, event):
    self.registerEvent("STREAM", "<STUB>", "white") # TODO: implement - variables: event.strm_id, event.status, event.circ_id, event.target_host, event.target_port, event.reason, event.remote_reason, event.source, event.source_addr, event.purpose
  
  def or_conn_status_event(self, event):
    self.registerEvent("ORCONN", "<STUB>", "white") # TODO: implement - variables: event.status, event.endpoint, event.age, event.read_bytes, event.wrote_bytes, event.reason, event.ncircs
  
  def stream_bw_event(self, event):
    self.registerEvent("STREAM_BW", "<STUB>", "white") # TODO: implement - variables: event.strm_id, event.bytes_read, event.bytes_written
  
  def bandwidth_event(self, event):
    if self.includeBW: self.registerEvent("BW", "READ: %i, WRITTEN: %i" % (event.read, event.written), "cyan")
  
  def msg_event(self, event):
    self.registerEvent(event.level, event.msg, RUNLEVEL_EVENT_COLOR[event.level])
  
  def new_desc_event(self, event):
    self.registerEvent("NEWDESC", "<STUB>", "white") # TODO: implement - variables: event.idlist
  
  def address_mapped_event(self, event):
    self.registerEvent("ADDRMAP", "<STUB>", "white") # TODO: implement - variables: event.from_addr, event.to_addr, event.when
  
  def ns_event(self, event):
    self.registerEvent("NS", "<STUB>", "white") # TODO: implement - variables: event.nslist
  
  def new_consensus_event(self, event):
    self.registerEvent("NEWCONSENSUS", "<STUB>", "white") # TODO: implement - variables: event.nslist
  
  def unknown_event(self, event):
    if self.includeUnknown: self.registerEvent("UNKNOWN", event.event_string, "red")
  
  def registerEvent(self, type, msg, color):
    """
    Notes event and redraws log. If paused it's held in a temporary buffer.
    """
    
    eventTime = time.localtime()
    msgLine = "%02i:%02i:%02i [%s] %s" % (eventTime[3], eventTime[4], eventTime[5], type, msg)
    
    if self.isPaused:
      self.pauseBuffer.insert(0, (msgLine, color))
      if len(self.pauseBuffer) > MAX_LOG_ENTRIES: del self.pauseBuffer[MAX_LOG_ENTRIES:]
    else:
      self.msgLogLock.acquire()
      self.msgLog.insert(0, (msgLine, color))
      if len(self.msgLog) > MAX_LOG_ENTRIES: del self.msgLog[MAX_LOG_ENTRIES:]
      self.refreshDisplay()
      self.msgLogLock.release()
  
  def refreshDisplay(self):
    """
    Redraws message log. Entries stretch to use available space and may
    contain up to two lines. Starts with newest entries.
    """
    
    self.logScreen.erase()
    y, x = self.logScreen.getmaxyx()
    lineCount = 0
    
    for (line, color) in self.msgLog:
      # splits over too lines if too long
      if len(line) < x:
        self.logScreen.addstr(lineCount, 0, line[:x - 1], LOG_ATTR | COLOR_ATTR[color])
        lineCount += 1
      else:
        if lineCount >= y - 1: break
        (line1, line2) = self._splitLine(line, x)
        self.logScreen.addstr(lineCount, 0, line1, LOG_ATTR | COLOR_ATTR[color])
        self.logScreen.addstr(lineCount + 1, 0, line2[:x - 1], LOG_ATTR | COLOR_ATTR[color])
        lineCount += 2
      
      if lineCount >= y: break
    self.logScreen.refresh()
  
  def setPaused(self, isPause):
    """
    If true, prevents message log from being updated with new events.
    """
    
    if isPause == self.isPaused: return
    
    self.isPaused = isPause
    if self.isPaused: self.pauseBuffer = []
    else:
      self.msgLog = self.pauseBuffer + self.msgLog
      self.msgLogLock.acquire()
      self.refreshDisplay()
      self.msgLogLock.release()
    
  # divides long message to cover two lines
  def _splitLine(self, message, x):
    # divides message into two lines, attempting to do it on a wordbreak
    lastWordbreak = message[:x].rfind(" ")
    if x - lastWordbreak < 10:
      line1 = message[:lastWordbreak]
      line2 = "  %s" % message[lastWordbreak:].strip()
    else:
      # over ten characters until the last word - dividing
      line1 = "%s-" % message[:x - 2]
      line2 = "  %s" % message[x - 2:].strip()
    
    # ends line with ellipsis if too long
    if len(line2) > x:
      lastWordbreak = line2[:x - 4].rfind(" ")
      
      # doesn't use wordbreak if it's a long word or the whole line is one 
      # word (picking up on two space indent to have index 1)
      if x - lastWordbreak > 10 or lastWordbreak == 1: lastWordbreak = x - 4
      line2 = "%s..." % line2[:lastWordbreak]
    
    return (line1, line2)

class BandwidthMonitor(TorCtl.PostEventListener):
  """
  Tor event listener, taking bandwidth sampling and drawing bar graph. This is
  updated every second by the BW events and graph samples are spaced at
  BANDWIDTH_GRAPH_SAMPLES second intervals.
  """
  
  def __init__(self, bandwidthScreen):
    TorCtl.PostEventListener.__init__(self)
    self.tick = 0                           # number of updates performed
    self.bandwidthScreen = bandwidthScreen  # curses window where bandwidth's displayed
    self.lastDownloadRate = 0               # most recently sampled rates
    self.lastUploadRate = 0
    self.maxDownloadRate = 1                # max rates seen, used to determine graph bounds
    self.maxUploadRate = 1
    self.isPaused = False
    self.pauseBuffer = None                 # mirror instance used to track updates when paused
    
    # graphed download (read) and upload (write) rates - first index accumulator
    self.downloadRates = [0] * (BANDWIDTH_GRAPH_COL + 1)
    self.uploadRates = [0] * (BANDWIDTH_GRAPH_COL + 1)
    
  def bandwidth_event(self, event):
    if self.isPaused:
      self.pauseBuffer.bandwidth_event(event)
    else:
      self.lastDownloadRate = event.read
      self.lastUploadRate = event.written
      
      self.downloadRates[0] += event.read
      self.uploadRates[0] += event.written
      
      self.tick += 1
      if self.tick % BANDWIDTH_GRAPH_SAMPLES == 0:
        self.maxDownloadRate = max(self.maxDownloadRate, self.downloadRates[0])
        self.downloadRates.insert(0, 0)
        del self.downloadRates[BANDWIDTH_GRAPH_COL + 1:]
        
        self.maxUploadRate = max(self.maxUploadRate, self.uploadRates[0])
        self.uploadRates.insert(0, 0)
        del self.uploadRates[BANDWIDTH_GRAPH_COL + 1:]
      
      self.refreshDisplay()
  
  def refreshDisplay(self):
    """ Redraws bandwidth panel. """
    
    # doesn't draw if headless (indicating that the instance is for a pause buffer)
    if self.bandwidthScreen:
      self.bandwidthScreen.erase()
      y, x = self.bandwidthScreen.getmaxyx()
      dlColor = COLOR_ATTR[BANDWIDTH_GRAPH_COLOR_DL]
      ulColor = COLOR_ATTR[BANDWIDTH_GRAPH_COLOR_UL]
      
      # current numeric measures
      self.bandwidthScreen.addstr(0, 0, ("Downloaded (%s/sec):" % getSizeLabel(self.lastDownloadRate))[:x - 1], curses.A_BOLD | dlColor)
      if x > 35: self.bandwidthScreen.addstr(0, 35, ("Uploaded (%s/sec):" % getSizeLabel(self.lastUploadRate))[:x - 36], curses.A_BOLD | ulColor)
      
      # graph bounds in KB (uses highest recorded value as max)
      self.bandwidthScreen.addstr(1, 0, ("%4s" % str(self.maxDownloadRate / 1024 / BANDWIDTH_GRAPH_SAMPLES))[:x - 1], dlColor)
      self.bandwidthScreen.addstr(6, 0, "   0"[:x - 1], dlColor)
      
      if x > 35:
        self.bandwidthScreen.addstr(1, 35, ("%4s" % str(self.maxUploadRate / 1024 / BANDWIDTH_GRAPH_SAMPLES))[:x - 36], ulColor)
        self.bandwidthScreen.addstr(6, 35, "   0"[:x - 36], ulColor)
      
      # creates bar graph of bandwidth usage over time
      for col in range(BANDWIDTH_GRAPH_COL):
        if col > x - 8: break
        bytesDownloaded = self.downloadRates[col + 1]
        colHeight = min(5, 5 * bytesDownloaded / self.maxDownloadRate)
        for row in range(colHeight): self.bandwidthScreen.addstr(6 - row, col + 5, " ", curses.A_STANDOUT | dlColor)
      
      for col in range(BANDWIDTH_GRAPH_COL):
        if col > x - 42: break
        bytesUploaded = self.uploadRates[col + 1]
        colHeight = min(5, 5 * bytesUploaded / self.maxUploadRate)
        for row in range(colHeight): self.bandwidthScreen.addstr(6 - row, col + 40, " ", curses.A_STANDOUT | ulColor)
        
      self.bandwidthScreen.refresh()
  
  def setPaused(self, isPause):
    """
    If true, prevents bandwidth updates from being presented.
    """
    
    if isPause == self.isPaused: return
    
    self.isPaused = isPause
    if self.isPaused:
      if self.pauseBuffer == None:
        self.pauseBuffer = BandwidthMonitor(None, None, None)
      
      self.pauseBuffer.tick = self.tick
      self.pauseBuffer.lastDownloadRate = self.lastDownloadRate
      self.pauseBuffer.lastuploadRate = self.lastUploadRate
      self.pauseBuffer.downloadRates = self.downloadRates
      self.pauseBuffer.uploadRates = self.uploadRates
    else:
      self.tick = self.pauseBuffer.tick
      self.lastDownloadRate = self.pauseBuffer.lastDownloadRate
      self.lastUploadRate = self.pauseBuffer.lastuploadRate
      self.downloadRates = self.pauseBuffer.downloadRates
      self.uploadRates = self.pauseBuffer.uploadRates
  
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
  
  # adds note when default exit policy is appended
  if maxY >= 5:
    exitPolicy = vals["ExitPolicy"]
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
  else: screen.addstr(0, 0, "q: quit, p: pause"[:maxX - 1])
  screen.refresh()

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
  summaryScreen = stdscr.subwin(6, x, 0, 0)     # top static content
  pauseLabel = stdscr.subwin(1, x, 6, 0)        # line concerned with user interface
  bandwidthLabel = stdscr.subwin(1, x, 7, 0)    # bandwidth section label
  bandwidthScreen = stdscr.subwin(8, x, 8, 0)   # bandwidth measurements / graph
  logLabel = stdscr.subwin(1, x, 16, 0)         # message log label
  logScreen = stdscr.subwin(y - 17, x, 17, 0)   # uses all remaining space for message log
  
  # listeners that update bandwidthScreen and logScreen with Tor statuses
  logListener = LogMonitor(logScreen, "BW" in loggedEvents, "UNKNOWN" in loggedEvents)
  conn.add_event_listener(logListener)
  
  bandwidthListener = BandwidthMonitor(bandwidthScreen)
  conn.add_event_listener(bandwidthListener)
  
  # Tries to set events being listened for, displaying error for any event
  # types that aren't supported (possibly due to version issues)
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
  eventsListing = ", ".join(loggedEvents)
  
  bandwidthScreen.refresh()
  logScreen.refresh()
  isPaused = False
  
  while True:
    y, x = stdscr.getmaxyx()
    
    if x != oldX or y != oldY:
      # Screen size changed - redraw content to conform to the new dimensions.
      # Labels attempt to shrink gracefully.
      
      drawSummary(summaryScreen, staticInfo, x, y)
      drawPauseLabel(pauseLabel, isPaused, x)
      
      # Bandwidth label (drops stats if not enough room)
      rateLabel = getSizeLabel(int(staticInfo["BandwidthRate"]))
      burstLabel = getSizeLabel(int(staticInfo["BandwidthBurst"]))
      labelContents = "Bandwidth (cap: %s, burst: %s):" % (rateLabel, burstLabel)
      if x < len(labelContents):
        labelContents = "%s):" % labelContents[:labelContents.find(",")] # removes burst measure
        if x < len(labelContents): labelContents = "Bandwidth:"
      
      bandwidthLabel.erase()
      bandwidthLabel.addstr(0, 0, labelContents[:x - 1], LABEL_ATTR)
      bandwidthLabel.refresh()
      
      # gives bandwidth display a chance to redraw with new size
      bandwidthListener.refreshDisplay()
      
      # Event log label - uses ellipsis if too long, for instance:
      # Events (DEBUG, INFO, NOTICE, WARN...):
      eventsLabel = "Events"
      
      firstLabelLen = eventsListing.find(", ")
      if firstLabelLen == -1: firstLabelLen = len(eventsListing)
      else: firstLabelLen += 3
      
      if x > 10 + firstLabelLen:
        eventsLabel += " ("
        if len(eventsListing) > x - 11:
          labelBreak = eventsListing[:x - 12].rfind(", ")
          eventsLabel += "%s..." % eventsListing[:labelBreak]
        else: eventsLabel += eventsListing
        eventsLabel += ")"
      eventsLabel += ":"
      
      # gives message log a chance to redraw with new size
      logLabel.erase()
      logLabel.addstr(0, 0, eventsLabel[:x - 1], LABEL_ATTR)
      logLabel.refresh()
      
      logListener.msgLogLock.acquire()
      logListener.refreshDisplay()
      logListener.msgLogLock.release()
      oldX, oldY = x, y
    
    stdscr.refresh()
    key = stdscr.getch()
    if key == ord('q') or key == ord('Q'): break # quits
    elif key == ord('p') or key == ord('P'):
      # toggles update freezing
      isPaused = not isPaused
      logListener.setPaused(isPaused)
      bandwidthListener.setPaused(isPaused)
      drawPauseLabel(pauseLabel, isPaused, x)

def startTorMonitor(conn, loggedEvents):
  curses.wrapper(drawTorMonitor, conn, loggedEvents)

