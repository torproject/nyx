"""
Panel providing a chronological log of events its been configured to listen
for. This provides prepopulation from the log file and supports filtering by
regular expressions.
"""

import re
import os
import time
import curses
import threading

from TorCtl import TorCtl

import popups
from version import VERSION
from util import conf, log, panel, sysTools, torTools, uiTools

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

RUNLEVEL_EVENT_COLOR = {log.DEBUG: "magenta", log.INFO: "blue", log.NOTICE: "green",
                        log.WARN: "yellow", log.ERR: "red"}
DAYBREAK_EVENT = "DAYBREAK" # special event for marking when the date changes
TIMEZONE_OFFSET = time.altzone if time.localtime()[8] else time.timezone

ENTRY_INDENT = 2 # spaces an entry's message is indented after the first line
DEFAULT_CONFIG = {"features.logFile": "",
                  "features.log.showDateDividers": True,
                  "features.log.showDuplicateEntries": False,
                  "features.log.entryDuration": 7,
                  "features.log.maxLinesPerEntry": 6,
                  "features.log.prepopulate": True,
                  "features.log.prepopulateReadLimit": 5000,
                  "features.log.maxRefreshRate": 300,
                  "features.log.regex": [],
                  "cache.logPanel.size": 1000,
                  "log.logPanel.prepopulateSuccess": log.INFO,
                  "log.logPanel.prepopulateFailed": log.WARN,
                  "log.logPanel.logFileOpened": log.NOTICE,
                  "log.logPanel.logFileWriteFailed": log.ERR,
                  "log.logPanel.forceDoubleRedraw": log.DEBUG,
                  "log.configEntryTypeError": log.NOTICE}

DUPLICATE_MSG = " [%i duplicate%s hidden]"

# The height of the drawn content is estimated based on the last time we redrew
# the panel. It's chiefly used for scrolling and the bar indicating its
# position. Letting the estimate be too inaccurate results in a display bug, so
# redraws the display if it's off by this threshold.
CONTENT_HEIGHT_REDRAW_THRESHOLD = 3

# static starting portion of common log entries, fetched from the config when
# needed if None
COMMON_LOG_MESSAGES = None

# cached values and the arguments that generated it for the getDaybreaks and
# getDuplicates functions
CACHED_DAYBREAKS_ARGUMENTS = (None, None) # events, current day
CACHED_DAYBREAKS_RESULT = None
CACHED_DUPLICATES_ARGUMENTS = None # events
CACHED_DUPLICATES_RESULT = None

# duration we'll wait for the deduplication function before giving up (in ms)
DEDUPLICATION_TIMEOUT = 100

# maximum number of regex filters we'll remember
MAX_REGEX_FILTERS = 5

def daysSince(timestamp=None):
  """
  Provides the number of days since the epoch converted to local time (rounded
  down).
  
  Arguments:
    timestamp - unix timestamp to convert, current time if undefined
  """
  
  if timestamp == None: timestamp = time.time()
  return int((timestamp - TIMEZONE_OFFSET) / 86400)

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
      armRunlevels = ["ARM_" + runlevel for runlevel in log.Runlevel.values()]
      torctlRunlevels = ["TORCTL_" + runlevel for runlevel in log.Runlevel.values()]
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
      
      runlevelSet = [typePrefix + runlevel for runlevel in log.Runlevel.values()[runlevelIndex:]]
      expandedEvents = expandedEvents.union(set(runlevelSet))
    elif flag == "U":
      expandedEvents.add("UNKNOWN")
    elif flag in TOR_EVENT_TYPES:
      expandedEvents.add(TOR_EVENT_TYPES[flag])
    else:
      invalidFlags += flag
  
  if invalidFlags: raise ValueError(invalidFlags)
  else: return expandedEvents

def getMissingEventTypes():
  """
  Provides the event types the current torctl connection supports but arm
  doesn't. This provides an empty list if no event types are missing, and None
  if the GETINFO query fails.
  """
  
  torEventTypes = torTools.getConn().getInfo("events/names")
  
  if torEventTypes:
    torEventTypes = torEventTypes.split(" ")
    armEventTypes = TOR_EVENT_TYPES.values()
    return [event for event in torEventTypes if not event in armEventTypes]
  else: return None # GETINFO call failed

def setEventListening(events):
  """
  Configures the events Tor listens for, filtering non-tor events from what we
  request from the controller. This returns a sorted list of the events we
  successfully set.
  
  Arguments:
    events - event types to attempt to set
  """
  
  events = set(events) # drops duplicates
  torEvents = events.intersection(set(TOR_EVENT_TYPES.values()))
  
  # adds events unrecognized by arm if we're listening to the 'UNKNOWN' type
  if "UNKNOWN" in events:
    torEvents.update(set(getMissingEventTypes()))
  
  setEvents = torTools.getConn().setControllerEvents(list(torEvents))
  
  # provides back the input set minus events we failed to set
  return sorted(events.difference(torTools.FAILED_EVENTS))

