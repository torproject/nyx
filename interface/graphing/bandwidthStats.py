"""
Tracks bandwidth usage of the tor process, expanding to include accounting
stats if they're set.
"""

import time
from TorCtl import TorCtl

import graphPanel
from util import torTools, uiTools

DL_COLOR, UL_COLOR = "green", "cyan"

# width at which panel abandons placing optional stats (avg and total) with
# header in favor of replacing the x-axis label
COLLAPSE_WIDTH = 135

# valid keys for the accountingInfo mapping
ACCOUNTING_ARGS = ("status", "resetTime", "read", "written", "readLimit", "writtenLimit")

DEFAULT_CONFIG = {"features.graph.bw.accounting.show": True, "features.graph.bw.accounting.rate": 10, "features.graph.bw.accounting.isTimeLong": False}

class BandwidthStats(graphPanel.GraphStats, TorCtl.PostEventListener):
  """
  Uses tor BW events to generate bandwidth usage graph.
  """
  
  def __init__(self, config=None):
    graphPanel.GraphStats.__init__(self)
    TorCtl.PostEventListener.__init__(self)
    
    self._config = dict(DEFAULT_CONFIG)
    if config:
      config.update(self._config)
      self._config["features.graph.bw.accounting.rate"] = max(1, self._config["features.graph.bw.accounting.rate"])
    
    # accounting data (set by _updateAccountingInfo method)
    self.accountingLastUpdated = 0
    self.accountingInfo = dict([(arg, "") for arg in ACCOUNTING_ARGS])
    
    # listens for tor reload (sighup) events which can reset the bandwidth
    # rate/burst and if tor's using accounting
    conn = torTools.getConn()
    self._titleStats, self.isAccounting = [], False
    self.resetListener(conn, torTools.TOR_INIT) # initializes values
    conn.addStatusListener(self.resetListener)
  
  def resetListener(self, conn, eventType):
    # updates title parameters and accounting status if they changed
    self.new_desc_event(None) # updates title params
    
    if self._config["features.graph.bw.accounting.show"]:
      self.isAccounting = conn.getInfo('accounting/enabled') == '1'
  
  def bandwidth_event(self, event):
    if self.isAccounting and self.isNextTickRedraw():
      if time.time() - self.accountingLastUpdated >= self._config["features.graph.bw.accounting.rate"]:
        self._updateAccountingInfo()
    
    # scales units from B to KB for graphing
    self._processEvent(event.read / 1024.0, event.written / 1024.0)
  
  def draw(self, panel, width, height):
    # if display is narrow, overwrites x-axis labels with avg / total stats
    if width <= COLLAPSE_WIDTH:
      # clears line
      panel.addstr(8, 0, " " * width)
      graphCol = min((width - 10) / 2, self.maxCol)
      
      primaryFooter = "%s, %s" % (self._getAvgLabel(True), self._getTotalLabel(True))
      secondaryFooter = "%s, %s" % (self._getAvgLabel(False), self._getTotalLabel(False))
      
      panel.addstr(8, 1, primaryFooter, uiTools.getColor(self.getColor(True)))
      panel.addstr(8, graphCol + 6, secondaryFooter, uiTools.getColor(self.getColor(False)))
    
    # provides accounting stats if enabled
    if self.isAccounting:
      if torTools.getConn().isAlive():
        status = self.accountingInfo["status"]
        
        hibernateColor = "green"
        if status == "soft": hibernateColor = "yellow"
        elif status == "hard": hibernateColor = "red"
        elif status == "":
          # failed to be queried
          status, hibernateColor = "unknown", "red"
        
        panel.addfstr(10, 0, "<b>Accounting (<%s>%s</%s>)</b>" % (hibernateColor, status, hibernateColor))
        
        resetTime = self.accountingInfo["resetTime"]
        if not resetTime: resetTime = "unknown"
        panel.addstr(10, 35, "Time to reset: %s" % resetTime)
        
        used, total = self.accountingInfo["read"], self.accountingInfo["readLimit"]
        if used and total:
          panel.addstr(11, 2, "%s / %s" % (used, total), uiTools.getColor(self.getColor(True)))
        
        used, total = self.accountingInfo["written"], self.accountingInfo["writtenLimit"]
        if used and total:
          panel.addstr(11, 37, "%s / %s" % (used, total), uiTools.getColor(self.getColor(False)))
      else:
        panel.addfstr(10, 0, "<b>Accounting:</b> Connection Closed...")
  
  def getTitle(self, width):
    stats = list(self._titleStats)
    
    while True:
      if not stats: return "Bandwidth:"
      else:
        label = "Bandwidth (%s):" % ", ".join(stats)
        
        if len(label) > width: del stats[-1]
        else: return label
  
  def getHeaderLabel(self, width, isPrimary):
    graphType = "Downloaded" if isPrimary else "Uploaded"
    stats = [""]
    
    # if wide then avg and total are part of the header, otherwise they're on
    # the x-axis
    if width * 2 > COLLAPSE_WIDTH:
      stats = [""] * 3
      stats[1] = "- %s" % self._getAvgLabel(isPrimary)
      stats[2] = ", %s" % self._getTotalLabel(isPrimary)
    
    stats[0] = "%-14s" % ("%s/sec" % uiTools.getSizeLabel((self.lastPrimary if isPrimary else self.lastSecondary) * 1024, 1))
    
    # drops label's components if there's not enough space
    labeling = graphType + " (" + "".join(stats).strip() + "):"
    while len(labeling) >= width:
      if len(stats) > 1:
        del stats[-1]
        labeling = graphType + " (" + "".join(stats).strip() + "):"
      else:
        labeling = graphType + ":"
        break
    
    return labeling
  
  def getColor(self, isPrimary):
    return DL_COLOR if isPrimary else UL_COLOR
  
  def getPreferredHeight(self):
    return 13 if self.isAccounting else 10
  
  def new_desc_event(self, event):
    # updates self._titleStats with updated values
    conn = torTools.getConn()
    if not conn.isAlive(): return # keep old values
    
    myFingerprint = conn.getMyFingerprint()
    if not self._titleStats or not myFingerprint or (event and myFingerprint in event.idlist):
      stats = []
      bwRate = conn.getMyBandwidthRate()
      bwBurst = conn.getMyBandwidthBurst()
      bwObserved = conn.getMyBandwidthObserved()
      
      if bwRate and bwBurst:
        bwRate = uiTools.getSizeLabel(bwRate, 1)
        bwBurst = uiTools.getSizeLabel(bwBurst, 1)
        
        # if both are using rounded values then strip off the ".0" decimal
        if ".0" in bwRate and ".0" in bwBurst:
          bwRate = bwRate.replace(".0", "")
          bwBurst = bwBurst.replace(".0", "")
        
        stats.append("limit: %s" % bwRate)
        stats.append("burst: %s" % bwBurst)
      
      if bwObserved: stats.append("observed: %s" % uiTools.getSizeLabel(bwObserved, 1))
      
      self._titleStats = stats
  
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
    
    Any failed lookups result in a mapping to an empty string.
    """
    
    conn = torTools.getConn()
    queried = dict([(arg, "") for arg in ACCOUNTING_ARGS])
    queried["status"] = conn.getInfo("accounting/hibernating")
    
    # provides a nicely formatted reset time
    endInterval = conn.getInfo("accounting/interval-end")
    if endInterval:
      # converts from gmt to local with respect to DST
      if time.localtime()[8]: tz_offset = time.altzone
      else: tz_offset = time.timezone
      
      sec = time.mktime(time.strptime(endInterval, "%Y-%m-%d %H:%M:%S")) - time.time() - tz_offset
      if self._config["features.graph.bw.accounting.isTimeLong"]:
        queried["resetTime"] = ", ".join(uiTools.getTimeLabels(sec, True))
      else:
        days = sec / 86400
        sec %= 86400
        hours = sec / 3600
        sec %= 3600
        minutes = sec / 60
        sec %= 60
        queried["resetTime"] = "%i:%02i:%02i:%02i" % (days, hours, minutes, sec)
    
    # number of bytes used and in total for the accounting period
    used = conn.getInfo("accounting/bytes")
    left = conn.getInfo("accounting/bytes-left")
    
    if used and left:
      usedComp, leftComp = used.split(" "), left.split(" ")
      read, written = int(usedComp[0]), int(usedComp[1])
      readLeft, writtenLeft = int(leftComp[0]), int(leftComp[1])
      
      queried["read"] = uiTools.getSizeLabel(read)
      queried["written"] = uiTools.getSizeLabel(written)
      queried["readLimit"] = uiTools.getSizeLabel(read + readLeft)
      queried["writtenLimit"] = uiTools.getSizeLabel(written + writtenLeft)
    
    self.accountingInfo = queried
    self.accountingLastUpdated = time.time()

