"""
Bandwidth monitors.
"""

import sys

import gobject
import gtk

from gui.graphing import graphPanel
from starter import CONFIG
from util import torTools, uiTools
from TorCtl import TorCtl

class BandwidthStats(graphPanel.GraphPanel):
  def __init__(self, builder):
    graphPanel.GraphPanel.__init__(self, builder)

    conn = torTools.getConn()
    if not conn.isAlive():
      try:
        conn.init()
      except ValueError:
        if CONFIG['features.allowDetachedStartup']:
          return
        else:
          raise

    conn.setControllerEvents(["BW", "NEWDESC"])
    conn.addEventListener(self)

    self.new_desc_event(None)

  def new_desc_event(self, event):
    conn = torTools.getConn()

    if not conn.isAlive():
      return

    bwRate = conn.getMyBandwidthRate()
    bwBurst = conn.getMyBandwidthBurst()
    bwObserved = conn.getMyBandwidthObserved()
    bwMeasured = conn.getMyBandwidthMeasured()

    if bwRate and bwBurst:
      bwRateLabel = uiTools.getSizeLabel(bwRate, 1, False, isBytes=False)
      bwBurstLabel = uiTools.getSizeLabel(bwBurst, 1, False, isBytes=False)

      msg = "Limit: %s/s, Burst: %s/s" % (bwRateLabel, bwBurstLabel)
      label = self.builder.get_object('label_graph_top')
      label.set_text(msg)

  def bandwidth_event(self, event):
    self._process_event(event.read, event.written)

    msg = 'Download: %s/s' % uiTools.getSizeLabel(event.read, 2, isBytes=False)
    self.update_header('primary', msg)
    msg = 'Upload: %s/s' % uiTools.getSizeLabel(event.written, 2, isBytes=False)
    self.update_header('secondary', msg)

