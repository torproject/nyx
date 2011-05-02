"""
Tracks the system resource usage (cpu and memory) of the tor process.
"""

from cli.graphing import graphPanel
from util import sysTools, torTools, uiTools

class ResourceStats(graphPanel.GraphStats):
  """
  System resource usage tracker.
  """
  
  def __init__(self):
    graphPanel.GraphStats.__init__(self)
    self.queryPid = torTools.getConn().getMyPid()
  
  def clone(self, newCopy=None):
    if not newCopy: newCopy = ResourceStats()
    return graphPanel.GraphStats.clone(self, newCopy)
  
  def getTitle(self, width):
    return "System Resources:"
  
  def getHeaderLabel(self, width, isPrimary):
    avg = (self.primaryTotal if isPrimary else self.secondaryTotal) / max(1, self.tick)
    lastAmount = self.lastPrimary if isPrimary else self.lastSecondary
    
    if isPrimary:
      return "CPU (%0.1f%%, avg: %0.1f%%):" % (lastAmount, avg)
    else:
      # memory sizes are converted from MB to B before generating labels
      usageLabel = uiTools.getSizeLabel(lastAmount * 1048576, 1)
      avgLabel = uiTools.getSizeLabel(avg * 1048576, 1)
      return "Memory (%s, avg: %s):" % (usageLabel, avgLabel)
  
  def eventTick(self):
    """
    Fetch the cached measurement of resource usage from the ResourceTracker.
    """
    
    primary, secondary = 0, 0
    if self.queryPid:
      resourceTracker = sysTools.getResourceTracker(self.queryPid, True)
      
      if resourceTracker and not resourceTracker.lastQueryFailed():
        primary, _, secondary, _ = resourceTracker.getResourceUsage()
        primary *= 100        # decimal percentage to whole numbers
        secondary /= 1048576  # translate size to MB so axis labels are short
    
    self._processEvent(primary, secondary)