def loadLogMessages():
  """
  Fetches a mapping of common log messages to their runlevels from the config.
  """
  
  global COMMON_LOG_MESSAGES
  armConf = conf.getConfig("arm")
  
  COMMON_LOG_MESSAGES = {}
  for confKey in armConf.getKeys():
    if confKey.startswith("msg."):
      eventType = confKey[4:].upper()
      messages = armConf.get(confKey, [])
      COMMON_LOG_MESSAGES[eventType] = messages

def getLogFileEntries(runlevels, readLimit = None, addLimit = None, config = None):
  """
  Parses tor's log file for past events matching the given runlevels, providing
  a list of log entries (ordered newest to oldest). Limiting the number of read
  entries is suggested to avoid parsing everything from logs in the GB and TB
  range.
  
  Arguments:
    runlevels - event types (DEBUG - ERR) to be returned
    readLimit - max lines of the log file that'll be read (unlimited if None)
    addLimit  - maximum entries to provide back (unlimited if None)
    config    - configuration parameters related to this panel, uses defaults
                if left as None
  """
  
  startTime = time.time()
  if not runlevels: return []
  
  if not config: config = DEFAULT_CONFIG
  
  # checks tor's configuration for the log file's location (if any exists)
  loggingTypes, loggingLocation = None, None
  for loggingEntry in torTools.getConn().getOption("Log", [], True):
    # looks for an entry like: notice file /var/log/tor/notices.log
    entryComp = loggingEntry.split()
    
    if entryComp[1] == "file":
      loggingTypes, loggingLocation = entryComp[0], entryComp[2]
      break
  
  if not loggingLocation: return []
  
  # includes the prefix for tor paths
  loggingLocation = torTools.getConn().getPathPrefix() + loggingLocation
  
  # if the runlevels argument is a superset of the log file then we can
  # limit the read contents to the addLimit
  runlevels = log.Runlevel.values()
  loggingTypes = loggingTypes.upper()
  if addLimit and (not readLimit or readLimit > addLimit):
    if "-" in loggingTypes:
      divIndex = loggingTypes.find("-")
      sIndex = runlevels.index(loggingTypes[:divIndex])
      eIndex = runlevels.index(loggingTypes[divIndex+1:])
      logFileRunlevels = runlevels[sIndex:eIndex+1]
    else:
      sIndex = runlevels.index(loggingTypes)
      logFileRunlevels = runlevels[sIndex:]
    
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
    log.log(config["log.logPanel.prepopulateFailed"], msg)
  
  if not lines: return []
  
  loggedEvents = []
  currentUnixTime, currentLocalTime = time.time(), time.localtime()
  for i in range(len(lines) - 1, -1, -1):
    line = lines[i]
    
    # entries look like:
    # Jul 15 18:29:48.806 [notice] Parsing GEOIP file.
    lineComp = line.split()
    
    # Checks that we have all the components we expect. This could happen if
    # we're either not parsing a tor log or in weird edge cases (like being
    # out of disk space)
    
    if len(lineComp) < 4: continue
    
    eventType = lineComp[3][1:-1].upper()
    
    if eventType in runlevels:
      # converts timestamp to unix time
      timestamp = " ".join(lineComp[:3])
      
      # strips the decimal seconds
      if "." in timestamp: timestamp = timestamp[:timestamp.find(".")]
      
      # Ignoring wday and yday since they aren't used.
      #
      # Pretend the year is 2012, because 2012 is a leap year, and parsing a
      # date with strptime fails if Feb 29th is passed without a year that's
      # actually a leap year. We can't just use the current year, because we
      # might be parsing old logs which didn't get rotated.
      #
      # https://trac.torproject.org/projects/tor/ticket/5265
      
      timestamp = "2012 " + timestamp
      eventTimeComp = list(time.strptime(timestamp, "%Y %b %d %H:%M:%S"))
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
  log.log(config["log.logPanel.prepopulateSuccess"], msg)
  return loggedEvents

def getDaybreaks(events, ignoreTimeForCache = False):
  """
  Provides the input events back with special 'DAYBREAK_EVENT' markers inserted
  whenever the date changed between log entries (or since the most recent
  event). The timestamp matches the beginning of the day for the following
  entry.
  
  Arguments:
    events             - chronologically ordered listing of events
    ignoreTimeForCache - skips taking the day into consideration for providing
                         cached results if true
  """
  
  global CACHED_DAYBREAKS_ARGUMENTS, CACHED_DAYBREAKS_RESULT
  if not events: return []
  
  newListing = []
  currentDay = daysSince()
  lastDay = currentDay
  
  if CACHED_DAYBREAKS_ARGUMENTS[0] == events and \
    (ignoreTimeForCache or CACHED_DAYBREAKS_ARGUMENTS[1] == currentDay):
    return list(CACHED_DAYBREAKS_RESULT)
  
  for entry in events:
    eventDay = daysSince(entry.timestamp)
    if eventDay != lastDay:
      markerTimestamp = (eventDay * 86400) + TIMEZONE_OFFSET
      newListing.append(LogEntry(markerTimestamp, DAYBREAK_EVENT, "", "white"))
    
    newListing.append(entry)
    lastDay = eventDay
  
  CACHED_DAYBREAKS_ARGUMENTS = (list(events), currentDay)
  CACHED_DAYBREAKS_RESULT = list(newListing)
  
  return newListing

