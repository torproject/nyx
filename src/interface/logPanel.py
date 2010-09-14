"""
Panel providing a chronological log of events its been configured to listen
for. This provides prepopulation from the log file and supports filtering by
regular expressions.
"""

import time
import curses
import threading
from curses.ascii import isprint

from TorCtl import TorCtl

from util import log, panel, sysTools, torTools, uiTools

TOR_EVENT_TYPES = {
  "d": "DEBUG",   "a": "ADDRMAP",          "k": "DESCCHANGED",  "s": "STREAM",
  "i": "INFO",    "f": "AUTHDIR_NEWDESCS", "g": "GUARD",        "r": "STREAM_BW",
  "n": "NOTICE",  "h": "BUILDTIMEOUT_SET", "l": "NEWCONSENSUS", "t": "STATUS_CLIENT",
  "w": "WARN",    "b": "BW",               "m": "NEWDESC",      "u": "STATUS_GENERAL",
  "e": "ERR",     "c": "CIRC",             "p": "NS",           "v": "STATUS_SERVER",
                  "j": "CLIENTS_SEEN",     "q": "ORCONN"}

EVENT_LISTING = """        d DEBUG      a ADDRMAP           k DESCCHANGED   s STREAM
        i INFO       f AUTHDIR_NEWDESCS  g GUARD         r STREAM_BW
        n NOTICE     h BUILDTIMEOUT_SET  l NEWCONSENSUS  t STATUS_CLIENT
        w WARN       b BW                m NEWDESC       u STATUS_GENERAL
        e ERR        c CIRC              p NS            v STATUS_SERVER
                     j CLIENTS_SEEN      q ORCONN
          DINWE tor runlevel+            A All Events
          12345 arm runlevel+            X No Events
          67890 torctl runlevel+         U Unknown Events"""

RUNLEVELS = ["DEBUG", "INFO", "NOTICE", "WARN", "ERR"]
RUNLEVEL_EVENT_COLOR = {"DEBUG": "magenta", "INFO": "blue", "NOTICE": "green", "WARN": "yellow", "ERR": "red"}

DEFAULT_CONFIG = {"features.log.prepopulate": True, "features.log.prepopulateReadLimit": 5000, "features.log.maxRefreshRate": 300, "cache.logPanel.size": 1000, "log.logPanel.prepopulateSuccess": log.INFO, "log.logPanel.prepopulateFailed": log.WARN}

def expandEvents(eventAbbr):
  """
  Expands event abbreviations to their full names. Beside mappings provided in
  TOR_EVENT_TYPES this recognizes the following special events and aliases:
  U - UKNOWN events
  A - all events
  X - no events
  DINWE - runlevel and higher
  12345 - arm runlevel and higher (ARM_DEBUG - ARM_ERR)
  67890 - torctl runlevel and higher (TORCTL_DEBUG - TORCTL_ERR)
  Raises ValueError with invalid input if any part isn't recognized.
  
  Examples:
  "inUt" -> ["INFO", "NOTICE", "UNKNOWN", "STREAM_BW"]
  "N4" -> ["NOTICE", "WARN", "ERR", "ARM_WARN", "ARM_ERR"]
  "cfX" -> []
  
  Arguments:
    eventAbbr - flags to be parsed to event types
  """
  
  expandedEvents, invalidFlags = set(), ""
  
  for flag in eventAbbr:
    if flag == "A":
      armRunlevels = ["ARM_" + runlevel for runlevel in RUNLEVELS]
      torctlRunlevels = ["TORCTL_" + runlevel for runlevel in RUNLEVELS]
      expandedEvents = set(TOR_EVENT_TYPES.values() + armRunlevels + torctlRunlevels + ["UNKNOWN"])
      break
    elif flag == "X":
      expandedEvents = set()
      break
    elif flag in "DINWE1234567890":
      # all events for a runlevel and higher
      if flag in "DINWE": typePrefix = ""
      elif flag in "12345": typePrefix = "ARM_"
      elif flag in "67890": typePrefix = "TORCTL_"
      
      if flag in "D16": runlevelIndex = 0
      elif flag in "I27": runlevelIndex = 1
      elif flag in "N38": runlevelIndex = 2
      elif flag in "W49": runlevelIndex = 3
      elif flag in "E50": runlevelIndex = 4
      
      runlevelSet = [typePrefix + runlevel for runlevel in RUNLEVELS[runlevelIndex:]]
      expandedEvents = expandedEvents.union(set(runlevelSet))
    elif flag == "U":
      expandedEvents.add("UNKNOWN")
    elif flag in TOR_EVENT_TYPES:
      expandedEvents.add(TOR_EVENT_TYPES[flag])
    else:
      invalidFlags += flag
  
  if invalidFlags: raise ValueError(invalidFlags)
  else: return expandedEvents

