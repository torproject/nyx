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
  
EVENT_LISTING = """        d DEBUG     a ADDRMAP       l NEWDESC         u AUTHDIR_NEWDESCS
        i INFO      b BW            m NS              v CLIENTS_SEEN
        n NOTICE    c CIRC          o ORCONN          x STATUS_GENERAL
        w WARN      f DESCCHANGED   s STREAM          y STATUS_CLIENT
        e ERR       g GUARD         t STREAM_BW       z STATUS_SERVER
        Aliases:    A All Events    U Unknown Events  R Runlevels (dinwe)"""

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

def drawEventLogLabel(scr, eventsListing):
  """
  Draws single line label for event log. Uses ellipsis if too long, for instance:
  Events (DEBUG, INFO, NOTICE, WARN...):
  """
  scr.clear()
  maxX = scr.maxX
  
  eventsLabel = "Events"
  
  firstLabelLen = eventsListing.find(", ")
  if firstLabelLen == -1: firstLabelLen = len(eventsListing)
  else: firstLabelLen += 3
  
  if maxX > 10 + firstLabelLen:
    eventsLabel += " ("
    if len(eventsListing) > maxX - 11:
      labelBreak = eventsListing[:maxX - 12].rfind(", ")
      eventsLabel += "%s..." % eventsListing[:labelBreak]
    else: eventsLabel += eventsListing
    eventsLabel += ")"
  eventsLabel += ":"
  
  scr.addstr(0, 0, eventsLabel, util.LABEL_ATTR)
  scr.refresh()

