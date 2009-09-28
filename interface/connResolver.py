#!/usr/bin/env python
# connResolver.py -- Background thread for retrieving tor's connections.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import os
import time
from threading import Thread
from threading import RLock

MIN_LOOKUP_WAIT = 5           # minimum seconds between lookups
SLEEP_INTERVAL = 1            # period to sleep when not making a netstat call
FAILURE_TOLERANCE = 3         # number of subsiquent failures tolerated before pausing thread
FAILURE_MSG = "Unable to query netstat for new connections"
SERIAL_FAILURE_MSG = "Failing to query netstat (connection related portions of the monitor won't function)"

class ConnResolver(Thread):
  """
  Service that periodically queries Tor's current connections to allow for a 
  best effort, non-blocking lookup. This is currently implemented via netstat. 
  In case of failure this gives an INFO level warning and provides the last 
  known results. This process provides an WARN level warning and pauses itself 
  if there's several subsiquent failures (probably indicating that netstat 
  isn't available).
  """
  
  def __init__(self, pid, logPanel):
    Thread.__init__(self)
    self.pid = pid                    # tor process ID to make sure we've got the right instance
    self.logger = logPanel            # used to notify of lookup failures
    
    self.connections = []             # unprocessed lines from netstat results
    self.connectionsLock = RLock()    # limits concurrent access to connections
    self.isPaused = False
    self.halt = False                 # terminates thread if true
    self.lastLookup = -1              # time of last lookup (reguardless of success)
    self.subsiquentFailures = 0       # number of failed netstat calls in a row
    self.setDaemon(True)
  
  def getConnections(self):
    """
    Provides the last querried connection results.
    """
    
    connectionsTmp = None
    
    self.connectionsLock.acquire()
    try: connectionsTmp = list(self.connections)
    finally: self.connectionsLock.release()
    
    return connectionsTmp
  
  def run(self):
    if not self.pid: return
    
    while not self.halt:
      if self.isPaused or time.time() - MIN_LOOKUP_WAIT < self.lastLookup: time.sleep(SLEEP_INTERVAL)
      else:
        try:
          # looks at netstat for tor with stderr redirected to /dev/null, options are:
          # n = prevents dns lookups, p = include process (say if it's tor), t = tcp only
          netstatCall = os.popen("netstat -npt 2> /dev/null | grep %s/tor 2> /dev/null" % self.pid)
          results = netstatCall.readlines()
          if not results: raise IOError
          
          # assign obtained results
          self.connectionsLock.acquire()
          try: self.connections = results
          finally: self.connectionsLock.release()
          
          self.subsiquentFailures = 0
        except IOError:
          # netstat call failed
          self.subsiquentFailures += 1
          self.logger.monitor_event("INFO", "%s (%i)" % (FAILURE_MSG, self.subsiquentFailures))
          
          if self.subsiquentFailures >= FAILURE_TOLERANCE:
            self.logger.monitor_event("WARN", SERIAL_FAILURE_MSG)
            self.setPaused(True)
        finally:
          self.lastLookup = time.time()
          netstatCall.close()
  
  def setPaused(self, isPause):
    """
    If true, prevents further netstat lookups.
    """
    
    if isPause == self.isPaused: return
    self.isPaused = isPause

