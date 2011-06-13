"""
Bandwidth monitors.
"""

import sys

import gobject
import gtk

from TorCtl import TorCtl
from gui.graphing import graphStats
from util import torTools

class BandwidthStats(graphStats.GraphStats):
  def __init__(self, widgets):
    graphStats.GraphStats.__init__(self, widgets)

    conn = torTools.getConn()
    if not conn.isAlive():
      conn.init()
    conn.setControllerEvents(["BW"])
    conn.addEventListener(self)

  def bandwidth_event(self, event):
    self._processEvent(event.read / 1024.0, event.written / 1024.0)

