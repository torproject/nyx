"""
Tracks stats concerning tor's current connections.
"""

from cli.graphing import graphPanel
from util import connections, torTools

class ConnStats(graphPanel.GraphStats):
  """
  Tracks number of connections, counting client and directory connections as 
  outbound. Control connections are excluded from counts.
  """
  
  def __init__(self):
    graphPanel.GraphStats.__init__(self)
    
    # listens for tor reload (sighup) events which can reset the ports tor uses
    conn = torTools.getConn()
    self.orPort, self.dirPort, self.controlPort = "0", "0", "0"
    self.resetListener(conn, torTools.State.INIT) # initialize port values
    conn.addStatusListener(self.resetListener)
  
  def clone(self, newCopy=None):
    if not newCopy: newCopy = ConnStats()
    return graphPanel.GraphStats.clone(self, newCopy)
  
  def resetListener(self, conn, eventType):
    if eventType in (torTools.State.INIT, torTools.State.RESET):
      self.orPort = conn.getOption("ORPort", "0")
      self.dirPort = conn.getOption("DirPort", "0")
      self.controlPort = conn.getOption("ControlPort", "0")
  
  def eventTick(self):
    """
    Fetches connection stats from cached information.
    """
    
    inboundCount, outboundCount = 0, 0
    
    for entry in connections.getResolver("tor").getConnections():
      localPort = entry[1]
      if localPort in (self.orPort, self.dirPort): inboundCount += 1
      elif localPort == self.controlPort: pass # control connection
      else: outboundCount += 1
    
    self._processEvent(inboundCount, outboundCount)
  
  def getTitle(self, width):
    return "Connection Count:"
  
  def getHeaderLabel(self, width, isPrimary):
    avg = (self.primaryTotal if isPrimary else self.secondaryTotal) / max(1, self.tick)
    if isPrimary: return "Inbound (%s, avg: %s):" % (self.lastPrimary, avg)
    else: return "Outbound (%s, avg: %s):" % (self.lastSecondary, avg)
  
  def getRefreshRate(self):
    return 5

