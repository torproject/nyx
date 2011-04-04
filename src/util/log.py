"""
Tracks application events, both directing them to attached listeners and
keeping a record of them. A limited space is provided for old events, keeping
and trimming them on a per-runlevel basis (ie, too many DEBUG events will only
result in entries from that runlevel being dropped). All functions are thread
safe.
"""

import os
import time
from sys import maxint
from threading import RLock

from util import enum

# Logging runlevels. These are *very* commonly used so including shorter
# aliases (so they can be referenced as log.DEBUG, log.WARN, etc).
Runlevel = enum.Enum(("DEBUG", "DEBUG"), ("INFO", "INFO"), ("NOTICE", "NOTICE"),
                     ("WARN", "WARN"), ("ERR", "ERR"))
DEBUG, INFO, NOTICE, WARN, ERR = Runlevel.values()

# provides thread safety for logging operations
LOG_LOCK = RLock()

# chronologically ordered records of events for each runlevel, stored as tuples
# consisting of: (time, message)
_backlog = dict([(level, []) for level in Runlevel.values()])

# mapping of runlevels to the listeners interested in receiving events from it
_listeners = dict([(level, []) for level in Runlevel.values()])

CONFIG = {"cache.armLog.size": 1000,
          "cache.armLog.trimSize": 200}

DUMP_FILE = None

def loadConfig(config):
  config.update(CONFIG, {
    "cache.armLog.size": 10,
    "cache.armLog.trimSize": 5})
  
  CONFIG["cache.armLog.trimSize"] = min(CONFIG["cache.armLog.trimSize"], CONFIG["cache.armLog.size"] / 2)

def setDumpFile(logPath):
  """
  Logs all future logged events to the given path. This raises an IOError if
  the file fails to be opened. If the file already exists then this overwrites
  it.
  
  Arguments:
    logPath - path where to persist logs
  """
  
  global DUMP_FILE
  
  # make sure that the parent directory exists
  baseDir = os.path.dirname(logPath)
  if not os.path.exists(baseDir): os.makedirs(baseDir)
  
  DUMP_FILE = open(logPath, "w")

def log(level, msg, eventTime = None):
  """
  Registers an event, directing it to interested listeners and preserving it in
  the backlog. If the level is None then this is a no-op.
  
  Arguments:
    level     - runlevel corresponding to the message severity
    msg       - string associated with the message
    eventTime - unix time at which the event occurred, current time if undefined
  """
  
  global DUMP_FILE
  if not level: return
  if eventTime == None: eventTime = time.time()
  
  LOG_LOCK.acquire()
  try:
    newEvent = (eventTime, msg)
    eventBacklog = _backlog[level]
    
    # inserts the new event into the backlog
    if not eventBacklog or eventTime >= eventBacklog[-1][0]:
      # newest event - append to end
      eventBacklog.append(newEvent)
    elif eventTime <= eventBacklog[0][0]:
      # oldest event - insert at start
      eventBacklog.insert(0, newEvent)
    else:
      # somewhere in the middle - start checking from the end
      for i in range(len(eventBacklog) - 1, -1, -1):
        if eventBacklog[i][0] <= eventTime:
          eventBacklog.insert(i + 1, newEvent)
          break
    
    # truncates backlog if too long
    toDelete = len(eventBacklog) - CONFIG["cache.armLog.size"]
    if toDelete >= 0: del eventBacklog[: toDelete + CONFIG["cache.armLog.trimSize"]]
    
    # persists the event if a debug file's been set
    if DUMP_FILE:
      try:
        entryTime = time.localtime(eventTime)
        timeLabel = "%i/%i/%i %02i:%02i:%02i" % (entryTime[1], entryTime[2], entryTime[0], entryTime[3], entryTime[4], entryTime[5])
        logEntry = "%s [%s] %s\n" % (timeLabel, level, msg)
        DUMP_FILE.write(logEntry)
        DUMP_FILE.flush()
      except IOError, exc:
        DUMP_FILE = None
        log(ERR, "Failed to write to the debug file - %s" % exc)
    
    # notifies listeners
    for callback in _listeners[level]:
      callback(level, msg, eventTime)
  finally:
    LOG_LOCK.release()

def addListener(level, callback):
  """
  Directs future events to the given callback function. The runlevels passed on
  to listeners are provided as the corresponding strings ("DEBUG", "INFO",
  "NOTICE", etc), and times in POSIX (unix) time.
  
  Arguments:
    level    - event runlevel the listener should be notified of
    callback - functor that'll accept the events, expected to be of the form:
               myFunction(level, msg, time)
  """
  
  if not callback in _listeners[level]:
    _listeners[level].append(callback)

def addListeners(levels, callback, dumpBacklog = False):
  """
  Directs future events of multiple runlevels to the given callback function.
  
  Arguments:
    levels      - list of runlevel events the listener should be notified of
    callback    - functor that'll accept the events, expected to be of the
                  form: myFunction(level, msg, time)
    dumpBacklog - if true, any past events of the designated runlevels will be
                  provided to the listener before returning (in chronological
                  order)
  """
  
  LOG_LOCK.acquire()
  try:
    for level in levels: addListener(level, callback)
    
    if dumpBacklog:
      for level, msg, eventTime in _getEntries(levels):
        callback(level, msg, eventTime)
  finally:
    LOG_LOCK.release()

def removeListener(level, callback):
  """
  Stops listener from being notified of further events. This returns true if a
  listener's removed, false otherwise.
  
  Arguments:
    level    - runlevel the listener to be removed
    callback - functor to be removed
  """
  
  if callback in _listeners[level]:
    _listeners[level].remove(callback)
    return True
  else: return False

def _getEntries(levels):
  """
  Generator for providing past events belonging to the given runlevels (in
  chronological order). This should be used under the LOG_LOCK to prevent
  concurrent modifications.
  
  Arguments:
    levels - runlevels for which events are provided
  """
  
  # drops any runlevels if there aren't entries in it
  toRemove = [level for level in levels if not _backlog[level]]
  for level in toRemove: levels.remove(level)
  
  # tracks where unprocessed entries start in the backlog
  backlogPtr = dict([(level, 0) for level in levels])
  
  while levels:
    earliestLevel, earliestMsg, earliestTime = None, "", maxint
    
    # finds the earliest unprocessed event
    for level in levels:
      entry = _backlog[level][backlogPtr[level]]
      
      if entry[0] < earliestTime:
        earliestLevel, earliestMsg, earliestTime = level, entry[1], entry[0]
    
    yield (earliestLevel, earliestMsg, earliestTime)
    
    # removes runlevel if there aren't any more entries
    backlogPtr[earliestLevel] += 1
    if len(_backlog[earliestLevel]) <= backlogPtr[earliestLevel]:
      levels.remove(earliestLevel)