def getLogFileEntries(runlevels, readLimit = None, addLimit = None):
  """
  Parses tor's log file for past events matching the given runlevels, providing
  a list of log entries (ordered newest to oldest). Limiting the number of read
  entries is suggested to avoid parsing everything from logs in the GB and TB
  range.
  
  Arguments:
    runlevels - event types (DEBUG - ERR) to be returned
    readLimit - max lines of the log file that'll be read (unlimited if None)
    addLimit  - maximum entries to provide back (unlimited if None)
  """
  
  startTime = time.time()
  if not runlevels: return []
  
  # checks tor's configuration for the log file's location (if any exists)
  loggingTypes, loggingLocation = None, None
  for loggingEntry in torTools.getConn().getOption("Log", [], True):
    # looks for an entry like: notice file /var/log/tor/notices.log
    entryComp = loggingEntry.split()
    
    if entryComp[1] == "file":
      loggingTypes, loggingLocation = entryComp[0], entryComp[2]
      break
  
  if not loggingLocation: return []
  
  # if the runlevels argument is a superset of the log file then we can
  # limit the read contents to the addLimit
  if addLimit and (not readLimit or readLimit > addLimit):
    if "-" in loggingTypes:
      divIndex = loggingTypes.find("-")
      sIndex = RUNLEVELS.index(loggingTypes[:divIndex])
      eIndex = RUNLEVELS.index(loggingTypes[divIndex+1:])
      logFileRunlevels = RUNLEVELS[sIndex:eIndex+1]
    else:
      sIndex = RUNLEVELS.index(loggingTypes)
      logFileRunlevels = RUNLEVELS[sIndex:]
    
    # checks if runlevels we're reporting are a superset of the file's contents
    isFileSubset = True
    for runlevelType in logFileRunlevels:
      if runlevelType not in runlevels:
        isFileSubset = False
        break
    
    if isFileSubset: readLimit = addLimit
  
  # tries opening the log file, cropping results to avoid choking on huge logs
  lines = []
  try:
    if readLimit:
      lines = sysTools.call("tail -n %i %s" % (readLimit, loggingLocation))
      if not lines: raise IOError()
    else:
      logFile = open(loggingLocation, "r")
      lines = logFile.readlines()
      logFile.close()
  except IOError:
    msg = "Unable to read tor's log file: %s" % loggingLocation
    log.log(DEFAULT_CONFIG["log.logPanel.prepopulateFailed"], msg)
  
  if not lines: return []
  
  loggedEvents = []
  currentUnixTime, currentLocalTime = time.time(), time.localtime()
  for i in range(len(lines) - 1, -1, -1):
    line = lines[i]
    
    # entries look like:
    # Jul 15 18:29:48.806 [notice] Parsing GEOIP file.
    lineComp = line.split()
    eventType = lineComp[3][1:-1].upper()
    
    if eventType in runlevels:
      # converts timestamp to unix time
      timestamp = " ".join(lineComp[:3])
      
      # strips the decimal seconds
      if "." in timestamp: timestamp = timestamp[:timestamp.find(".")]
      
      # overwrites missing time parameters with the local time (ignoring wday
      # and yday since they aren't used)
      eventTimeComp = list(time.strptime(timestamp, "%b %d %H:%M:%S"))
      eventTimeComp[0] = currentLocalTime.tm_year
      eventTimeComp[8] = currentLocalTime.tm_isdst
      eventTime = time.mktime(eventTimeComp) # converts local to unix time
      
      # The above is gonna be wrong if the logs are for the previous year. If
      # the event's in the future then correct for this.
      if eventTime > currentUnixTime + 60:
        eventTimeComp[0] -= 1
        eventTime = time.mktime(eventTimeComp)
      
      eventMsg = " ".join(lineComp[4:])
      loggedEvents.append(LogEntry(eventTime, eventType, eventMsg, RUNLEVEL_EVENT_COLOR[eventType]))
    
    if "opening log file" in line:
      break # this entry marks the start of this tor instance
  
  if addLimit: loggedEvents = loggedEvents[:addLimit]
  msg = "Read %i entries from tor's log file: %s (read limit: %i, runtime: %0.3f)" % (len(loggedEvents), loggingLocation, readLimit, time.time() - startTime)
  log.log(DEFAULT_CONFIG["log.logPanel.prepopulateSuccess"], msg)
  return loggedEvents

