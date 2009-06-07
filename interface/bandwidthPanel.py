#!/usr/bin/env python
# bandwidthPanel.py -- Resources related to monitoring Tor bandwidth usage.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import time
import curses
from TorCtl import TorCtl

import util

BANDWIDTH_GRAPH_SAMPLES = 5         # seconds of data used for a bar in the graph
BANDWIDTH_GRAPH_COL = 30            # columns of data in graph
BANDWIDTH_GRAPH_COLOR_DL = "green"  # download section color
BANDWIDTH_GRAPH_COLOR_UL = "cyan"   # upload section color

class BandwidthMonitor(TorCtl.PostEventListener, util.Panel):
  """
  Tor event listener, taking bandwidth sampling and drawing bar graph. This is
  updated every second by the BW events and graph samples are spaced at
  BANDWIDTH_GRAPH_SAMPLES second intervals.
  """
  
  def __init__(self, lock, conn):
    TorCtl.PostEventListener.__init__(self)
    self.isAccounting = conn.get_info('accounting/enabled')['accounting/enabled'] == '1'
    height = 12 if self.isAccounting else 9
    util.Panel.__init__(self, lock, height)
    
    self.conn = conn              # Tor control port connection
    self.tick = 0                 # number of updates performed
    self.lastDownloadRate = 0     # most recently sampled rates
    self.lastUploadRate = 0
    self.maxDownloadRate = 1      # max rates seen, used to determine graph bounds
    self.maxUploadRate = 1
    self.isPaused = False
    self.pauseBuffer = None       # mirror instance used to track updates when paused
    
    # graphed download (read) and upload (write) rates - first index accumulator
    self.downloadRates = [0] * (BANDWIDTH_GRAPH_COL + 1)
    self.uploadRates = [0] * (BANDWIDTH_GRAPH_COL + 1)
    
    # retrieves static stats for label
    if conn:
      bwStats = conn.get_option(['BandwidthRate', 'BandwidthBurst'])
      self.bwRate = util.getSizeLabel(int(bwStats[0][1]))
      self.bwBurst = util.getSizeLabel(int(bwStats[1][1]))
    else: self.bwRate, self.bwBurst = -1, -1
  
  def bandwidth_event(self, event):
    if self.isPaused: self.pauseBuffer.bandwidth_event(event)
    else:
      self.lastDownloadRate = event.read
      self.lastUploadRate = event.written
      
      self.downloadRates[0] += event.read
      self.uploadRates[0] += event.written
      
      self.tick += 1
      if self.tick % BANDWIDTH_GRAPH_SAMPLES == 0:
        self.maxDownloadRate = max(self.maxDownloadRate, self.downloadRates[0])
        self.downloadRates.insert(0, 0)
        del self.downloadRates[BANDWIDTH_GRAPH_COL + 1:]
        
        self.maxUploadRate = max(self.maxUploadRate, self.uploadRates[0])
        self.uploadRates.insert(0, 0)
        del self.uploadRates[BANDWIDTH_GRAPH_COL + 1:]
      
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
        
        self.addstr(0, 0, labelContents, util.LABEL_ATTR)
        
        # current numeric measures
        self.addstr(1, 0, "Downloaded (%s/sec):" % util.getSizeLabel(self.lastDownloadRate), curses.A_BOLD | dlColor)
        self.addstr(1, 35, "Uploaded (%s/sec):" % util.getSizeLabel(self.lastUploadRate), curses.A_BOLD | ulColor)
        
        # graph bounds in KB (uses highest recorded value as max)
        self.addstr(2, 0, "%4s" % str(self.maxDownloadRate / 1024 / BANDWIDTH_GRAPH_SAMPLES), dlColor)
        self.addstr(7, 0, "   0", dlColor)
        
        self.addstr(2, 35, "%4s" % str(self.maxUploadRate / 1024 / BANDWIDTH_GRAPH_SAMPLES), ulColor)
        self.addstr(7, 35, "   0", ulColor)
        
        # creates bar graph of bandwidth usage over time
        for col in range(BANDWIDTH_GRAPH_COL):
          bytesDownloaded = self.downloadRates[col + 1]
          colHeight = min(5, 5 * bytesDownloaded / self.maxDownloadRate)
          for row in range(colHeight):
            self.addstr(7 - row, col + 5, " ", curses.A_STANDOUT | dlColor)
        
        for col in range(BANDWIDTH_GRAPH_COL):
          bytesUploaded = self.uploadRates[col + 1]
          colHeight = min(5, 5 * bytesUploaded / self.maxUploadRate)
          for row in range(colHeight):
            self.addstr(7 - row, col + 40, " ", curses.A_STANDOUT | ulColor)
        
        if self.isAccounting:
          try:
            accountingParams = self.conn.get_info(["accounting/hibernating", "accounting/bytes", "accounting/bytes-left", "accounting/interval-end"])
            
            hibernateStr = accountingParams["accounting/hibernating"]
            hibernateColor = "green"
            if hibernateStr == "soft": hibernateColor = "yellow"
            elif hibernateStr == "hard": hibernateColor = "red"
            
            self.addstr(9, 0, "Accounting (", curses.A_BOLD)
            self.addstr(9, 12, hibernateStr, curses.A_BOLD | util.getColor(hibernateColor))
            self.addstr(9, 12 + len(hibernateStr), "):", curses.A_BOLD)
            
            sec = time.mktime(time.strptime(accountingParams["accounting/interval-end"], "%Y-%m-%d %H:%M:%S")) - time.time()
            resetHours = sec / 3600
            sec %= 3600
            resetMin = sec / 60
            sec %= 60
            
            self.addstr(9, 35, "Time to reset: %i:%02i:%02i" % (resetHours, resetMin, sec))
            
            read = util.getSizeLabel(int(accountingParams["accounting/bytes"].split(" ")[0]))
            written = util.getSizeLabel(int(accountingParams["accounting/bytes"].split(" ")[1]))
            limit = util.getSizeLabel(int(accountingParams["accounting/bytes"].split(" ")[0]) + int(accountingParams["accounting/bytes-left"].split(" ")[0]))
            
            self.addstr(10, 2, "%s / %s" % (read, limit), dlColor)
            self.addstr(10, 37, "%s / %s" % (written, limit), ulColor)
            
          except TorCtl.TorCtlClosed:
            self.addstr(9, 0, "Accounting:", curses.A_BOLD)
            self.addstr(9, 12, "Shutting Down...")
        
        self.refresh()
      finally:
        self.lock.release()
  
  def setPaused(self, isPause):
    """
    If true, prevents bandwidth updates from being presented.
    """
    
    if isPause == self.isPaused: return
    
    self.isPaused = isPause
    if self.isPaused:
      if self.pauseBuffer == None: self.pauseBuffer = BandwidthMonitor(None, None)
      
      self.pauseBuffer.tick = self.tick
      self.pauseBuffer.lastDownloadRate = self.lastDownloadRate
      self.pauseBuffer.lastuploadRate = self.lastUploadRate
      self.pauseBuffer.maxDownloadRate = self.maxDownloadRate
      self.pauseBuffer.maxUploadRate = self.maxUploadRate
      self.pauseBuffer.downloadRates = list(self.downloadRates)
      self.pauseBuffer.uploadRates = list(self.uploadRates)
    else:
      self.tick = self.pauseBuffer.tick
      self.lastDownloadRate = self.pauseBuffer.lastDownloadRate
      self.lastUploadRate = self.pauseBuffer.lastuploadRate
      self.maxDownloadRate = self.pauseBuffer.maxDownloadRate
      self.maxUploadRate = self.pauseBuffer.maxUploadRate
      self.downloadRates = self.pauseBuffer.downloadRates
      self.uploadRates = self.pauseBuffer.uploadRates
      self.redraw()

