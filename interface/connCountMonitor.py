#!/usr/bin/env python
# connCountMonitor.py -- Tracks the number of connections made by Tor.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import os
import time
from threading import Thread
from TorCtl import TorCtl

import connPanel
import graphPanel
import util

class ConnCountMonitor(graphPanel.GraphStats, Thread):
  """
  Tracks number of connections, using cached values in connPanel if recent
  enough (otherwise retrieved independently).
  """
  
  def __init__(self, connectionPanel):
    graphPanel.GraphStats.__init__(self)
    Thread.__init__(self)
    graphPanel.GraphStats.initialize(self, connPanel.TYPE_COLORS["inbound"], connPanel.TYPE_COLORS["outbound"], 10)
    
    self.lastUpdate = -1                    # time last stats was retrived
    self.connectionPanel = connectionPanel  # connection panel, used to limit netstat calls
    
    self.setDaemon(True)
    self.start()
  
  def run(self):
    while True:
      while self.lastUpdate + 1 > time.time(): time.sleep(0.5)
      
      if self.connectionPanel.lastUpdate + 1 >= time.time():
        # reuses netstat results if recent enough
        counts = self.connectionPanel.connectionCount
        self._processEvent(counts[0], counts[1])
      else:
        # cached results stale - requery netstat
        inbound, outbound, control = 0, 0, 0
        netstatCall = os.popen("netstat -npt 2> /dev/null | grep %s/tor 2> /dev/null" % self.connectionPanel.pid)
        try:
          results = netstatCall.readlines()
          
          for line in results:
            if not line.startswith("tcp"): continue
            param = line.split()
            localPort = param[3][param[3].find(":") + 1:]
            
            if localPort in (self.connectionPanel.orPort, self.connectionPanel.dirPort): inbound += 1
            elif localPort == self.connectionPanel.controlPort: control += 1
            else: outbound += 1
        except IOError:
          # netstat call failed
          self.connectionPanel.monitor_event("WARN", "Unable to query netstat for connection counts")
        
        netstatCall.close()
        self._processEvent(inbound, outbound)
      
      self.lastUpdate = time.time()
  
  def getTitle(self, width):
    return "Connection Count:"
  
  def getHeaderLabel(self, isPrimary):
    avg = (self.primaryTotal if isPrimary else self.secondaryTotal) / max(1, self.tick)
    if isPrimary: return "Inbound (%s, avg: %s):" % (self.lastPrimary, avg)
    else: return "Outbound (%s, avg: %s):" % (self.lastSecondary, avg)