def getDuplicates(events):
  """
  Deduplicates a list of log entries, providing back a tuple listing with the
  log entry and count of duplicates following it. Entries in different days are
  not considered to be duplicates. This times out, returning None if it takes
  longer than DEDUPLICATION_TIMEOUT.
  
  Arguments:
    events - chronologically ordered listing of events
  """
  
  global CACHED_DUPLICATES_ARGUMENTS, CACHED_DUPLICATES_RESULT
  if CACHED_DUPLICATES_ARGUMENTS == events:
    return list(CACHED_DUPLICATES_RESULT)
  
  # loads common log entries from the config if they haven't been
  if COMMON_LOG_MESSAGES == None: loadLogMessages()
  
  startTime = time.time()
  eventsRemaining = list(events)
  returnEvents = []
  
  while eventsRemaining:
    entry = eventsRemaining.pop(0)
    duplicateIndices = isDuplicate(entry, eventsRemaining, True)
    
    # checks if the call timeout has been reached
    if (time.time() - startTime) > DEDUPLICATION_TIMEOUT / 1000.0:
      return None
    
    # drops duplicate entries
    duplicateIndices.reverse()
    for i in duplicateIndices: del eventsRemaining[i]
    
    returnEvents.append((entry, len(duplicateIndices)))
  
  CACHED_DUPLICATES_ARGUMENTS = list(events)
  CACHED_DUPLICATES_RESULT = list(returnEvents)
  
  return returnEvents

