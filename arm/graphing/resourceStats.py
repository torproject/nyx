"""
Tracks the system resource usage (cpu and memory) of the tor process.
"""

import arm.util.tracker

from arm.graphing import graphPanel
from arm.util import torTools

from stem.util import str_tools

class ResourceStats(graphPanel.GraphStats):
  """
  System resource usage tracker.
  """

  def __init__(self):
    graphPanel.GraphStats.__init__(self)
    self.queryPid = torTools.getConn().controller.get_pid(None)

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
      usageLabel = str_tools.get_size_label(lastAmount * 1048576, 1)
      avgLabel = str_tools.get_size_label(avg * 1048576, 1)
      return "Memory (%s, avg: %s):" % (usageLabel, avgLabel)

  def eventTick(self):
    """
    Fetch the cached measurement of resource usage from the ResourceTracker.
    """

    primary, secondary = 0, 0
    if self.queryPid:
      resourceTracker = arm.util.tracker.get_resource_tracker()

      if resourceTracker and not resourceTracker.last_query_failed():
        resources = resourceTracker.get_resource_usage()
        primary = resources.cpu_sample * 100  # decimal percentage to whole numbers
        secondary = resources.memory_bytes / 1048576  # translate size to MB so axis labels are short
        self.runCount = resourceTracker.run_counter()

    self._processEvent(primary, secondary)

