#!/usr/bin/env python
# bandwidthMonitor.py -- Tracks stats concerning bandwidth usage.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import time
import socket
from TorCtl import TorCtl

import graphPanel
from util import uiTools

DL_COLOR = "green"  # download section color
UL_COLOR = "cyan"   # upload section color

# width at which panel abandons placing optional stats (avg and total) with
# header in favor of replacing the x-axis label
COLLAPSE_WIDTH = 135

class BandwidthMonitor(graphPanel.GraphStats, TorCtl.PostEventListener):
  """
  Tor event listener, taking bandwidth sampling to draw a bar graph. This is
  updated every second by the BW events.
  """
  
  def __init__(self, conn):
    graphPanel.GraphStats.__init__(self)
    TorCtl.PostEventListener.__init__(self)
    self.conn = conn              # Tor control port connection
    self.accountingInfo = None    # accounting data (set by _updateAccountingInfo method)
    
    # dummy values for static data
    self.isAccounting = False
    self.bwRate, self.bwBurst = None, None
    self.resetOptions()
  
  def resetOptions(self):
    """
    Checks with tor for static bandwidth parameters (rates, accounting
    information, etc).
    """
    
    try:
      if not self.conn: raise ValueError
      self.isAccounting = self.conn.get_info('accounting/enabled')['accounting/enabled'] == '1'
      
      # static limit stats for label, uses relay stats if defined (internal behavior of tor)
      bwStats = self.conn.get_option(['BandwidthRate', 'BandwidthBurst'])
      relayStats = self.conn.get_option(['RelayBandwidthRate', 'RelayBandwidthBurst'])
      
      self.bwRate = uiTools.getSizeLabel(int(bwStats[0][1] if relayStats[0][1] == "0" else relayStats[0][1]), 1)
      self.bwBurst = uiTools.getSizeLabel(int(bwStats[1][1] if relayStats[1][1] == "0" else relayStats[1][1]), 1)
      
      # if both are using rounded values then strip off the ".0" decimal
      if ".0" in self.bwRate and ".0" in self.bwBurst:
        self.bwRate = self.bwRate.replace(".0", "")
        self.bwBurst = self.bwBurst.replace(".0", "")
      
    except (ValueError, socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed):
      pass # keep old values
    
    # this doesn't track accounting stats when paused so doesn't need a custom pauseBuffer
    contentHeight = 13 if self.isAccounting else 10
    graphPanel.GraphStats.initialize(self, DL_COLOR, UL_COLOR, contentHeight)
  
  def bandwidth_event(self, event):
    self._processEvent(event.read / 1024.0, event.written / 1024.0)
  
  def draw(self, panel):
    # if display is narrow, overwrites x-axis labels with avg / total stats
    if panel.maxX <= COLLAPSE_WIDTH:
      # clears line
      panel.addstr(8, 0, " " * 200)
      graphCol = min((panel.maxX - 10) / 2, graphPanel.MAX_GRAPH_COL)
      
      primaryFooter = "%s, %s" % (self._getAvgLabel(True), self._getTotalLabel(True))
      secondaryFooter = "%s, %s" % (self._getAvgLabel(False), self._getTotalLabel(False))
      
      panel.addstr(8, 1, primaryFooter, uiTools.getColor(self.primaryColor))
      panel.addstr(8, graphCol + 6, secondaryFooter, uiTools.getColor(self.secondaryColor))
    
    # provides accounting stats if enabled
    if self.isAccounting:
      if not self.isPaused: self._updateAccountingInfo()
      
      if self.accountingInfo:
        status = self.accountingInfo["status"]
        hibernateColor = "green"
        if status == "soft": hibernateColor = "yellow"
        elif status == "hard": hibernateColor = "red"
        
        panel.addfstr(10, 0, "<b>Accounting (<%s>%s</%s>)</b>" % (hibernateColor, status, hibernateColor))
        panel.addstr(10, 35, "Time to reset: %s" % self.accountingInfo["resetTime"])
        panel.addstr(11, 2, "%s / %s" % (self.accountingInfo["read"], self.accountingInfo["readLimit"]), uiTools.getColor(self.primaryColor))
        panel.addstr(11, 37, "%s / %s" % (self.accountingInfo["written"], self.accountingInfo["writtenLimit"]), uiTools.getColor(self.secondaryColor))
      else:
        panel.addfstr(10, 0, "<b>Accounting:</b> Connection Closed...")
  
  def getTitle(self, width):
    # provides label, dropping stats if there's not enough room
    capLabel = "cap: %s" % self.bwRate if self.bwRate else ""
    burstLabel = "burst: %s" % self.bwBurst if self.bwBurst else ""
    
    if capLabel and burstLabel:
      bwLabel = " (%s, %s)" % (capLabel, burstLabel)
    elif capLabel or burstLabel:
      # only one is set - use whatever's avaialble
      bwLabel = " (%s%s)" % (capLabel, burstLabel)
    else:
      bwLabel = ""
    
    labelContents = "Bandwidth%s:" % bwLabel
    if width < len(labelContents):
      labelContents = "%s):" % labelContents[:labelContents.find(",")]  # removes burst measure
      if width < len(labelContents): labelContents = "Bandwidth:"       # removes both
    
    return labelContents
  
  def getHeaderLabel(self, width, isPrimary):
    graphType = "Downloaded" if isPrimary else "Uploaded"
    stats = [""]
    
    # conditional is to avoid flickering as stats change size for tty terminals
    if width * 2 > COLLAPSE_WIDTH:
      stats = [""] * 3
      stats[1] = "- %s" % self._getAvgLabel(isPrimary)
      stats[2] = ", %s" % self._getTotalLabel(isPrimary)
    
    stats[0] = "%-14s" % ("%s/sec" % uiTools.getSizeLabel((self.lastPrimary if isPrimary else self.lastSecondary) * 1024, 1))
    
    labeling = graphType + " (" + "".join(stats).strip() + "):"
    while (len(labeling) >= width):
      if len(stats) > 1:
        del stats[-1]
        labeling = graphType + " (" + "".join(stats).strip() + "):"
      else:
        labeling = graphType + ":"
        break
    
    return labeling
  
  def _getAvgLabel(self, isPrimary):
    total = self.primaryTotal if isPrimary else self.secondaryTotal
    return "avg: %s/sec" % uiTools.getSizeLabel((total / max(1, self.tick)) * 1024, 1)
  
  def _getTotalLabel(self, isPrimary):
    total = self.primaryTotal if isPrimary else self.secondaryTotal
    return "total: %s" % uiTools.getSizeLabel(total * 1024, 1)
  
  def _updateAccountingInfo(self):
    """
    Updates mapping used for accounting info. This includes the following keys:
    status, resetTime, read, written, readLimit, writtenLimit
    
    Sets mapping to None if the Tor connection is closed.
    """
    
    try:
      self.accountingInfo = {}
      
      accountingParams = self.conn.get_info(["accounting/hibernating", "accounting/bytes", "accounting/bytes-left", "accounting/interval-end"])
      self.accountingInfo["status"] = accountingParams["accounting/hibernating"]
      
      # converts from gmt to local with respect to DST
      if time.localtime()[8]: tz_offset = time.altzone
      else: tz_offset = time.timezone
      
      sec = time.mktime(time.strptime(accountingParams["accounting/interval-end"], "%Y-%m-%d %H:%M:%S")) - time.time() - tz_offset
      resetHours = sec / 3600
      sec %= 3600
      resetMin = sec / 60
      sec %= 60
      self.accountingInfo["resetTime"] = "%i:%02i:%02i" % (resetHours, resetMin, sec)
      
      read = int(accountingParams["accounting/bytes"].split(" ")[0])
      written = int(accountingParams["accounting/bytes"].split(" ")[1])
      readLeft = int(accountingParams["accounting/bytes-left"].split(" ")[0])
      writtenLeft = int(accountingParams["accounting/bytes-left"].split(" ")[1])
      
      self.accountingInfo["read"] = uiTools.getSizeLabel(read)
      self.accountingInfo["written"] = uiTools.getSizeLabel(written)
      self.accountingInfo["readLimit"] = uiTools.getSizeLabel(read + readLeft)
      self.accountingInfo["writtenLimit"] = uiTools.getSizeLabel(written + writtenLeft)
    except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed):
      self.accountingInfo = None