class LogEntry():
  """
  Individual log file entry, having the following attributes:
    timestamp - unix timestamp for when the event occurred
    eventType - event type that occurred ("INFO", "BW", "ARM_WARN", etc)
    msg       - message that was logged
    color     - color of the log entry
  """
  
  def __init__(self, timestamp, eventType, msg, color):
    self.timestamp = timestamp
    self.type = eventType
    self.msg = msg
    self.color = color
    self._displayMessage = None
  
  def getDisplayMessage(self):
    """
    Provides the entry's message for the log.
    """
    
    if not self._displayMessage:
      entryTime = time.localtime(self.timestamp)
      self._displayMessage = "%02i:%02i:%02i [%s] %s" % (entryTime[3], entryTime[4], entryTime[5], self.type, self.msg)
    
    return self._displayMessage

class TorEventObserver(TorCtl.PostEventListener):
  """
  Listens for all types of events provided by TorCtl, providing an LogEntry
  instance to the given callback function.
  """
  
  def __init__(self, callback):
    """
    Tor event listener with the purpose of translating events to nicely
    formatted calls of a callback function.
    
    Arguments:
      callback   - function accepting a LogEntry, called when an event of these
                   types occur
    """
    
    TorCtl.PostEventListener.__init__(self)
    self.callback = callback
  
  def circ_status_event(self, event):
    msg = "ID: %-3s STATUS: %-10s PATH: %s" % (event.circ_id, event.status, ", ".join(event.path))
    if event.purpose: msg += " PURPOSE: %s" % event.purpose
    if event.reason: msg += " REASON: %s" % event.reason
    if event.remote_reason: msg += " REMOTE_REASON: %s" % event.remote_reason
    self._notify(event, msg, "yellow")
  
  def buildtimeout_set_event(self, event):
    self._notify(event, "SET_TYPE: %s, TOTAL_TIMES: %s, TIMEOUT_MS: %s, XM: %s, ALPHA: %s, CUTOFF_QUANTILE: %s" % (event.set_type, event.total_times, event.timeout_ms, event.xm, event.alpha, event.cutoff_quantile))
  
  def stream_status_event(self, event):
    self._notify(event, "ID: %s STATUS: %s CIRC_ID: %s TARGET: %s:%s REASON: %s REMOTE_REASON: %s SOURCE: %s SOURCE_ADDR: %s PURPOSE: %s" % (event.strm_id, event.status, event.circ_id, event.target_host, event.target_port, event.reason, event.remote_reason, event.source, event.source_addr, event.purpose))
  
  def or_conn_status_event(self, event):
    msg = "STATUS: %-10s ENDPOINT: %-20s" % (event.status, event.endpoint)
    if event.age: msg += " AGE: %-3s" % event.age
    if event.read_bytes: msg += " READ: %-4i" % event.read_bytes
    if event.wrote_bytes: msg += " WRITTEN: %-4i" % event.wrote_bytes
    if event.reason: msg += " REASON: %-6s" % event.reason
    if event.ncircs: msg += " NCIRCS: %i" % event.ncircs
    self._notify(event, msg)
  
  def stream_bw_event(self, event):
    self._notify(event, "ID: %s READ: %s WRITTEN: %s" % (event.strm_id, event.bytes_read, event.bytes_written))
  
  def bandwidth_event(self, event):
    self._notify(event, "READ: %i, WRITTEN: %i" % (event.read, event.written), "cyan")
  
  def msg_event(self, event):
    self._notify(event, event.msg, RUNLEVEL_EVENT_COLOR[event.level])
  
  def new_desc_event(self, event):
    idlistStr = [str(item) for item in event.idlist]
    self._notify(event, ", ".join(idlistStr))
  
  def address_mapped_event(self, event):
    self._notify(event, "%s, %s -> %s" % (event.when, event.from_addr, event.to_addr))
  
  def ns_event(self, event):
    # NetworkStatus params: nickname, idhash, orhash, ip, orport (int),
    #     dirport (int), flags, idhex, bandwidth, updated (datetime)
    msg = ", ".join(["%s (%s)" % (ns.idhex, ns.nickname) for ns in event.nslist])
    self._notify(event, "Listed (%i): %s" % (len(event.nslist), msg), "blue")
  
  def new_consensus_event(self, event):
    msg = ", ".join(["%s (%s)" % (ns.idhex, ns.nickname) for ns in event.nslist])
    self._notify(event, "Listed (%i): %s" % (len(event.nslist), msg), "magenta")
  
  def unknown_event(self, event):
    msg = "(%s) %s" % (event.event_name, event.event_string)
    self.callback(LogEntry(event.arrived_at, "UNKNOWN", msg, "red"))
  
  def _notify(self, event, msg, color="white"):
    self.callback(LogEntry(event.arrived_at, event.event_name, msg, color))