def isDuplicate(event, eventSet, getDuplicates = False):
  """
  True if the event is a duplicate for something in the eventSet, false
  otherwise. If the getDuplicates flag is set this provides the indices of
  the duplicates instead.
  
  Arguments:
    event         - event to search for duplicates of
    eventSet      - set to look for the event in
    getDuplicates - instead of providing back a boolean this gives a list of
                    the duplicate indices in the eventSet
  """
  
  duplicateIndices = []
  for i in range(len(eventSet)):
    forwardEntry = eventSet[i]
    
    # if showing dates then do duplicate detection for each day, rather
    # than globally
    if forwardEntry.type == DAYBREAK_EVENT: break
    
    if event.type == forwardEntry.type:
      isDuplicate = False
      if event.msg == forwardEntry.msg: isDuplicate = True
      elif event.type in COMMON_LOG_MESSAGES:
        for commonMsg in COMMON_LOG_MESSAGES[event.type]:
          # if it starts with an asterisk then check the whole message rather
          # than just the start
          if commonMsg[0] == "*":
            isDuplicate = commonMsg[1:] in event.msg and commonMsg[1:] in forwardEntry.msg
          else:
            isDuplicate = event.msg.startswith(commonMsg) and forwardEntry.msg.startswith(commonMsg)
          
          if isDuplicate: break
      
      if isDuplicate:
        if getDuplicates: duplicateIndices.append(i)
        else: return True
  
  if getDuplicates: return duplicateIndices
  else: return False

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
  
  def getDisplayMessage(self, includeDate = False):
    """
    Provides the entry's message for the log.
    
    Arguments:
      includeDate - appends the event's date to the start of the message
    """
    
    if includeDate:
      # not the common case so skip caching
      entryTime = time.localtime(self.timestamp)
      timeLabel =  "%i/%i/%i %02i:%02i:%02i" % (entryTime[1], entryTime[2], entryTime[0], entryTime[3], entryTime[4], entryTime[5])
      return "%s [%s] %s" % (timeLabel, self.type, self.msg)
    
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
      callback - function accepting a LogEntry, called when an event of these
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
    whenLabel, gmtExpiryLabel = "", ""
    
    if event.when:
      whenLabel = time.strftime("%H:%M %m/%d/%Y", event.when)
    
    # TODO: torctl is getting an 'error' and 'gmt_expiry' attribute so display
    # those when they become available
    #
    #if event.gmt_expiry:
    #  gmtExpiryLabel = time.strftime("%H:%M %m/%d/%Y", event.gmt_expiry)
    
    self._notify(event, "%s, %s -> %s" % (whenLabel, event.from_addr, event.to_addr))
  
  def ns_event(self, event):
    # NetworkStatus params: nickname, idhash, orhash, ip, orport (int),
    #     dirport (int), flags, idhex, bandwidth, updated (datetime)
    msg = ", ".join(["%s (%s)" % (ns.idhex, ns.nickname) for ns in event.nslist])
    self._notify(event, "Listed (%i): %s" % (len(event.nslist), msg), "blue")
  
  def new_consensus_event(self, event):
    msg = ", ".join(["%s (%s)" % (ns.idhex, ns.nickname) for ns in event.nslist])
    self._notify(event, "Listed (%i): %s" % (len(event.nslist), msg), "magenta")
  
  def guard_event(self, event):
    msg = "%s (%s), STATUS: %s" % (event.idhex, event.nick, event.status)
    self._notify(event, msg, "yellow")
  
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
    self.setDaemon(True)
    
    # Make sure that the msg.* messages are loaded. Lazy loading it later is
    # fine, but this way we're sure it happens before warning about unused
    # config options.
    loadLogMessages()
    
    # regex filters the user has defined
    self.filterOptions = []
    
    self._config = dict(DEFAULT_CONFIG)
    
    if config:
      config.update(self._config, {
        "features.log.maxLinesPerEntry": 1,
        "features.log.prepopulateReadLimit": 0,
        "features.log.maxRefreshRate": 10,
        "cache.logPanel.size": 1000})
      
      for filter in self._config["features.log.regex"]:
        # checks if we can't have more filters
        if len(self.filterOptions) >= MAX_REGEX_FILTERS: break
        
        try:
          re.compile(filter)
          self.filterOptions.append(filter)
        except re.error, exc:
          msg = "Invalid regular expression pattern (%s): %s" % (exc, filter)
          log.log(self._config["log.configEntryTypeError"], msg)
    
    # collapses duplicate log entries if false, showing only the most recent
    self.showDuplicates = self._config["features.log.showDuplicateEntries"]
    
    # restricts the input to the set of events we can listen to, and
    # configures the controller to liten to them
    loggedEvents = setEventListening(loggedEvents)
    
    self.setPauseAttr("msgLog")         # tracks the message log when we're paused
    self.msgLog = []                    # log entries, sorted by the timestamp
    self.loggedEvents = loggedEvents    # events we're listening to
    self.regexFilter = None             # filter for presented log events (no filtering if None)
    self.lastContentHeight = 0          # height of the rendered content when last drawn
    self.logFile = None                 # file log messages are saved to (skipped if None)
    self.scroll = 0
    
    self._lastUpdate = -1               # time the content was last revised
    self._halt = False                  # terminates thread if true
    self._cond = threading.Condition()  # used for pausing/resuming the thread
    
    # restricts concurrent write access to attributes used to draw the display
    # and pausing:
    # msgLog, loggedEvents, regexFilter, scroll
    self.valsLock = threading.RLock()
    
    # cached parameters (invalidated if arguments for them change)
    # last set of events we've drawn with
    self._lastLoggedEvents = []
    
    # _getTitle (args: loggedEvents, regexFilter pattern, width)
    self._titleCache = None
    self._titleArgs = (None, None, None)
    
    # adds arm listener and prepopulates log with past tor/arm events
    log.LOG_LOCK.acquire()
    try:
      log.addListeners(log.Runlevel.values(), self._registerArmEvent)
      self.reprepopulateEvents()
    finally:
      log.LOG_LOCK.release()
    
    # leaving lastContentHeight as being too low causes initialization problems
    self.lastContentHeight = len(self.msgLog)
    
    # adds listeners for tor and torctl events
    conn = torTools.getConn()
    conn.addEventListener(TorEventObserver(self.registerEvent))
    conn.addTorCtlListener(self._registerTorCtlEvent)
    conn.addStatusListener(self._resetListener)
    
    # opens log file if we'll be saving entries
    if self._config["features.logFile"]:
      logPath = self._config["features.logFile"]
      
      try:
        # make dir if the path doesn't already exist
        baseDir = os.path.dirname(logPath)
        if not os.path.exists(baseDir): os.makedirs(baseDir)
        
        self.logFile = open(logPath, "a")
        log.log(self._config["log.logPanel.logFileOpened"], "arm %s opening log file (%s)" % (VERSION, logPath))
      except (IOError, OSError), exc:
        log.log(self._config["log.logPanel.logFileWriteFailed"], "Unable to write to log file: %s" % sysTools.getFileErrorMsg(exc))
        self.logFile = None
  
  def reprepopulateEvents(self):
    """
    Clears the event log and repopulates it from the arm and tor backlogs.
    """
    
    self.valsLock.acquire()
    
    # clears the event log
    self.msgLog = []
    
    # fetches past tor events from log file, if available
    torEventBacklog = []
    if self._config["features.log.prepopulate"]:
      setRunlevels = list(set.intersection(set(self.loggedEvents), set(log.Runlevel.values())))
      readLimit = self._config["features.log.prepopulateReadLimit"]
      addLimit = self._config["cache.logPanel.size"]
      torEventBacklog = getLogFileEntries(setRunlevels, readLimit, addLimit, self._config)
    
    # gets the set of arm events we're logging
    setRunlevels = []
    armRunlevels = log.Runlevel.values()
    for i in range(len(armRunlevels)):
      if "ARM_" + log.Runlevel.values()[i] in self.loggedEvents:
        setRunlevels.append(armRunlevels[i])
    
    armEventBacklog = []
    for level, msg, eventTime in log._getEntries(setRunlevels):
      armEventEntry = LogEntry(eventTime, "ARM_" + level, msg, RUNLEVEL_EVENT_COLOR[level])
      armEventBacklog.insert(0, armEventEntry)
    
    # joins armEventBacklog and torEventBacklog chronologically into msgLog
    while armEventBacklog or torEventBacklog:
      if not armEventBacklog:
        self.msgLog.append(torEventBacklog.pop(0))
      elif not torEventBacklog:
        self.msgLog.append(armEventBacklog.pop(0))
      elif armEventBacklog[0].timestamp < torEventBacklog[0].timestamp:
        self.msgLog.append(torEventBacklog.pop(0))
      else:
        self.msgLog.append(armEventBacklog.pop(0))
    
    # crops events that are either too old, or more numerous than the caching size
    self._trimEvents(self.msgLog)
    
    self.valsLock.release()
  
  def setDuplicateVisability(self, isVisible):
    """
    Sets if duplicate log entries are collaped or expanded.
    
    Arguments:
      isVisible - if true all log entries are shown, otherwise they're
                  deduplicated
    """
    
    self.showDuplicates = isVisible
  
  def registerEvent(self, event):
    """
    Notes event and redraws log. If paused it's held in a temporary buffer.
    
    Arguments:
      event - LogEntry for the event that occurred
    """
    
    if not event.type in self.loggedEvents: return
    
    # strips control characters to avoid screwing up the terminal
    event.msg = uiTools.getPrintable(event.msg)
    
    # note event in the log file if we're saving them
    if self.logFile:
      try:
        self.logFile.write(event.getDisplayMessage(True) + "\n")
        self.logFile.flush()
      except IOError, exc:
        log.log(self._config["log.logPanel.logFileWriteFailed"], "Unable to write to log file: %s" % sysTools.getFileErrorMsg(exc))
        self.logFile = None
    
    self.valsLock.acquire()
    self.msgLog.insert(0, event)
    self._trimEvents(self.msgLog)
    
    # notifies the display that it has new content
    if not self.regexFilter or self.regexFilter.search(event.getDisplayMessage()):
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
    
    # configures the controller to listen for these tor events, and provides
    # back a subset without anything we're failing to listen to
    setTypes = setEventListening(eventTypes)
    self.loggedEvents = setTypes
    self.redraw(True)
    self.valsLock.release()
  
  def getFilter(self):
    """
    Provides our currently selected regex filter.
    """
    
    return self.filterOptions[0] if self.regexFilter else None
  
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
  
  def makeFilterSelection(self, selectedOption):
    """
    Makes the given filter selection, applying it to the log and reorganizing
    our filter selection.
    
    Arguments:
      selectedOption - regex filter we've already added, None if no filter
                       should be applied
    """
    
    if selectedOption:
      try:
        self.setFilter(re.compile(selectedOption))
        
        # move selection to top
        self.filterOptions.remove(selectedOption)
        self.filterOptions.insert(0, selectedOption)
      except re.error, exc:
        # shouldn't happen since we've already checked validity
        msg = "Invalid regular expression ('%s': %s) - removing from listing" % (selectedOption, exc)
        log.log(log.WARN, msg)
        self.filterOptions.remove(selectedOption)
    else: self.setFilter(None)
  
  def showFilterPrompt(self):
    """
    Prompts the user to add a new regex filter.
    """
    
    regexInput = popups.inputPrompt("Regular expression: ")
    
    if regexInput:
      try:
        self.setFilter(re.compile(regexInput))
        if regexInput in self.filterOptions: self.filterOptions.remove(regexInput)
        self.filterOptions.insert(0, regexInput)
      except re.error, exc:
        popups.showMsg("Unable to compile expression: %s" % exc, 2)
  
  def showEventSelectionPrompt(self):
    """
    Prompts the user to select the events being listened for.
    """
    
    # allow user to enter new types of events to log - unchanged if left blank
    popup, width, height = popups.init(11, 80)
    
    if popup:
      try:
        # displays the available flags
        popup.win.box()
        popup.addstr(0, 0, "Event Types:", curses.A_STANDOUT)
        eventLines = EVENT_LISTING.split("\n")
        
        for i in range(len(eventLines)):
          popup.addstr(i + 1, 1, eventLines[i][6:])
        
        popup.win.refresh()
        
        userInput = popups.inputPrompt("Events to log: ")
        if userInput:
          userInput = userInput.replace(' ', '') # strips spaces
          try: self.setLoggedEvents(expandEvents(userInput))
          except ValueError, exc:
            popups.showMsg("Invalid flags: %s" % str(exc), 2)
      finally: popups.finalize()
  
  def showSnapshotPrompt(self):
    """
    Lets user enter a path to take a snapshot, canceling if left blank.
    """
    
    pathInput = popups.inputPrompt("Path to save log snapshot: ")
    
    if pathInput:
      try:
        self.saveSnapshot(pathInput)
        popups.showMsg("Saved: %s" % pathInput, 2)
      except IOError, exc:
        popups.showMsg("Unable to save snapshot: %s" % sysTools.getFileErrorMsg(exc), 2)
  
  def clear(self):
    """
    Clears the contents of the event log.
    """
    
    self.valsLock.acquire()
    self.msgLog = []
    self.redraw(True)
    self.valsLock.release()
  
  def saveSnapshot(self, path):
    """
    Saves the log events currently being displayed to the given path. This
    takes filers into account. This overwrites the file if it already exists,
    and raises an IOError if there's a problem.
    
    Arguments:
      path - path where to save the log snapshot
    """
    
    path = os.path.abspath(path)
    
    # make dir if the path doesn't already exist
    baseDir = os.path.dirname(path)
    
    try:
      if not os.path.exists(baseDir): os.makedirs(baseDir)
    except OSError, exc:
      raise IOError("unable to make directory '%s'" % baseDir)
    
    snapshotFile = open(path, "w")
    self.valsLock.acquire()
    try:
      for entry in self.msgLog:
        isVisible = not self.regexFilter or self.regexFilter.search(entry.getDisplayMessage())
        if isVisible: snapshotFile.write(entry.getDisplayMessage(True) + "\n")
      
      self.valsLock.release()
    except Exception, exc:
      self.valsLock.release()
      raise exc
  
  def handleKey(self, key):
    isKeystrokeConsumed = True
    if uiTools.isScrollKey(key):
      pageHeight = self.getPreferredSize()[0] - 1
      newScroll = uiTools.getScrollPosition(key, self.scroll, pageHeight, self.lastContentHeight)
      
      if self.scroll != newScroll:
        self.valsLock.acquire()
        self.scroll = newScroll
        self.redraw(True)
        self.valsLock.release()
    elif key in (ord('u'), ord('U')):
      self.valsLock.acquire()
      self.showDuplicates = not self.showDuplicates
      self.redraw(True)
      self.valsLock.release()
    elif key == ord('c') or key == ord('C'):
      msg = "This will clear the log. Are you sure (c again to confirm)?"
      keyPress = popups.showMsg(msg, attr = curses.A_BOLD)
      if keyPress in (ord('c'), ord('C')): self.clear()
    elif key == ord('f') or key == ord('F'):
      # Provides menu to pick regular expression filters or adding new ones:
      # for syntax see: http://docs.python.org/library/re.html#regular-expression-syntax
      options = ["None"] + self.filterOptions + ["New..."]
      oldSelection = 0 if not self.regexFilter else 1
      
      # does all activity under a curses lock to prevent redraws when adding
      # new filters
      panel.CURSES_LOCK.acquire()
      try:
        selection = popups.showMenu("Log Filter:", options, oldSelection)
        
        # applies new setting
        if selection == 0:
          self.setFilter(None)
        elif selection == len(options) - 1:
          # selected 'New...' option - prompt user to input regular expression
          self.showFilterPrompt()
        elif selection != -1:
          self.makeFilterSelection(self.filterOptions[selection - 1])
      finally:
        panel.CURSES_LOCK.release()
      
      if len(self.filterOptions) > MAX_REGEX_FILTERS: del self.filterOptions[MAX_REGEX_FILTERS:]
    elif key == ord('e') or key == ord('E'):
      self.showEventSelectionPrompt()
    elif key == ord('a') or key == ord('A'):
      self.showSnapshotPrompt()
    else: isKeystrokeConsumed = False
    
    return isKeystrokeConsumed
  
  def getHelp(self):
    options = []
    options.append(("up arrow", "scroll log up a line", None))
    options.append(("down arrow", "scroll log down a line", None))
    options.append(("a", "save snapshot of the log", None))
    options.append(("e", "change logged events", None))
    options.append(("f", "log regex filter", "enabled" if self.regexFilter else "disabled"))
    options.append(("u", "duplicate log entries", "visible" if self.showDuplicates else "hidden"))
    options.append(("c", "clear event log", None))
    return options
  
  def draw(self, width, height):
    """
    Redraws message log. Entries stretch to use available space and may
    contain up to two lines. Starts with newest entries.
    """
    
    currentLog = self.getAttr("msgLog")
    
    self.valsLock.acquire()
    self._lastLoggedEvents, self._lastUpdate = list(currentLog), time.time()
    
    # draws the top label
    if self.isTitleVisible():
      self.addstr(0, 0, self._getTitle(width), curses.A_STANDOUT)
    
    # restricts scroll location to valid bounds
    self.scroll = max(0, min(self.scroll, self.lastContentHeight - height + 1))
    
    # draws left-hand scroll bar if content's longer than the height
    msgIndent, dividerIndent = 1, 0 # offsets for scroll bar
    isScrollBarVisible = self.lastContentHeight > height - 1
    if isScrollBarVisible:
      msgIndent, dividerIndent = 3, 2
      self.addScrollBar(self.scroll, self.scroll + height - 1, self.lastContentHeight, 1)
    
    # draws log entries
    lineCount = 1 - self.scroll
    seenFirstDateDivider = False
    dividerAttr, duplicateAttr = curses.A_BOLD | uiTools.getColor("yellow"), curses.A_BOLD | uiTools.getColor("green")
    
    isDatesShown = self.regexFilter == None and self._config["features.log.showDateDividers"]
    eventLog = getDaybreaks(currentLog, self.isPaused()) if isDatesShown else list(currentLog)
    if not self.showDuplicates:
      deduplicatedLog = getDuplicates(eventLog)
      
      if deduplicatedLog == None:
        msg = "Deduplication took too long. Its current implementation has difficulty handling large logs so disabling it to keep the interface responsive."
        log.log(log.WARN, msg)
        self.showDuplicates = True
        deduplicatedLog = [(entry, 0) for entry in eventLog]
    else: deduplicatedLog = [(entry, 0) for entry in eventLog]
    
    # determines if we have the minimum width to show date dividers
    showDaybreaks = width - dividerIndent >= 3
    
    while deduplicatedLog:
      entry, duplicateCount = deduplicatedLog.pop(0)
      
      if self.regexFilter and not self.regexFilter.search(entry.getDisplayMessage()):
        continue  # filter doesn't match log message - skip
      
      # checks if we should be showing a divider with the date
      if entry.type == DAYBREAK_EVENT:
        # bottom of the divider
        if seenFirstDateDivider:
          if lineCount >= 1 and lineCount < height and showDaybreaks:
            self.addch(lineCount, dividerIndent, curses.ACS_LLCORNER,  dividerAttr)
            self.hline(lineCount, dividerIndent + 1, width - dividerIndent - 2, dividerAttr)
            self.addch(lineCount, width - 1, curses.ACS_LRCORNER, dividerAttr)
          
          lineCount += 1
        
        # top of the divider
        if lineCount >= 1 and lineCount < height and showDaybreaks:
          timeLabel = time.strftime(" %B %d, %Y ", time.localtime(entry.timestamp))
          self.addch(lineCount, dividerIndent, curses.ACS_ULCORNER, dividerAttr)
          self.addch(lineCount, dividerIndent + 1, curses.ACS_HLINE, dividerAttr)
          self.addstr(lineCount, dividerIndent + 2, timeLabel, curses.A_BOLD | dividerAttr)
          
          lineLength = width - dividerIndent - len(timeLabel) - 3
          self.hline(lineCount, dividerIndent + len(timeLabel) + 2, lineLength, dividerAttr)
          self.addch(lineCount, dividerIndent + len(timeLabel) + 2 + lineLength, curses.ACS_URCORNER, dividerAttr)
        
        seenFirstDateDivider = True
        lineCount += 1
      else:
        # entry contents to be displayed, tuples of the form:
        # (msg, formatting, includeLinebreak)
        displayQueue = []
        
        msgComp = entry.getDisplayMessage().split("\n")
        for i in range(len(msgComp)):
          font = curses.A_BOLD if "ERR" in entry.type else curses.A_NORMAL # emphasizes ERR messages
          displayQueue.append((msgComp[i].strip(), font | uiTools.getColor(entry.color), i != len(msgComp) - 1))
        
        if duplicateCount:
          pluralLabel = "s" if duplicateCount > 1 else ""
          duplicateMsg = DUPLICATE_MSG % (duplicateCount, pluralLabel)
          displayQueue.append((duplicateMsg, duplicateAttr, False))
        
        cursorLoc, lineOffset = msgIndent, 0
        maxEntriesPerLine = self._config["features.log.maxLinesPerEntry"]
        while displayQueue:
          msg, format, includeBreak = displayQueue.pop(0)
          drawLine = lineCount + lineOffset
          if lineOffset == maxEntriesPerLine: break
          
          maxMsgSize = width - cursorLoc - 1
          if len(msg) > maxMsgSize:
            # message is too long - break it up
            if lineOffset == maxEntriesPerLine - 1:
              msg = uiTools.cropStr(msg, maxMsgSize)
            else:
              msg, remainder = uiTools.cropStr(msg, maxMsgSize, 4, 4, uiTools.Ending.HYPHEN, True)
              displayQueue.insert(0, (remainder.strip(), format, includeBreak))
            
            includeBreak = True
          
          if drawLine < height and drawLine >= 1:
            if seenFirstDateDivider and width - dividerIndent >= 3 and showDaybreaks:
              self.addch(drawLine, dividerIndent, curses.ACS_VLINE, dividerAttr)
              self.addch(drawLine, width - 1, curses.ACS_VLINE, dividerAttr)
            
            self.addstr(drawLine, cursorLoc, msg, format)
          
          cursorLoc += len(msg)
          
          if includeBreak or not displayQueue:
            lineOffset += 1
            cursorLoc = msgIndent + ENTRY_INDENT
        
        lineCount += lineOffset
      
      # if this is the last line and there's room, then draw the bottom of the divider
      if not deduplicatedLog and seenFirstDateDivider:
        if lineCount < height and showDaybreaks:
          self.addch(lineCount, dividerIndent, curses.ACS_LLCORNER, dividerAttr)
          self.hline(lineCount, dividerIndent + 1, width - dividerIndent - 2, dividerAttr)
          self.addch(lineCount, width - 1, curses.ACS_LRCORNER, dividerAttr)
        
        lineCount += 1
    
    # redraw the display if...
    # - lastContentHeight was off by too much
    # - we're off the bottom of the page
    newContentHeight = lineCount + self.scroll - 1
    contentHeightDelta = abs(self.lastContentHeight - newContentHeight)
    forceRedraw, forceRedrawReason = True, ""
    
    if contentHeightDelta >= CONTENT_HEIGHT_REDRAW_THRESHOLD:
      forceRedrawReason = "estimate was off by %i" % contentHeightDelta
    elif newContentHeight > height and self.scroll + height - 1 > newContentHeight:
      forceRedrawReason = "scrolled off the bottom of the page"
    elif not isScrollBarVisible and newContentHeight > height - 1:
      forceRedrawReason = "scroll bar wasn't previously visible"
    elif isScrollBarVisible and newContentHeight <= height - 1:
      forceRedrawReason = "scroll bar shouldn't be visible"
    else: forceRedraw = False
    
    self.lastContentHeight = newContentHeight
    if forceRedraw:
      forceRedrawReason = "redrawing the log panel with the corrected content height (%s)" % forceRedrawReason
      log.log(self._config["log.logPanel.forceDoubleRedraw"], forceRedrawReason)
      self.redraw(True)
    
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
    
    lastDay = daysSince() # used to determine if the date has changed
    while not self._halt:
      currentDay = daysSince()
      timeSinceReset = time.time() - self._lastUpdate
      maxLogUpdateRate = self._config["features.log.maxRefreshRate"] / 1000.0
      
      sleepTime = 0
      if (self.msgLog == self._lastLoggedEvents and lastDay == currentDay) or self.isPaused():
        sleepTime = 5
      elif timeSinceReset < maxLogUpdateRate:
        sleepTime = max(0.05, maxLogUpdateRate - timeSinceReset)
      
      if sleepTime:
        self._cond.acquire()
        if not self._halt: self._cond.wait(sleepTime)
        self._cond.release()
      else:
        lastDay = currentDay
        self.redraw(True)
        
        # makes sure that we register this as an update, otherwise lacking the
        # curses lock can cause a busy wait here
        self._lastUpdate = time.time()
  
  def stop(self):
    """
    Halts further resolutions and terminates the thread.
    """
    
    self._cond.acquire()
    self._halt = True
    self._cond.notifyAll()
    self._cond.release()
  
  def _resetListener(self, _, eventType):
    # if we're attaching to a new tor instance then clears the log and
    # prepopulates it with the content belonging to this instance
    
    if eventType == torTools.State.INIT:
      self.reprepopulateEvents()
      self.redraw(True)
  
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
      # - condense further if there's identical runlevel ranges for multiple
      #   types (ex. "NOTICE - ERR, ARM_NOTICE - ERR" becomes "TOR/ARM NOTICE - ERR")
      tmpRunlevels = [] # runlevels pulled from the list (just the runlevel part)
      runlevelRanges = [] # tuple of type, startLevel, endLevel for ranges to be consensed
      
      # reverses runlevels and types so they're appended in the right order
      reversedRunlevels = log.Runlevel.values()
      reversedRunlevels.reverse()
      for prefix in ("TORCTL_", "ARM_", ""):
        # blank ending runlevel forces the break condition to be reached at the end
        for runlevel in reversedRunlevels + [""]:
          eventType = prefix + runlevel
          if runlevel and eventType in eventsList:
            # runlevel event found, move to the tmp list
            eventsList.remove(eventType)
            tmpRunlevels.append(runlevel)
          elif tmpRunlevels:
            # adds all tmp list entries to the start of eventsList
            if len(tmpRunlevels) >= 3:
              # save condense sequential runlevels to be added later
              runlevelRanges.append((prefix, tmpRunlevels[-1], tmpRunlevels[0]))
            else:
              # adds runlevels individaully
              for tmpRunlevel in tmpRunlevels:
                eventsList.insert(0, prefix + tmpRunlevel)
            
            tmpRunlevels = []
      
      # adds runlevel ranges, condensing if there's identical ranges
      for i in range(len(runlevelRanges)):
        if runlevelRanges[i]:
          prefix, startLevel, endLevel = runlevelRanges[i]
          
          # check for matching ranges
          matches = []
          for j in range(i + 1, len(runlevelRanges)):
            if runlevelRanges[j] and runlevelRanges[j][1] == startLevel and runlevelRanges[j][2] == endLevel:
              matches.append(runlevelRanges[j])
              runlevelRanges[j] = None
          
          if matches:
            # strips underscores and replaces empty entries with "TOR"
            prefixes = [entry[0] for entry in matches] + [prefix]
            for k in range(len(prefixes)):
              if prefixes[k] == "": prefixes[k] = "TOR"
              else: prefixes[k] = prefixes[k].replace("_", "")
            
            eventsList.insert(0, "%s %s - %s" % ("/".join(prefixes), startLevel, endLevel))
          else:
            eventsList.insert(0, "%s%s - %s" % (prefix, startLevel, endLevel))
      
      # truncates to use an ellipsis if too long, for instance:
      attrLabel = ", ".join(eventsList)
      if currentPattern: attrLabel += " - filter: %s" % currentPattern
      attrLabel = uiTools.cropStr(attrLabel, width - 10, 1)
      if attrLabel: attrLabel = " (%s)" % attrLabel
      panelLabel = "Events%s:" % attrLabel
    
    # cache results and return
    self._titleCache = panelLabel
    self._titleArgs = (list(self.loggedEvents), currentPattern, width)
    self.valsLock.release()
    return panelLabel
  
  def _trimEvents(self, eventListing):
    """
    Crops events that have either:
    - grown beyond the cache limit
    - outlived the configured log duration
    
    Argument:
      eventListing - listing of log entries
    """
    
    cacheSize = self._config["cache.logPanel.size"]
    if len(eventListing) > cacheSize: del eventListing[cacheSize:]
    
    logTTL = self._config["features.log.entryDuration"]
    if logTTL > 0:
      currentDay = daysSince()
      
      breakpoint = None # index at which to crop from
      for i in range(len(eventListing) - 1, -1, -1):
        daysSinceEvent = currentDay - daysSince(eventListing[i].timestamp)
        if daysSinceEvent > logTTL: breakpoint = i # older than the ttl
        else: break
      
      # removes entries older than the ttl
      if breakpoint != None: del eventListing[breakpoint:]

