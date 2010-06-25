"""
Tracks bandwidth usage of the tor process, expanding to include accounting
stats if they're set.
"""

import time

import graphPanel
from util import torTools, uiTools

DL_COLOR, UL_COLOR = "green", "cyan"

# width at which panel abandons placing optional stats (avg and total) with
# header in favor of replacing the x-axis label
COLLAPSE_WIDTH = 135

# valid keys for the accountingInfo mapping
ACCOUNTING_ARGS = ("status", "resetTime", "read", "written", "readLimit", "writtenLimit")

DEFAULT_CONFIG = {"features.graph.bw.showAccounting": True, "features.graph.bw.isAccountingTimeLong": False}

class BandwidthStats(graphPanel.GraphStats):
  """
  Uses tor BW events to generate bandwidth usage graph.
  """
  
  def __init__(self, config=None):
    graphPanel.GraphStats.__init__(self)
    
    self._config = dict(DEFAULT_CONFIG)
    if config: config.update(self._config)
    
    # accounting data (set by _updateAccountingInfo method)
    self.accountingInfo = dict([(arg, "") for arg in ACCOUNTING_ARGS])
    
    # listens for tor reload (sighup) events which can reset the bandwidth
    # rate/burst and if tor's using accounting
    conn = torTools.getConn()
    self.isAccounting, self.bwRate, self.bwBurst = False, None, None
    self.resetListener(conn, torTools.TOR_INIT) # initializes values
    conn.addStatusListener(self.resetListener)
  
  def resetListener(self, conn, eventType):
    # queries for rate, burst, and accounting status if it might have changed
    if eventType == torTools.TOR_INIT:
      if self._config["features.graph.bw.showAccounting"]:
        self.isAccounting = conn.getInfo('accounting/enabled') == '1'
      
      # effective relayed bandwidth is the minimum of BandwidthRate,
      # MaxAdvertisedBandwidth, and RelayBandwidthRate (if set)
      effectiveRate = int(conn.getOption("BandwidthRate"))
      
      relayRate = conn.getOption("RelayBandwidthRate")
      if relayRate and relayRate != "0":
        effectiveRate = min(effectiveRate, int(relayRate))
      
      maxAdvertised = conn.getOption("MaxAdvertisedBandwidth")
      if maxAdvertised: effectiveRate = min(effectiveRate, int(maxAdvertised))
      
      # effective burst (same for BandwidthBurst and RelayBandwidthBurst)
      effectiveBurst = int(conn.getOption("BandwidthBurst"))
      
      relayBurst = conn.getOption("RelayBandwidthBurst")
      if relayBurst and relayBurst != "0":
        effectiveBurst = min(effectiveBurst, int(relayBurst))
      
      self.bwRate = uiTools.getSizeLabel(effectiveRate, 1)
      self.bwBurst = uiTools.getSizeLabel(effectiveBurst, 1)
      
      # if both are using rounded values then strip off the ".0" decimal
      if ".0" in self.bwRate and ".0" in self.bwBurst:
        self.bwRate = self.bwRate.replace(".0", "")
        self.bwBurst = self.bwBurst.replace(".0", "")
  
  def bandwidth_event(self, event):
    if self.isAccounting and self.isNextTickRedraw():
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
      if self._config["features.graph.bw.isAccountingTimeLong"]:
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

