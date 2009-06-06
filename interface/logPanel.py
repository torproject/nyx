#!/usr/bin/env python
# logPanel.py -- Resources related to Tor event monitoring.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import time
import curses
from curses.ascii import isprint
from TorCtl import TorCtl

import util

MAX_LOG_ENTRIES = 80                # size of log buffer (max number of entries)
RUNLEVEL_EVENT_COLOR = {"DEBUG": "magenta", "INFO": "blue", "NOTICE": "green", "WARN": "yellow", "ERR": "red"}

EVENT_TYPES = {
  "d": "DEBUG",   "a": "ADDRMAP",     "l": "NEWDESC",   "u": "AUTHDIR_NEWDESCS",
  "i": "INFO",    "b": "BW",          "m": "NS",        "v": "CLIENTS_SEEN",
  "n": "NOTICE",  "c": "CIRC",        "o": "ORCONN",    "x": "STATUS_GENERAL",
  "w": "WARN",    "f": "DESCCHANGED", "s": "STREAM",    "y": "STATUS_CLIENT",
  "e": "ERR",     "g": "GUARD",       "t": "STREAM_BW", "z": "STATUS_SERVER"}
  
def expandEvents(eventAbbr):
  """
  Expands event abbreviations to their full names. Beside mappings privided in
  EVENT_TYPES this recognizes:
  A - alias for all events
  U - "UNKNOWN" events
  R - alias for runtime events (DEBUG, INFO, NOTICE, WARN, ERR)
  Raises ValueError with invalid input if any part isn't recognized.
  
  Example:
  "inUt" -> ["INFO", "NOTICE", "UNKNOWN", "STREAM_BW"]
  """
  
  expandedEvents = set()
  invalidFlags = ""
  for flag in eventAbbr:
    if flag == "A":
      expandedEvents = set(EVENT_TYPES.values())
      expandedEvents.add("UNKNOWN")
      break
    elif flag == "U":
      expandedEvents.add("UNKNOWN")
    elif flag == "R":
      expandedEvents = expandedEvents.union(set(["DEBUG", "INFO", "NOTICE", "WARN", "ERR"]))
    elif flag in EVENT_TYPES:
      expandedEvents.add(EVENT_TYPES[flag])
    else:
      invalidFlags += flag
  
  if invalidFlags: raise ValueError(invalidFlags)
  else: return expandedEvents

