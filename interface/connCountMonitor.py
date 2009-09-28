#!/usr/bin/env python
# connCountMonitor.py -- Tracks the number of connections made by Tor.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

from TorCtl import TorCtl

import graphPanel

class ConnCountMonitor(graphPanel.GraphStats, TorCtl.PostEventListener):
  """
  Tracks number of connections, counting client and directory connections as 
  outbound.
  """
  
  def __init__(self, conn, connResolver):
    graphPanel.GraphStats.__init__(self)
    TorCtl.PostEventListener.__init__(self)
    graphPanel.GraphStats.initialize(self, "green", "cyan", 10)
    self.connResolver = connResolver    # thread performing netstat queries
    self.orPort = conn.get_option("ORPort")[0][1]
    self.dirPort = conn.get_option("DirPort")[0][1]
    self.controlPort = conn.get_option("ControlPort")[0][1]
  
  def bandwidth_event(self, event):
    # doesn't use events but this keeps it in sync with the bandwidth panel
    # (and so it stops if Tor stops - used to use a separate thread but this
    # is better)
    inbound, outbound, control = 0, 0, 0
    results = self.connResolver.getConnections()
    
    for line in results:
      if not line.startswith("tcp"): continue
      param = line.split()
      localPort = param[3][param[3].find(":") + 1:]
      
      if localPort in (self.orPort, self.dirPort): inbound += 1
      elif localPort == self.controlPort: control += 1
      else: outbound += 1
    
    self._processEvent(inbound, outbound)
  
  def getTitle(self, width):
    return "Connection Count:"
  
  def getHeaderLabel(self, width, isPrimary):
    avg = (self.primaryTotal if isPrimary else self.secondaryTotal) / max(1, self.tick)
    if isPrimary: return "Inbound (%s, avg: %s):" % (self.lastPrimary, avg)
    else: return "Outbound (%s, avg: %s):" % (self.lastSecondary, avg)

