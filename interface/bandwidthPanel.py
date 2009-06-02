#!/usr/bin/env python
# bandwidthPanel.py -- Resources related to monitoring Tor bandwidth usage.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import curses
from TorCtl import TorCtl

import util

BANDWIDTH_GRAPH_SAMPLES = 5         # seconds of data used for a bar in the graph
BANDWIDTH_GRAPH_COL = 30            # columns of data in graph
BANDWIDTH_GRAPH_COLOR_DL = "green"  # download section color
BANDWIDTH_GRAPH_COLOR_UL = "cyan"   # upload section color

def drawBandwidthLabel(scr, staticInfo):
  """ Draws bandwidth label text (drops stats if not enough room). """
  scr.clear()
  maxX = scr.maxX
  
  rateLabel = util.getSizeLabel(int(staticInfo["BandwidthRate"]))
  burstLabel = util.getSizeLabel(int(staticInfo["BandwidthBurst"]))
  labelContents = "Bandwidth (cap: %s, burst: %s):" % (rateLabel, burstLabel)
  if maxX < len(labelContents):
    labelContents = "%s):" % labelContents[:labelContents.find(",")]  # removes burst measure
    if maxX < len(labelContents): labelContents = "Bandwidth:"           # removes both
  
  scr.addstr(0, 0, labelContents, util.LABEL_ATTR)
  scr.refresh()

class BandwidthMonitor(TorCtl.PostEventListener):
  """
  Tor event listener, taking bandwidth sampling and drawing bar graph. This is
  updated every second by the BW events and graph samples are spaced at
  BANDWIDTH_GRAPH_SAMPLES second intervals.
  """
  
  def __init__(self, scr):
    TorCtl.PostEventListener.__init__(self)
    self.scr = scr                # associated subwindow
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
      
      self.refreshDisplay()
  
  def refreshDisplay(self):
    """ Redraws bandwidth panel. """
    # doesn't draw if headless (indicating that the instance is for a pause buffer)
    if self.scr:
      if not self.scr.lock.acquire(False): return
      try:
        self.scr.clear()
        dlColor = util.getColor(BANDWIDTH_GRAPH_COLOR_DL)
        ulColor = util.getColor(BANDWIDTH_GRAPH_COLOR_UL)
        
        # current numeric measures
        self.scr.addstr(0, 0, "Downloaded (%s/sec):" % util.getSizeLabel(self.lastDownloadRate), curses.A_BOLD | dlColor)
        self.scr.addstr(0, 35, "Uploaded (%s/sec):" % util.getSizeLabel(self.lastUploadRate), curses.A_BOLD | ulColor)
        
        # graph bounds in KB (uses highest recorded value as max)
        self.scr.addstr(1, 0, "%4s" % str(self.maxDownloadRate / 1024 / BANDWIDTH_GRAPH_SAMPLES), dlColor)
        self.scr.addstr(6, 0, "   0", dlColor)
        
        self.scr.addstr(1, 35, "%4s" % str(self.maxUploadRate / 1024 / BANDWIDTH_GRAPH_SAMPLES), ulColor)
        self.scr.addstr(6, 35, "   0", ulColor)
        
        # creates bar graph of bandwidth usage over time
        for col in range(BANDWIDTH_GRAPH_COL):
          bytesDownloaded = self.downloadRates[col + 1]
          colHeight = min(5, 5 * bytesDownloaded / self.maxDownloadRate)
          for row in range(colHeight):
            self.scr.addstr(6 - row, col + 5, " ", curses.A_STANDOUT | dlColor)
        
        for col in range(BANDWIDTH_GRAPH_COL):
          bytesUploaded = self.uploadRates[col + 1]
          colHeight = min(5, 5 * bytesUploaded / self.maxUploadRate)
          for row in range(colHeight):
            self.scr.addstr(6 - row, col + 40, " ", curses.A_STANDOUT | ulColor)
        
        self.scr.refresh()
      finally:
        self.scr.lock.release()
  
  def setPaused(self, isPause):
    """
    If true, prevents bandwidth updates from being presented.
    """
    
    if isPause == self.isPaused: return
    
    self.isPaused = isPause
    if self.isPaused:
      if self.pauseBuffer == None: self.pauseBuffer = BandwidthMonitor(None)
      
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
      self.refreshDisplay()