class LogMonitor(TorCtl.PostEventListener, util.Panel):
  """
  Tor event listener, noting messages, the time, and their type in a panel.
  """
  
  def __init__(self, lock, loggedEvents):
    TorCtl.PostEventListener.__init__(self)
    util.Panel.__init__(self, lock, -1)
    self.msgLog = []                      # tuples of (logText, color)
    self.isPaused = False
    self.pauseBuffer = []                 # location where messages are buffered if paused
    self.loggedEvents = loggedEvents      # events we're listening to
    self.lastHeartbeat = time.time()      # time of last BW event
  
  # Listens for all event types and redirects to registerEvent
  def circ_status_event(self, event):
    optionalParams = ""
    if event.purpose: optionalParams += " PURPOSE: %s" % event.purpose
    if event.reason: optionalParams += " REASON: %s" % event.reason
    if event.remote_reason: optionalParams += " REMOTE_REASON: %s" % event.remote_reason
    self.registerEvent("CIRC", "ID: %-3s STATUS: %-10s PATH: %s%s" % (event.circ_id, event.status, ", ".join(event.path), optionalParams), "yellow")
  
  def stream_status_event(self, event):
    # TODO: not sure how to stimulate event - needs sanity check
    try:
      self.registerEvent("STREAM", "ID: %s STATUS: %s CIRC_ID: %s TARGET: %s:%s REASON: %s REMOTE_REASON: %s SOURCE: %s SOURCE_ADDR: %s PURPOSE: %s" % (event.strm_id, event.status, event.circ_id, event.target_host, event.target_port, event.reason, event.remote_reason, event.source, event.source_addr, event.purpose), "white")
    except TypeError:
      self.registerEvent("STREAM", "DEBUG -> ID: %s STATUS: %s CIRC_ID: %s TARGET: %s:%s REASON: %s REMOTE_REASON: %s SOURCE: %s SOURCE_ADDR: %s PURPOSE: %s" % (type(event.strm_id), type(event.status), type(event.circ_id), type(event.target_host), type(event.target_port), type(event.reason), type(event.remote_reason), type(event.source), type(event.source_addr), type(event.purpose)), "white")
  
  def or_conn_status_event(self, event):
    optionalParams = ""
    if event.age: optionalParams += " AGE: %-3s" % event.age
    if event.read_bytes: optionalParams += " READ: %-4i" % event.read_bytes
    if event.wrote_bytes: optionalParams += " WRITTEN: %-4i" % event.wrote_bytes
    if event.reason: optionalParams += " REASON: %-6s" % event.reason
    if event.ncircs: optionalParams += " NCIRCS: %i" % event.ncircs
    self.registerEvent("ORCONN", "STATUS: %-10s ENDPOINT: %-20s%s" % (event.status, event.endpoint, optionalParams), "white")
  
  def stream_bw_event(self, event):
    # TODO: not sure how to stimulate event - needs sanity check
    try:
      self.registerEvent("STREAM_BW", "ID: %s READ: %i WRITTEN: %i" % (event.strm_id, event.bytes_read, event.bytes_written), "white")
    except TypeError:
      self.registerEvent("STREAM_BW", "DEBUG -> ID: %s READ: %i WRITTEN: %i" % (type(event.strm_id), type(event.bytes_read), type(event.bytes_written)), "white")
  
  def bandwidth_event(self, event):
    self.lastHeartbeat = time.time()
    if "BW" in self.loggedEvents: self.registerEvent("BW", "READ: %i, WRITTEN: %i" % (event.read, event.written), "cyan")
  
  def msg_event(self, event):
    self.registerEvent(event.level, event.msg, RUNLEVEL_EVENT_COLOR[event.level])
  
  def new_desc_event(self, event):
    idlistStr = [str(item) for item in event.idlist]
    self.registerEvent("NEWDESC", ", ".join(idlistStr), "white")
  
  def address_mapped_event(self, event):
    self.registerEvent("ADDRMAP", "%s, %s -> %s" % (event.when, event.from_addr, event.to_addr), "white")
  
  def ns_event(self, event):
    # NetworkStatus params: nickname, idhash, orhash, ip, orport (int), dirport (int), flags, idhex, bandwidth, updated (datetime)
    msg = ""
    for ns in event.nslist:
      msg += ", %s (%s:%i)" % (ns.nickname, ns.ip, ns.orport)
    if len(msg) > 1: msg = msg[2:]
    self.registerEvent("NS", "Listed (%i): %s" % (len(event.nslist), msg), "blue")
  
  def new_consensus_event(self, event):
    msg = ""
    for ns in event.nslist:
      msg += ", %s (%s:%i)" % (ns.nickname, ns.ip, ns.orport)
    self.registerEvent("NEWCONSENSUS", "Listed (%i): %s" % (len(event.nslist), msg), "magenta")
  
  def unknown_event(self, event):
    if "UNKNOWN" in self.loggedEvents: self.registerEvent("UNKNOWN", event.event_string, "red")
  
  def monitor_event(self, level, msg):
    # events provided by the arm monitor - types use the same as runlevel
    self.registerEvent("ARM-%s" % level, msg, RUNLEVEL_EVENT_COLOR[level])
  
  def registerEvent(self, type, msg, color):
    """
    Notes event and redraws log. If paused it's held in a temporary buffer.
    """
    
    # strips control characters to avoid screwing up the terminal
    msg = "".join([char for char in msg if isprint(char)])
    
    eventTime = time.localtime()
    msgLine = "%02i:%02i:%02i [%s] %s" % (eventTime[3], eventTime[4], eventTime[5], type, msg)
    
    if self.isPaused:
      self.pauseBuffer.insert(0, (msgLine, color))
      if len(self.pauseBuffer) > MAX_LOG_ENTRIES: del self.pauseBuffer[MAX_LOG_ENTRIES:]
    else:
      self.msgLog.insert(0, (msgLine, color))
      if len(self.msgLog) > MAX_LOG_ENTRIES: del self.msgLog[MAX_LOG_ENTRIES:]
      self.redraw()
  
  def redraw(self):
    """
    Redraws message log. Entries stretch to use available space and may
    contain up to two lines. Starts with newest entries.
    """
    
    if self.win:
      if not self.lock.acquire(False): return
      try:
        self.clear()
        
        # draws label - uses ellipsis if too long, for instance:
        # Events (DEBUG, INFO, NOTICE, WARN...):
        eventsLabel = "Events"
        eventsListing = ", ".join(self.loggedEvents)
        
        firstLabelLen = eventsListing.find(", ")
        if firstLabelLen == -1: firstLabelLen = len(eventsListing)
        else: firstLabelLen += 3
        
        if self.maxX > 10 + firstLabelLen:
          eventsLabel += " ("
          if len(eventsListing) > self.maxX - 11:
            labelBreak = eventsListing[:self.maxX - 12].rfind(", ")
            eventsLabel += "%s..." % eventsListing[:labelBreak]
          else: eventsLabel += eventsListing
          eventsLabel += ")"
        eventsLabel += ":"
        
        self.addstr(0, 0, eventsLabel, util.LABEL_ATTR)
        
        # log entries
        lineCount = 1
        
        for (line, color) in self.msgLog:
          # splits over too lines if too long
          if len(line) < self.maxX:
            self.addstr(lineCount, 0, line, util.getColor(color))
            lineCount += 1
          else:
            (line1, line2) = self._splitLine(line, self.maxX)
            self.addstr(lineCount, 0, line1, util.getColor(color))
            self.addstr(lineCount + 1, 0, line2, util.getColor(color))
            lineCount += 2
          
          if lineCount >= self.maxY: break # further log messages wouldn't fit
        self.refresh()
      finally:
        self.lock.release()
  
  def setPaused(self, isPause):
    """
    If true, prevents message log from being updated with new events.
    """
    
    if isPause == self.isPaused: return
    
    self.isPaused = isPause
    if self.isPaused: self.pauseBuffer = []
    else:
      self.msgLog = (self.pauseBuffer + self.msgLog)[:MAX_LOG_ENTRIES]
      self.redraw()
  
  def getHeartbeat(self):
    """
    Provides the number of seconds since the last BW event.
    """
    
    return time.time() - self.lastHeartbeat
  
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