class LogPanel(panel.Panel, threading.Thread):
  """
  Listens for and displays tor, arm, and torctl events. This can prepopulate
  from tor's log file if it exists.
  """
  
  def __init__(self, stdscr, loggedEvents, config=None):
    panel.Panel.__init__(self, stdscr, "log", 0)
    threading.Thread.__init__(self)
    
    self._config = dict(DEFAULT_CONFIG)
    
    if config:
      config.update(self._config)
      
      # ensures prepopulation and cache sizes are sane
      self._config["features.log.prepopulateReadLimit"] = max(self._config["features.log.prepopulateReadLimit"], 0)
      self._config["features.log.maxRefreshRate"] = max(self._config["features.log.maxRefreshRate"], 10)
      self._config["cache.logPanel.size"] = max(self._config["cache.logPanel.size"], 50)
    
    self.msgLog = []                    # log entries, sorted by the timestamp
    self.loggedEvents = loggedEvents    # events we're listening to
    self.regexFilter = None             # filter for presented log events (no filtering if None)
    self.scroll = 0
    self._isPaused = False
    self._pauseBuffer = []              # location where messages are buffered if paused
    
    self._isChanged = False             # if true, has new event(s) since last drawn if true
    self._lastUpdate = -1               # time the content was last revised
    self._halt = False                  # terminates thread if true
    self._cond = threading.Condition()  # used for pausing/resuming the thread
    
    # restricts concurrent write access to attributes used to draw the display:
    # msgLog, loggedEvents, regexFilter, scroll
    self.valsLock = threading.RLock()
    
    # cached parameters (invalidated if arguments for them change)
    # _getTitle (args: loggedEvents, regexFilter pattern, width)
    self._titleCache = None
    self._titleArgs = (None, None, None)
    
    # _getContentLength (args: msgLog, regexFilter pattern, height, width)
    self._contentLengthCache = None
    self._contentLengthArgs = (None, None, None, None)
    
    # fetches past tor events from log file, if available
    torEventBacklog = []
    if self._config["features.log.prepopulate"]:
      setRunlevels = list(set.intersection(set(self.loggedEvents), set(RUNLEVELS)))
      readLimit = self._config["features.log.prepopulateReadLimit"]
      addLimit = self._config["cache.logPanel.size"]
      torEventBacklog = getLogFileEntries(setRunlevels, readLimit, addLimit)
    
    # adds arm listener and fetches past events
    log.LOG_LOCK.acquire()
    try:
      armRunlevels = [log.DEBUG, log.INFO, log.NOTICE, log.WARN, log.ERR]
      log.addListeners(armRunlevels, self._registerArmEvent)
      
      # gets the set of arm events we're logging
      setRunlevels = []
      for i in range(len(armRunlevels)):
        if "ARM_" + RUNLEVELS[i] in self.loggedEvents:
          setRunlevels.append(armRunlevels[i])
      
      armEventBacklog = []
      for level, msg, eventTime in log._getEntries(setRunlevels):
        runlevelStr = log.RUNLEVEL_STR[level]
        armEventEntry = LogEntry(eventTime, "ARM_" + runlevelStr, msg, RUNLEVEL_EVENT_COLOR[runlevelStr])
        armEventBacklog.append(armEventEntry)
      
      # joins armEventBacklog and torEventBacklog chronologically into msgLog
      while armEventBacklog or torEventBacklog:
        if not armEventBacklog:
          self.msgLog.append(torEventBacklog.pop(0))
        elif not torEventBacklog:
          self.msgLog.append(armEventBacklog.pop(0))
        elif armEventBacklog[0].timestamp > torEventBacklog[0].timestamp:
          self.msgLog.append(torEventBacklog.pop(0))
        else:
          self.msgLog.append(armEventBacklog.pop(0))
    finally:
      log.LOG_LOCK.release()
    
    # adds listeners for tor and torctl events
    conn = torTools.getConn()
    conn.addEventListener(TorEventObserver(self.registerEvent))
    conn.addTorCtlListener(self._registerTorCtlEvent)
  
  def registerEvent(self, event):
    """
    Notes event and redraws log. If paused it's held in a temporary buffer.
    
    Arguments:
      event - LogEntry for the event that occurred
    """
    
    if not event.type in self.loggedEvents: return
    
    # strips control characters to avoid screwing up the terminal
    event.msg = "".join([char for char in event.msg if (isprint(char) or char == "\n")])
    
    cacheSize = self._config["cache.logPanel.size"]
    if self._isPaused:
      self._pauseBuffer.insert(0, event)
      if len(self._pauseBuffer) > cacheSize: del self._pauseBuffer[cacheSize:]
    else:
      self.valsLock.acquire()
      self.msgLog.insert(0, event)
      if len(self.msgLog) > cacheSize: del self.msgLog[cacheSize:]
      
      # notifies the display that it has new content
      if not self.regexFilter or self.regexFilter.search(event.getDisplayMessage()):
        self._isChanged = True
        self._cond.acquire()
        self._cond.notifyAll()
        self._cond.release()
      
      self.valsLock.release()
  
  def _registerArmEvent(self, level, msg, eventTime):
    eventColor = RUNLEVEL_EVENT_COLOR[level]
    self.registerEvent(LogEntry(eventTime, "ARM_%s" % level, msg, eventColor))
  
  def _registerTorCtlEvent(self, level, msg):
    eventColor = RUNLEVEL_EVENT_COLOR[level]
    self.registerEvent(LogEntry(time.time(), "TORCTL_%s" % level, msg, eventColor))
  
  def setLoggedEvents(self, eventTypes):
    """
    Sets the event types recognized by the panel.
    
    Arguments:
      eventTypes - event types to be logged
    """
    
    if eventTypes == self.loggedEvents: return
    
    self.valsLock.acquire()
    self.loggedEvents = eventTypes
    self.redraw(True)
    self.valsLock.release()
  
  def setFilter(self, logFilter):
    """
    Filters log entries according to the given regular expression.
    
    Arguments:
      logFilter - regular expression used to determine which messages are
                  shown, None if no filter should be applied
    """
    
    if logFilter == self.regexFilter: return
    
    self.valsLock.acquire()
    self.regexFilter = logFilter
    self.redraw(True)
    self.valsLock.release()
  
  def clear(self):
    """
    Clears the contents of the event log.
    """
    
    self.valsLock.acquire()
    self.msgLog = []
    self.redraw(True)
    self.valsLock.release()
  
  def handleKey(self, key):
    if uiTools.isScrollKey(key):
      pageHeight = self.getPreferredSize()[0] - 1
      contentHeight = self._getContentLength()
      newScroll = uiTools.getScrollPosition(key, self.scroll, pageHeight, contentHeight)
      
      if self.scroll != newScroll:
        self.valsLock.acquire()
        self.scroll = newScroll
        self.redraw(True)
        self.valsLock.release()
  
  def setPaused(self, isPause):
    """
    If true, prevents message log from being updated with new events.
    """
    
    if isPause == self._isPaused: return
    
    self._isPaused = isPause
    if self._isPaused: self._pauseBuffer = []
    else:
      self.valsLock.acquire()
      self.msgLog = (self._pauseBuffer + self.msgLog)[:self._config["cache.logPanel.size"]]
      self.redraw(True)
      self.valsLock.release()
  
  def draw(self, subwindow, width, height):
    """
    Redraws message log. Entries stretch to use available space and may
    contain up to two lines. Starts with newest entries.
    """
    
    self.valsLock.acquire()
    self._isChanged, self._lastUpdate = False, time.time()
    
    # draws the top label
    self.addstr(0, 0, self._getTitle(width), curses.A_STANDOUT)
    
    # restricts scroll location to valid bounds
    contentHeight = self._getContentLength()
    self.scroll = max(0, min(self.scroll, contentHeight - height + 1))
    
    # draws left-hand scroll bar if content's longer than the height
    xOffset = 0 # offset for scroll bar
    if contentHeight > height - 1:
      xOffset = 3
      self.addScrollBar(self.scroll, self.scroll + height - 1, contentHeight, 1)
    
    # draws log entries
    lineCount = 1 - self.scroll
    for entry in self.msgLog:
      if self.regexFilter and not self.regexFilter.search(entry.getDisplayMessage()):
        continue  # filter doesn't match log message - skip
      
      for line in entry.getDisplayMessage().split("\n"):
        # splits over too lines if too long
        if len(line) < width:
          if lineCount >= 1: self.addstr(lineCount, xOffset, line, uiTools.getColor(entry.color))
          lineCount += 1
        else:
          (line1, line2) = uiTools.splitLine(line, width - xOffset)
          if lineCount >= 1: self.addstr(lineCount, xOffset, line1, uiTools.getColor(entry.color))
          if lineCount >= 0: self.addstr(lineCount + 1, xOffset, line2, uiTools.getColor(entry.color))
          lineCount += 2
      
      if lineCount >= height: break # further log messages wouldn't fit
    
    self.valsLock.release()
  
  def redraw(self, forceRedraw=False, block=False):
    # determines if the content needs to be redrawn or not
    panel.Panel.redraw(self, forceRedraw, block)
  
  def run(self):
    """
    Redraws the display, coalescing updates if events are rapidly logged (for
    instance running at the DEBUG runlevel) while also being immediately
    responsive if additions are less frequent.
    """
    
    while not self._halt:
      timeSinceReset = time.time() - self._lastUpdate
      maxLogUpdateRate = self._config["features.log.maxRefreshRate"] / 1000.0
      
      sleepTime = 0
      if not self._isChanged or self._isPaused:
        sleepTime = 10
      elif timeSinceReset < maxLogUpdateRate:
        sleepTime = max(0.05, maxLogUpdateRate - timeSinceReset)
      
      if sleepTime:
        self._cond.acquire()
        if not self._halt: self._cond.wait(sleepTime)
        self._cond.release()
      else:
        self.redraw(True)
  
  def stop(self):
    """
    Halts further resolutions and terminates the thread.
    """
    
    self._cond.acquire()
    self._halt = True
    self._cond.notifyAll()
    self._cond.release()
  
  def _getTitle(self, width):
    """
    Provides the label used for the panel, looking like:
      Events (ARM NOTICE - ERR, BW - filter: prepopulate):
    
    This truncates the attributes (with an ellipse) if too long, and condenses
    runlevel ranges if there's three or more in a row (for instance ARM_INFO,
    ARM_NOTICE, and ARM_WARN becomes "ARM_INFO - WARN").
    
    Arguments:
      width - width constraint the label needs to fix in
    """
    
    # usually the attributes used to make the label are decently static, so
    # provide cached results if they're unchanged
    self.valsLock.acquire()
    currentPattern = self.regexFilter.pattern if self.regexFilter else None
    isUnchanged = self._titleArgs[0] == self.loggedEvents
    isUnchanged &= self._titleArgs[1] == currentPattern
    isUnchanged &= self._titleArgs[2] == width
    if isUnchanged:
      self.valsLock.release()
      return self._titleCache
    
    eventsList = list(self.loggedEvents)
    if not eventsList:
      if not currentPattern:
        panelLabel = "Events:"
      else:
        labelPattern = uiTools.cropStr(currentPattern, width - 18)
        panelLabel = "Events (filter: %s):" % labelPattern
    else:
      # does the following with all runlevel types (tor, arm, and torctl):
      # - pulls to the start of the list
      # - condenses range if there's three or more in a row (ex. "ARM_INFO - WARN")
      tmpRunlevels = [] # runlevels pulled from the list (just the runlevel part)
      for prefix in ("TORCTL_", "ARM_", ""):
        # blank ending runlevel forces the break condition to be reached at the end
        for runlevel in RUNLEVELS + [""]:
          eventType = prefix + runlevel
          if eventType in eventsList:
            # runlevel event found, move to the tmp list
            eventsList.remove(eventType)
            tmpRunlevels.append(runlevel)
          elif tmpRunlevels:
            # adds all tmp list entries to the start of eventsList
            if len(tmpRunlevels) >= 3:
              # condense sequential runlevels
              startLevel, endLevel = tmpRunlevels[0], tmpRunlevels[-1]
              eventsList.insert(0, "%s%s - %s" % (prefix, startLevel, endLevel))
            else:
              # adds runlevels individaully
              tmpRunlevels.reverse()
              for tmpRunlevel in tmpRunlevels:
                eventsList.insert(0, prefix + tmpRunlevel)
            
            tmpRunlevels = []
      
      # truncates to use an ellipsis if too long, for instance:
      attrLabel = ", ".join(eventsList)
      if currentPattern: attrLabel += " - filter: %s" % currentPattern
      attrLabel = uiTools.cropStr(attrLabel, width - 10, -1)
      panelLabel = "Events (%s):" % attrLabel
    
    # cache results and return
    self._titleCache = panelLabel
    self._titleArgs = (list(self.loggedEvents), currentPattern, width)
    self.valsLock.release()
    return panelLabel
  
  def _getContentLength(self):
    """
    Provides the number of lines the log's contents would currently occupy,
    taking into account filtered/wrapped lines, the scroll bar, etc.
    """
    
    # if the arguments haven't changed then we can use cached results
    self.valsLock.acquire()
    height, width = self.getPreferredSize()
    currentPattern = self.regexFilter.pattern if self.regexFilter else None
    isUnchanged = self._contentLengthArgs[0] == self.msgLog
    isUnchanged &= self._contentLengthArgs[1] == currentPattern
    isUnchanged &= self._contentLengthArgs[2] == height
    isUnchanged &= self._contentLengthArgs[3] == width
    if isUnchanged:
      self.valsLock.release()
      return self._contentLengthCache
    
    contentLengths = [0, 0] # length of the content without and with a scroll bar
    for entry in self.msgLog:
      if not self.regexFilter or self.regexFilter.search(entry.getDisplayMessage()):
        for line in entry.getDisplayMessage().split("\n"):
          if len(line) >= width: contentLengths[0] += 2
          else: contentLengths[0] += 1
          
          if len(line) >= width - 3: contentLengths[1] += 2
          else: contentLengths[1] += 1
    
    # checks if the scroll bar would be displayed to determine the actual length
    actualLength = contentLengths[0] if contentLengths[0] <= height - 1 else contentLengths[1]
    
    self._contentLengthCache = actualLength
    self._contentLengthArgs = (list(self.msgLog), currentPattern, height, width)
    self.valsLock.release()
    return actualLength

