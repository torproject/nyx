#!/usr/bin/env python
# cpuMemMonitor.py -- Tracks cpu and memory usage of Tor.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import time
from TorCtl import TorCtl

from util import sysTools, torTools, uiTools
import graphPanel

class CpuMemMonitor(graphPanel.GraphStats, TorCtl.PostEventListener):
  """
  Tracks system resource usage (cpu and memory usage), using cached values in
  headerPanel if recent enough (otherwise retrieved independently).
  """
  
  def __init__(self):
    graphPanel.GraphStats.__init__(self)
    TorCtl.PostEventListener.__init__(self)
    graphPanel.GraphStats.initialize(self, "green", "cyan", 10)
  
  def bandwidth_event(self, event):
    # doesn't use events but this keeps it in sync with the bandwidth panel
    # (and so it stops if Tor stops
    # TODO: ok, screw it - the number of ps calls this makes is ridicuous
    # compared to how frequently it changes - now caching for five seconds
    # (note this during the rewrite that its fidelity isn't at the second
    # level)
    # TODO: when rewritten raise fidelity to second level if being actively
    # looked at (or has been recently)
    # TODO: dropped header requirement so any documentation will, of course,
    # need to be revised
    torPid = torTools.getConn().getPid()
    
    # cached results stale - requery ps
    # TODO: issue the same request as header panel to take advantage of cached results
    sampling = []
    psCall = None
    if torPid:
      psCall = sysTools.call("ps -p %s -o %s" % (torPid, "%cpu,rss,%mem,etime"), 5, True)
    if psCall and len(psCall) >= 2: sampling = psCall[1].strip().split()
    
    if len(sampling) < 2:
      # either ps failed or returned no tor instance, register error
      # ps call failed (returned no tor instance or registered an  error) -
      # we need to register something (otherwise timescale would be thrown
      # off) so keep old results
      self._processEvent(self.lastPrimary, self.lastSecondary)
    else:
      self._processEvent(float(sampling[0]), float(sampling[1]) / 1024.0)
  
  def getTitle(self, width):
    return "System Resources:"
  
  def getHeaderLabel(self, width, isPrimary):
    avg = (self.primaryTotal if isPrimary else self.secondaryTotal) / max(1, self.tick)
    if isPrimary: return "CPU (%s%%, avg: %0.1f%%):" % (self.lastPrimary, avg)
    else: return "Memory (%s, avg: %s):" % (uiTools.getSizeLabel(self.lastSecondary * 1048576, 1), uiTools.getSizeLabel(avg * 1048576, 1))

