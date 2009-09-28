#!/usr/bin/env python
# cpuMemMonitor.py -- Tracks cpu and memory usage of Tor.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import os
import time
from TorCtl import TorCtl

import util
import graphPanel

class CpuMemMonitor(graphPanel.GraphStats, TorCtl.PostEventListener):
  """
  Tracks system resource usage (cpu and memory usage), using cached values in
  headerPanel if recent enough (otherwise retrieved independently).
  """
  
  def __init__(self, headerPanel):
    graphPanel.GraphStats.__init__(self)
    TorCtl.PostEventListener.__init__(self)
    graphPanel.GraphStats.initialize(self, "green", "cyan", 10)
    self.headerPanel = headerPanel  # header panel, used to limit ps calls
  
  def bandwidth_event(self, event):
    # doesn't use events but this keeps it in sync with the bandwidth panel
    # (and so it stops if Tor stops
    if self.headerPanel.lastUpdate + 1 >= time.time():
      # reuses ps results if recent enough
      self._processEvent(float(self.headerPanel.vals["%cpu"]), float(self.headerPanel.vals["rss"]) / 1024.0)
    else:
      # cached results stale - requery ps
      psCall = os.popen('ps -p %s -o %s  2> /dev/null' % (self.headerPanel.vals["pid"], "%cpu,rss"))
      try:
        sampling = psCall.read().strip().split()[2:]
        psCall.close()
        
        if len(sampling) < 2:
          # either ps failed or returned no tor instance, register error
          raise IOError()
        else:
          self._processEvent(float(sampling[0]), float(sampling[1]) / 1024.0)
      except IOError:
        # ps call failed - we need to register something (otherwise timescale
        # would be thrown off) so keep old results
        self._processEvent(self.lastPrimary, self.lastSecondary)
  
  def getTitle(self, width):
    return "System Resources:"
  
  def getHeaderLabel(self, width, isPrimary):
    avg = (self.primaryTotal if isPrimary else self.secondaryTotal) / max(1, self.tick)
    if isPrimary: return "CPU (%s%%, avg: %0.1f%%):" % (self.lastPrimary, avg)
    else: return "Memory (%s, avg: %s):" % (util.getSizeLabel(self.lastSecondary * 1048576, 1), util.getSizeLabel(avg * 1048576, 1))

