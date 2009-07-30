#!/usr/bin/env python
# bandwidthPanel.py -- Resources related to monitoring Tor bandwidth usage.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import time
import copy
import curses
from TorCtl import TorCtl

import util

BANDWIDTH_GRAPH_COL = 30            # columns of data in graph
BANDWIDTH_GRAPH_COLOR_DL = "green"  # download section color
BANDWIDTH_GRAPH_COLOR_UL = "cyan"   # upload section color

# time intervals at which graphs can be updated
DEFAULT_INTERVAL_INDEX = 1   # defaults to using five seconds of data per bar in the graph
UPDATE_INTERVALS = [("each second", 1),     ("5 seconds", 5),   ("30 seconds", 30),   ("minutely", 60),
                    ("half hour", 1800),    ("hourly", 3600),   ("daily", 86400)]

class BandwidthMonitor(TorCtl.PostEventListener, util.Panel):
  """
  Tor event listener, taking bandwidth sampling and drawing bar graph. This is
  updated every second by the BW events and graph samples are spaced at
  a timescale determined by the updateIntervalIndex.
  """
  
  def __init__(self, lock, conn):
    TorCtl.PostEventListener.__init__(self)
    if conn: self.isAccounting = conn.get_info('accounting/enabled')['accounting/enabled'] == '1'
    else: self.isAccounting = False
    
    self.contentHeight = 13 if self.isAccounting else 10
    util.Panel.__init__(self, lock, self.contentHeight)
    
    self.conn = conn              # Tor control port connection
    self.tick = 0                 # number of updates performed
    self.lastDownloadRate = 0     # most recently sampled rates
    self.lastUploadRate = 0
    self.accountingInfo = None    # accounting data (set by _updateAccountingInfo method)
    self.isPaused = False
    self.isVisible = True
    self.showLabel = True         # shows top label if true, hides otherwise
    self.pauseBuffer = None       # mirror instance used to track updates when paused
    self.updateIntervalIndex = DEFAULT_INTERVAL_INDEX
    
    # graphed download (read) and upload (write) rates - first index accumulator
    # iterative insert is to avoid issue with shallow copies (nasty, nasty gotcha)
    self.downloadRates, self.uploadRates = [], []
    for i in range(len(UPDATE_INTERVALS)):
      self.downloadRates.insert(0, (BANDWIDTH_GRAPH_COL + 1) * [0])
      self.uploadRates.insert(0, (BANDWIDTH_GRAPH_COL + 1) * [0])
    
    # max rates seen, used to determine graph bounds
    self.maxDownloadRate = len(UPDATE_INTERVALS) * [1]
    self.maxUploadRate = len(UPDATE_INTERVALS) * [1]
    
    # used to calculate averages, uses tick for time
    self.totalDownload = 0
    self.totalUpload = 0
    
    # retrieves static stats for label
    if conn:
      bwStats = conn.get_option(['BandwidthRate', 'BandwidthBurst'])
      self.bwRate = util.getSizeLabel(int(bwStats[0][1]))
      self.bwBurst = util.getSizeLabel(int(bwStats[1][1]))
    else: self.bwRate, self.bwBurst = -1, -1
  
  def bandwidth_event(self, event):
    if self.isPaused or not self.isVisible: self.pauseBuffer.bandwidth_event(event)
    else:
      self.lastDownloadRate = event.read
      self.lastUploadRate = event.written
      
      self.totalDownload += event.read
      self.totalUpload += event.written
      
      # updates graphs for all time intervals
      self.tick += 1
      for i in range(len(UPDATE_INTERVALS)):
        self.downloadRates[i][0] += event.read
        self.uploadRates[i][0] += event.written
        interval = UPDATE_INTERVALS[i][1]
        
        if self.tick % interval == 0:
          self.maxDownloadRate[i] = max(self.maxDownloadRate[i], self.downloadRates[i][0] / interval)
          self.downloadRates[i].insert(0, 0)
          del self.downloadRates[i][BANDWIDTH_GRAPH_COL + 1:]
          
          self.maxUploadRate[i] = max(self.maxUploadRate[i], self.uploadRates[i][0] / interval)
          self.uploadRates[i].insert(0, 0)
          del self.uploadRates[i][BANDWIDTH_GRAPH_COL + 1:]
      
      self.redraw()
  
  def redraw(self):
    """ Redraws bandwidth panel. """
    # doesn't draw if headless (indicating that the instance is for a pause buffer)
    if self.win:
      if not self.lock.acquire(False): return
      try:
        self.clear()
        dlColor = util.getColor(BANDWIDTH_GRAPH_COLOR_DL)
        ulColor = util.getColor(BANDWIDTH_GRAPH_COLOR_UL)
        
        # draws label, dropping stats if there's not enough room
        labelContents = "Bandwidth (cap: %s, burst: %s):" % (self.bwRate, self.bwBurst)
        if self.maxX < len(labelContents):
          labelContents = "%s):" % labelContents[:labelContents.find(",")]  # removes burst measure
          if self.maxX < len(labelContents): labelContents = "Bandwidth:"   # removes both
        
        if self.showLabel: self.addstr(0, 0, labelContents, util.LABEL_ATTR)
        
        # current numeric measures
        self.addstr(1, 0, "Downloaded (%s/sec):" % util.getSizeLabel(self.lastDownloadRate), curses.A_BOLD | dlColor)
        self.addstr(1, 35, "Uploaded (%s/sec):" % util.getSizeLabel(self.lastUploadRate), curses.A_BOLD | ulColor)
        
        # graph bounds in KB (uses highest recorded value as max)
        self.addstr(2, 0, "%4s" % str(self.maxDownloadRate[self.updateIntervalIndex] / 1024), dlColor)
        self.addstr(7, 0, "   0", dlColor)
        
        self.addstr(2, 35, "%4s" % str(self.maxUploadRate[self.updateIntervalIndex] / 1024), ulColor)
        self.addstr(7, 35, "   0", ulColor)
        
        # creates bar graph of bandwidth usage over time
        for col in range(BANDWIDTH_GRAPH_COL):
          bytesDownloaded = self.downloadRates[self.updateIntervalIndex][col + 1] / UPDATE_INTERVALS[self.updateIntervalIndex][1]
          colHeight = min(5, 5 * bytesDownloaded / self.maxDownloadRate[self.updateIntervalIndex])
          for row in range(colHeight):
            self.addstr(7 - row, col + 5, " ", curses.A_STANDOUT | dlColor)
        
        for col in range(BANDWIDTH_GRAPH_COL):
          bytesUploaded = self.uploadRates[self.updateIntervalIndex][col + 1] / UPDATE_INTERVALS[self.updateIntervalIndex][1]
          colHeight = min(5, 5 * bytesUploaded / self.maxUploadRate[self.updateIntervalIndex])
          for row in range(colHeight):
            self.addstr(7 - row, col + 40, " ", curses.A_STANDOUT | ulColor)
        
        # provides average dl/ul rates
        if self.tick > 0:
          avgDownload = self.totalDownload / self.tick
          avgUpload = self.totalUpload / self.tick
        else: avgDownload, avgUpload = 0, 0
        self.addstr(8, 1, "avg: %s/sec" % util.getSizeLabel(avgDownload), dlColor)
        self.addstr(8, 36, "avg: %s/sec" % util.getSizeLabel(avgUpload), ulColor)
        
        # accounting stats if enabled
        if self.isAccounting:
          if not self.isPaused and self.isVisible: self._updateAccountingInfo()
          
          if self.accountingInfo:
            status = self.accountingInfo["status"]
            hibernateColor = "green"
            if status == "soft": hibernateColor = "yellow"
            elif status == "hard": hibernateColor = "red"
            
            self.addfstr(10, 0, "<b>Accounting (<%s>%s</%s>)" % (hibernateColor, status, hibernateColor))
            self.addstr(10, 35, "Time to reset: %s" % self.accountingInfo["resetTime"])
            self.addstr(11, 2, "%s / %s" % (self.accountingInfo["read"], self.accountingInfo["readLimit"]), dlColor)
            self.addstr(11, 37, "%s / %s" % (self.accountingInfo["written"], self.accountingInfo["writtenLimit"]), ulColor)
          else:
            self.addfstr(10, 0, "<b>Accounting:</b> Shutting Down...")
        
        self.refresh()
      finally:
        self.lock.release()
  
  def setUpdateInterval(self, intervalIndex):
    """
    Sets the timeframe at which the graph is updated. This throws a ValueError
    if the index isn't within UPDATE_INTERVALS.
    """
    
    if intervalIndex >= 0 and intervalIndex < len(UPDATE_INTERVALS):
      self.updateIntervalIndex = intervalIndex
    else: raise ValueError("%i out of bounds of UPDATE_INTERVALS" % intervalIndex)
  
  def setPaused(self, isPause):
    """
    If true, prevents bandwidth updates from being presented.
    """
    
    if isPause == self.isPaused: return
    self.isPaused = isPause
    if self.isVisible: self._parameterSwap()
  
  def setVisible(self, isVisible):
    """
    Toggles panel visability, hiding if false.
    """
    
    if isVisible == self.isVisible: return
    self.isVisible = isVisible
    
    if self.isVisible: self.height = self.contentHeight
    else: self.height = 0
    
    if not self.isPaused: self._parameterSwap()
  
  def _parameterSwap(self):
    if self.isPaused or not self.isVisible:
      if self.pauseBuffer == None: self.pauseBuffer = BandwidthMonitor(None, None)
      
      # TODO: use a more clever swap using 'active' and 'inactive' instances
      self.pauseBuffer.tick = self.tick
      self.pauseBuffer.lastDownloadRate = self.lastDownloadRate
      self.pauseBuffer.lastUploadRate = self.lastUploadRate
      self.pauseBuffer.maxDownloadRate = list(self.maxDownloadRate)
      self.pauseBuffer.maxUploadRate = list(self.maxUploadRate)
      self.pauseBuffer.downloadRates = copy.deepcopy(self.downloadRates)
      self.pauseBuffer.uploadRates = copy.deepcopy(self.uploadRates)
      self.pauseBuffer.totalDownload = self.totalDownload
      self.pauseBuffer.totalUpload = self.totalUpload
      self.pauseBuffer.bwRate = self.bwRate
      self.pauseBuffer.bwBurst = self.bwBurst
    else:
      self.tick = self.pauseBuffer.tick
      self.lastDownloadRate = self.pauseBuffer.lastDownloadRate
      self.lastUploadRate = self.pauseBuffer.lastUploadRate
      self.maxDownloadRate = self.pauseBuffer.maxDownloadRate
      self.maxUploadRate = self.pauseBuffer.maxUploadRate
      self.downloadRates = self.pauseBuffer.downloadRates
      self.uploadRates = self.pauseBuffer.uploadRates
      self.totalDownload = self.pauseBuffer.totalDownload
      self.totalUpload = self.pauseBuffer.totalUpload
      self.bwRate = self.pauseBuffer.bwRate
      self.bwBurst = self.pauseBuffer.bwBurst
      self.redraw()
  
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
      
      # altzone subtraction converts from gmt to local with respect to DST
      sec = time.mktime(time.strptime(accountingParams["accounting/interval-end"], "%Y-%m-%d %H:%M:%S")) - time.time() - time.altzone
      resetHours = sec / 3600
      sec %= 3600
      resetMin = sec / 60
      sec %= 60
      self.accountingInfo["resetTime"] = "%i:%02i:%02i" % (resetHours, resetMin, sec)
      
      read = int(accountingParams["accounting/bytes"].split(" ")[0])
      written = int(accountingParams["accounting/bytes"].split(" ")[1])
      readLeft = int(accountingParams["accounting/bytes-left"].split(" ")[0])
      writtenLeft = int(accountingParams["accounting/bytes-left"].split(" ")[1])
      
      self.accountingInfo["read"] = util.getSizeLabel(read)
      self.accountingInfo["written"] = util.getSizeLabel(written)
      self.accountingInfo["readLimit"] = util.getSizeLabel(read + readLeft)
      self.accountingInfo["writtenLimit"] = util.getSizeLabel(written + writtenLeft)
    except TorCtl.TorCtlClosed:
      self.accountingInfo = None

