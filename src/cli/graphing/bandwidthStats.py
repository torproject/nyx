"""
Tracks bandwidth usage of the tor process, expanding to include accounting
stats if they're set.
"""

import time
import curses

import cli.controller

from cli.graphing import graphPanel
from util import log, sysTools, torTools, uiTools

DL_COLOR, UL_COLOR = "green", "cyan"

# width at which panel abandons placing optional stats (avg and total) with
# header in favor of replacing the x-axis label
COLLAPSE_WIDTH = 135

# valid keys for the accountingInfo mapping
ACCOUNTING_ARGS = ("status", "resetTime", "read", "written", "readLimit", "writtenLimit")

PREPOPULATE_SUCCESS_MSG = "Read the last day of bandwidth history from the state file"
PREPOPULATE_FAILURE_MSG = "Unable to prepopulate bandwidth information (%s)"

DEFAULT_CONFIG = {"features.graph.bw.transferInBytes": False,
                  "features.graph.bw.accounting.show": True,
                  "features.graph.bw.accounting.rate": 10,
                  "features.graph.bw.accounting.isTimeLong": False,
                  "log.graph.bw.prepopulateSuccess": log.NOTICE,
                  "log.graph.bw.prepopulateFailure": log.NOTICE}

class BandwidthStats(graphPanel.GraphStats):
  """
  Uses tor BW events to generate bandwidth usage graph.
  """
  
  def __init__(self, config=None, isPauseBuffer=False):
    graphPanel.GraphStats.__init__(self)
    
    self.inputConfig = config
    self._config = dict(DEFAULT_CONFIG)
    if config:
      config.update(self._config, {"features.graph.bw.accounting.rate": 1})
    
    # stats prepopulated from tor's state file
    self.prepopulatePrimaryTotal = 0
    self.prepopulateSecondaryTotal = 0
    self.prepopulateTicks = 0
    
    # accounting data (set by _updateAccountingInfo method)
    self.accountingLastUpdated = 0
    self.accountingInfo = dict([(arg, "") for arg in ACCOUNTING_ARGS])
    
    # listens for tor reload (sighup) events which can reset the bandwidth
    # rate/burst and if tor's using accounting
    conn = torTools.getConn()
    self._titleStats, self.isAccounting = [], False
    if not isPauseBuffer: self.resetListener(conn, torTools.State.INIT) # initializes values
    conn.addStatusListener(self.resetListener)
    
    # Initialized the bandwidth totals to the values reported by Tor. This
    # uses a controller options introduced in ticket 2345:
    # https://trac.torproject.org/projects/tor/ticket/2345
    # 
    # further updates are still handled via BW events to avoid unnecessary
    # GETINFO requests.
    
    self.initialPrimaryTotal = 0
    self.initialSecondaryTotal = 0
    
    readTotal = conn.getInfo("traffic/read")
    if readTotal and readTotal.isdigit():
      self.initialPrimaryTotal = int(readTotal) / 1024 # Bytes -> KB
    
    writeTotal = conn.getInfo("traffic/written")
    if writeTotal and writeTotal.isdigit():
      self.initialSecondaryTotal = int(writeTotal) / 1024 # Bytes -> KB
  
  def clone(self, newCopy=None):
    if not newCopy: newCopy = BandwidthStats(self.inputConfig, True)
    newCopy.accountingLastUpdated = self.accountingLastUpdated
    newCopy.accountingInfo = self.accountingInfo
    
    # attributes that would have been initialized from calling the resetListener
    newCopy.isAccounting = self.isAccounting
    newCopy._titleStats = self._titleStats
    
    return graphPanel.GraphStats.clone(self, newCopy)
  
  def resetListener(self, conn, eventType):
    # updates title parameters and accounting status if they changed
    self._titleStats = []     # force reset of title
    self.new_desc_event(None) # updates title params
    
    if eventType in (torTools.State.INIT, torTools.State.RESET) and self._config["features.graph.bw.accounting.show"]:
      isAccountingEnabled = conn.getInfo('accounting/enabled') == '1'
      
      if isAccountingEnabled != self.isAccounting:
        self.isAccounting = isAccountingEnabled
        
        # redraws the whole screen since our height changed
        cli.controller.getController().redraw()
    
    # redraws to reflect changes (this especially noticeable when we have
    # accounting and shut down since it then gives notice of the shutdown)
    if self._graphPanel and self.isSelected: self._graphPanel.redraw(True)
  
  def prepopulateFromState(self):
    """
    Attempts to use tor's state file to prepopulate values for the 15 minute
    interval via the BWHistoryReadValues/BWHistoryWriteValues values. This
    returns True if successful and False otherwise.
    """
    
    # checks that this is a relay (if ORPort is unset, then skip)
    conn = torTools.getConn()
    orPort = conn.getOption("ORPort")
    if orPort == "0": return
    
    # gets the uptime (using the same parameters as the header panel to take
    # advantage of caching
    uptime = None
    queryPid = conn.getMyPid()
    if queryPid:
      queryParam = ["%cpu", "rss", "%mem", "etime"]
      queryCmd = "ps -p %s -o %s" % (queryPid, ",".join(queryParam))
      psCall = sysTools.call(queryCmd, 3600, True)
      
      if psCall and len(psCall) == 2:
        stats = psCall[1].strip().split()
        if len(stats) == 4: uptime = stats[3]
    
    # checks if tor has been running for at least a day, the reason being that
    # the state tracks a day's worth of data and this should only prepopulate
    # results associated with this tor instance
    if not uptime or not "-" in uptime:
      msg = PREPOPULATE_FAILURE_MSG % "insufficient uptime"
      log.log(self._config["log.graph.bw.prepopulateFailure"], msg)
      return False
    
    # get the user's data directory (usually '~/.tor')
    dataDir = conn.getOption("DataDirectory")
    if not dataDir:
      msg = PREPOPULATE_FAILURE_MSG % "data directory not found"
      log.log(self._config["log.graph.bw.prepopulateFailure"], msg)
      return False
    
    # attempt to open the state file
    try: stateFile = open("%s%s/state" % (conn.getPathPrefix(), dataDir), "r")
    except IOError:
      msg = PREPOPULATE_FAILURE_MSG % "unable to read the state file"
      log.log(self._config["log.graph.bw.prepopulateFailure"], msg)
      return False
    
    # get the BWHistory entries (ordered oldest to newest) and number of
    # intervals since last recorded
    bwReadEntries, bwWriteEntries = None, None
    missingReadEntries, missingWriteEntries = None, None
    
    # converts from gmt to local with respect to DST
    tz_offset = time.altzone if time.localtime()[8] else time.timezone
    
    for line in stateFile:
      line = line.strip()
      
      # According to the rep_hist_update_state() function the BWHistory*Ends
      # correspond to the start of the following sampling period. Also, the
      # most recent values of BWHistory*Values appear to be an incremental
      # counter for the current sampling period. Hence, offsets are added to
      # account for both.
      
      if line.startswith("BWHistoryReadValues"):
        bwReadEntries = line[20:].split(",")
        bwReadEntries = [int(entry) / 1024.0 / 900 for entry in bwReadEntries]
        bwReadEntries.pop()
      elif line.startswith("BWHistoryWriteValues"):
        bwWriteEntries = line[21:].split(",")
        bwWriteEntries = [int(entry) / 1024.0 / 900 for entry in bwWriteEntries]
        bwWriteEntries.pop()
      elif line.startswith("BWHistoryReadEnds"):
        lastReadTime = time.mktime(time.strptime(line[18:], "%Y-%m-%d %H:%M:%S")) - tz_offset
        lastReadTime -= 900
        missingReadEntries = int((time.time() - lastReadTime) / 900)
      elif line.startswith("BWHistoryWriteEnds"):
        lastWriteTime = time.mktime(time.strptime(line[19:], "%Y-%m-%d %H:%M:%S")) - tz_offset
        lastWriteTime -= 900
        missingWriteEntries = int((time.time() - lastWriteTime) / 900)
    
    if not bwReadEntries or not bwWriteEntries or not lastReadTime or not lastWriteTime:
      msg = PREPOPULATE_FAILURE_MSG % "bandwidth stats missing from state file"
      log.log(self._config["log.graph.bw.prepopulateFailure"], msg)
      return False
    
    # fills missing entries with the last value
    bwReadEntries += [bwReadEntries[-1]] * missingReadEntries
    bwWriteEntries += [bwWriteEntries[-1]] * missingWriteEntries
    
    # crops starting entries so they're the same size
    entryCount = min(len(bwReadEntries), len(bwWriteEntries), self.maxCol)
    bwReadEntries = bwReadEntries[len(bwReadEntries) - entryCount:]
    bwWriteEntries = bwWriteEntries[len(bwWriteEntries) - entryCount:]
    
    # gets index for 15-minute interval
    intervalIndex = 0
    for indexEntry in graphPanel.UPDATE_INTERVALS:
      if indexEntry[1] == 900: break
      else: intervalIndex += 1
    
    # fills the graphing parameters with state information
    for i in range(entryCount):
      readVal, writeVal = bwReadEntries[i], bwWriteEntries[i]
      
      self.lastPrimary, self.lastSecondary = readVal, writeVal
      
      self.prepopulatePrimaryTotal += readVal * 900
      self.prepopulateSecondaryTotal += writeVal * 900
      self.prepopulateTicks += 900
      
      self.primaryCounts[intervalIndex].insert(0, readVal)
      self.secondaryCounts[intervalIndex].insert(0, writeVal)
    
    self.maxPrimary[intervalIndex] = max(self.primaryCounts)
    self.maxSecondary[intervalIndex] = max(self.secondaryCounts)
    del self.primaryCounts[intervalIndex][self.maxCol + 1:]
    del self.secondaryCounts[intervalIndex][self.maxCol + 1:]
    
    msg = PREPOPULATE_SUCCESS_MSG
    missingSec = time.time() - min(lastReadTime, lastWriteTime)
    if missingSec: msg += " (%s is missing)" % uiTools.getTimeLabel(missingSec, 0, True)
    log.log(self._config["log.graph.bw.prepopulateSuccess"], msg)
    
    return True
  
  def bandwidth_event(self, event):
    if self.isAccounting and self.isNextTickRedraw():
      if time.time() - self.accountingLastUpdated >= self._config["features.graph.bw.accounting.rate"]:
        self._updateAccountingInfo()
    
    # scales units from B to KB for graphing
    self._processEvent(event.read / 1024.0, event.written / 1024.0)
  
  def draw(self, panel, width, height):
    # line of the graph's x-axis labeling
    labelingLine = graphPanel.GraphStats.getContentHeight(self) + panel.graphHeight - 2
    
    # if display is narrow, overwrites x-axis labels with avg / total stats
    if width <= COLLAPSE_WIDTH:
      # clears line
      panel.addstr(labelingLine, 0, " " * width)
      graphCol = min((width - 10) / 2, self.maxCol)
      
      primaryFooter = "%s, %s" % (self._getAvgLabel(True), self._getTotalLabel(True))
      secondaryFooter = "%s, %s" % (self._getAvgLabel(False), self._getTotalLabel(False))
      
      panel.addstr(labelingLine, 1, primaryFooter, uiTools.getColor(self.getColor(True)))
      panel.addstr(labelingLine, graphCol + 6, secondaryFooter, uiTools.getColor(self.getColor(False)))
    
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
        
        panel.addstr(labelingLine + 2, 0, "Accounting (", curses.A_BOLD)
        panel.addstr(labelingLine + 2, 12, status, curses.A_BOLD | uiTools.getColor(hibernateColor))
        panel.addstr(labelingLine + 2, 12 + len(status), ")", curses.A_BOLD)
        
        resetTime = self.accountingInfo["resetTime"]
        if not resetTime: resetTime = "unknown"
        panel.addstr(labelingLine + 2, 35, "Time to reset: %s" % resetTime)
        
        used, total = self.accountingInfo["read"], self.accountingInfo["readLimit"]
        if used and total:
          panel.addstr(labelingLine + 3, 2, "%s / %s" % (used, total), uiTools.getColor(self.getColor(True)))
        
        used, total = self.accountingInfo["written"], self.accountingInfo["writtenLimit"]
        if used and total:
          panel.addstr(labelingLine + 3, 37, "%s / %s" % (used, total), uiTools.getColor(self.getColor(False)))
      else:
        panel.addstr(labelingLine + 2, 0, "Accounting:", curses.A_BOLD)
        panel.addstr(labelingLine + 2, 12, "Connection Closed...")
  
  def getTitle(self, width):
    stats = list(self._titleStats)
    
    while True:
      if not stats: return "Bandwidth:"
      else:
        label = "Bandwidth (%s):" % ", ".join(stats)
        
        if len(label) > width: del stats[-1]
        else: return label
  
  def getHeaderLabel(self, width, isPrimary):
    graphType = "Download" if isPrimary else "Upload"
    stats = [""]
    
    # if wide then avg and total are part of the header, otherwise they're on
    # the x-axis
    if width * 2 > COLLAPSE_WIDTH:
      stats = [""] * 3
      stats[1] = "- %s" % self._getAvgLabel(isPrimary)
      stats[2] = ", %s" % self._getTotalLabel(isPrimary)
    
    stats[0] = "%-14s" % ("%s/sec" % uiTools.getSizeLabel((self.lastPrimary if isPrimary else self.lastSecondary) * 1024, 1, False, self._config["features.graph.bw.transferInBytes"]))
    
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
  
  def getContentHeight(self):
    baseHeight = graphPanel.GraphStats.getContentHeight(self)
    return baseHeight + 3 if self.isAccounting else baseHeight
  
  def new_desc_event(self, event):
    # updates self._titleStats with updated values
    conn = torTools.getConn()
    if not conn.isAlive(): return # keep old values
    
    myFingerprint = conn.getInfo("fingerprint")
    if not self._titleStats or not myFingerprint or (event and myFingerprint in event.idlist):
      stats = []
      bwRate = conn.getMyBandwidthRate()
      bwBurst = conn.getMyBandwidthBurst()
      bwObserved = conn.getMyBandwidthObserved()
      bwMeasured = conn.getMyBandwidthMeasured()
      labelInBytes = self._config["features.graph.bw.transferInBytes"]
      
      if bwRate and bwBurst:
        bwRateLabel = uiTools.getSizeLabel(bwRate, 1, False, labelInBytes)
        bwBurstLabel = uiTools.getSizeLabel(bwBurst, 1, False, labelInBytes)
        
        # if both are using rounded values then strip off the ".0" decimal
        if ".0" in bwRateLabel and ".0" in bwBurstLabel:
          bwRateLabel = bwRateLabel.replace(".0", "")
          bwBurstLabel = bwBurstLabel.replace(".0", "")
        
        stats.append("limit: %s/s" % bwRateLabel)
        stats.append("burst: %s/s" % bwBurstLabel)
      
      # Provide the observed bandwidth either if the measured bandwidth isn't
      # available or if the measured bandwidth is the observed (this happens
      # if there isn't yet enough bandwidth measurements).
      if bwObserved and (not bwMeasured or bwMeasured == bwObserved):
        stats.append("observed: %s/s" % uiTools.getSizeLabel(bwObserved, 1, False, labelInBytes))
      elif bwMeasured:
        stats.append("measured: %s/s" % uiTools.getSizeLabel(bwMeasured, 1, False, labelInBytes))
      
      self._titleStats = stats
  
  def _getAvgLabel(self, isPrimary):
    total = self.primaryTotal if isPrimary else self.secondaryTotal
    total += self.prepopulatePrimaryTotal if isPrimary else self.prepopulateSecondaryTotal
    return "avg: %s/sec" % uiTools.getSizeLabel((total / max(1, self.tick + self.prepopulateTicks)) * 1024, 1, False, self._config["features.graph.bw.transferInBytes"])
  
  def _getTotalLabel(self, isPrimary):
    total = self.primaryTotal if isPrimary else self.secondaryTotal
    total += self.initialPrimaryTotal if isPrimary else self.initialSecondaryTotal
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