class LogMonitor(TorCtl.PostEventListener):
  """
  Tor event listener, noting messages, the time, and their type in a curses
  subwindow.
  """
  
  def __init__(self, scr, includeBW, includeUnknown):
    TorCtl.PostEventListener.__init__(self)
    self.scr = scr                        # associated subwindow
    self.msgLog = []                      # tuples of (logText, color)
    self.isPaused = False
    self.pauseBuffer = []                 # location where messages are buffered if paused
    self.includeBW = includeBW            # true if we're supposed to listen for BW events
    self.includeUnknown = includeUnknown  # true if registering unrecognized events
    self.lastHeartbeat = time.time()      # time of last BW event
  
  # Listens for all event types and redirects to registerEvent
  # TODO: not sure how to stimulate all event types - should be tried before
  # implemented to see what's the best formatting, what information is
  # important, and to make sure of variable's types so we don't get exceptions.
  
  def circ_status_event(self, event):
    # TODO: sanity check on how we're presenting event
    try:
      self.registerEvent("CIRC", "ID: %s STATUS: %s PATH: %s PURPOSE: %s REASON: %s REMOTE_REASON: %s" % (event.circ_id, event.status, event.path, event.purpose, event.reason, event.remote_reason), "white")
    except TypeError:
      self.registerEvent("CIRC", "DEBUG -> ID: %s STATUS: %s PATH: %s PURPOSE: %s REASON: %s REMOTE_REASON: %s" % (type(event.circ_id), type(event.status), type(event.path), type(event.purpose), type(event.reason), type(event.remote_reason)), "white")
  
  def stream_status_event(self, event):
    # TODO: sanity check on how we're presenting event
    try:
      self.registerEvent("STREAM", "ID: %s STATUS: %s CIRC_ID: %s TARGET: %s:%s REASON: %s REMOTE_REASON: %s SOURCE: %s SOURCE_ADDR: %s PURPOSE: %s" % (event.strm_id, event.status, event.circ_id, event.target_host, event.target_port, event.reason, event.remote_reason, event.source, event.source_addr, event.purpose), "white")
    except TypeError:
      self.registerEvent("STREAM", "DEBUG -> ID: %s STATUS: %s CIRC_ID: %s TARGET: %s:%s REASON: %s REMOTE_REASON: %s SOURCE: %s SOURCE_ADDR: %s PURPOSE: %s" % (type(event.strm_id), type(event.status), type(event.circ_id), type(event.target_host), type(event.target_port), type(event.reason), type(event.remote_reason), type(event.source), type(event.source_addr), type(event.purpose)), "white")
  
  def or_conn_status_event(self, event):
    self.registerEvent("ORCONN", "STATUS: %-10s ENDPOINT: %-22s AGE: %-3s READ: %-4s WRITTEN: %-4s REASON: %-6s NCIRCS: %s" % (event.status, event.endpoint, event.age, event.read_bytes, event.wrote_bytes, event.reason, event.ncircs), "white")
  
  def stream_bw_event(self, event):
    # TODO: sanity check on how we're presenting event
    try:
      self.registerEvent("STREAM_BW", "ID: %s READ: %i WRITTEN: %i" % (event.strm_id, event.bytes_read, event.bytes_written), "white")
    except TypeError:
      self.registerEvent("STREAM_BW", "DEBUG -> ID: %s READ: %i WRITTEN: %i" % (type(event.strm_id), type(event.bytes_read), type(event.bytes_written)), "white")
  
  def bandwidth_event(self, event):
    self.lastHeartbeat = time.time()
    if self.includeBW: self.registerEvent("BW", "READ: %i, WRITTEN: %i" % (event.read, event.written), "cyan")
  
  def msg_event(self, event):
    self.registerEvent(event.level, event.msg, RUNLEVEL_EVENT_COLOR[event.level])
  
  def new_desc_event(self, event):
    idlistStr = [str(item) for item in event.idlist]
    self.registerEvent("NEWDESC", ", ".join(idlistStr), "white")
  
  def address_mapped_event(self, event):
    # TODO: sanity check on how we're presenting event
    try:
      self.registerEvent("ADDRMAP", "%s FROM: %s TO: %s" % (event.when, event.from_addr, event.to_addr), "white")
    except TypeError:
      self.registerEvent("ADDRMAP", "DEBUG -> %s FROM: %s TO: %s" % (type(event.when), type(event.from_addr), type(event.to_addr)), "white")
  
  def ns_event(self, event):
    # TODO: sanity check on how we're presenting event
    nslistStr = [str(item) for item in event.nslist]
    self.registerEvent("NS", ", ".join(nslistStr), "white")
  
  def new_consensus_event(self, event):
    # TODO: sanity check on how we're presenting event
    nslistStr = [str(item) for item in event.nslist]
    self.registerEvent("NEWCONSENSUS", str(nslistStr), "white")
  
  def unknown_event(self, event):
    if self.includeUnknown: self.registerEvent("UNKNOWN", event.event_string, "red")
  
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
      self.refreshDisplay()
  
  def refreshDisplay(self):
    """
    Redraws message log. Entries stretch to use available space and may
    contain up to two lines. Starts with newest entries.
    """
    
    if self.scr:
      if not self.scr.lock.acquire(False): return
      try:
        self.scr.clear()
        x, y = self.scr.maxX, self.scr.maxY
        lineCount = 0
        
        for (line, color) in self.msgLog:
          # splits over too lines if too long
          if len(line) < x:
            self.scr.addstr(lineCount, 0, line, util.getColor(color))
            lineCount += 1
          else:
            (line1, line2) = self._splitLine(line, x)
            self.scr.addstr(lineCount, 0, line1, util.getColor(color))
            self.scr.addstr(lineCount + 1, 0, line2, util.getColor(color))
            lineCount += 2
          
          if lineCount >= y: break # further log messages wouldn't fit
        self.scr.refresh()
      finally:
        self.scr.lock.release()
  
  def setPaused(self, isPause):
    """
    If true, prevents message log from being updated with new events.
    """
    
    if isPause == self.isPaused: return
    
    self.isPaused = isPause
    if self.isPaused: self.pauseBuffer = []
    else:
      self.msgLog = (self.pauseBuffer + self.msgLog)[:MAX_LOG_ENTRIES]
      self.refreshDisplay()
  
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

