#!/usr/bin/env python
# bandwidthMonitor.py -- Resources related to monitoring Tor bandwidth usage.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import curses
import controller
from TorCtl import TorCtl

BANDWIDTH_GRAPH_SAMPLES = 5         # seconds of data used for a bar in the graph
BANDWIDTH_GRAPH_COL = 30            # columns of data in graph
BANDWIDTH_GRAPH_COLOR_DL = "green"  # download section color
BANDWIDTH_GRAPH_COLOR_UL = "cyan"   # upload section color

def drawBandwidthLabel(screen, staticInfo, maxX):
  """ Draws bandwidth label text (drops stats if not enough room). """
  rateLabel = controller.getSizeLabel(int(staticInfo["BandwidthRate"]))
  burstLabel = controller.getSizeLabel(int(staticInfo["BandwidthBurst"]))
  labelContents = "Bandwidth (cap: %s, burst: %s):" % (rateLabel, burstLabel)
  if maxX < len(labelContents):
    labelContents = "%s):" % labelContents[:labelContents.find(",")]  # removes burst measure
    if x < len(labelContents): labelContents = "Bandwidth:"           # removes both
  
  screen.erase()
  screen.addstr(0, 0, labelContents[:maxX - 1], controller.LABEL_ATTR)
  screen.refresh()

class BandwidthMonitor(TorCtl.PostEventListener):
  """
  Tor event listener, taking bandwidth sampling and drawing bar graph. This is
  updated every second by the BW events and graph samples are spaced at
  BANDWIDTH_GRAPH_SAMPLES second intervals.
  """
  
  def __init__(self, bandwidthScreen, cursesLock):
    TorCtl.PostEventListener.__init__(self)
    self.tick = 0                           # number of updates performed
    self.bandwidthScreen = bandwidthScreen  # curses window where bandwidth's displayed
    self.lastDownloadRate = 0               # most recently sampled rates
    self.lastUploadRate = 0
    self.maxDownloadRate = 1                # max rates seen, used to determine graph bounds
    self.maxUploadRate = 1
    self.isPaused = False
    self.pauseBuffer = None                 # mirror instance used to track updates when paused
    self.cursesLock = cursesLock            # lock to safely use bandwidthScreen
    
    # graphed download (read) and upload (write) rates - first index accumulator
    self.downloadRates = [0] * (BANDWIDTH_GRAPH_COL + 1)
    self.uploadRates = [0] * (BANDWIDTH_GRAPH_COL + 1)
    
  def bandwidth_event(self, event):
    if self.isPaused:
      self.pauseBuffer.bandwidth_event(event)
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
    if self.bandwidthScreen:
      if not self.cursesLock.acquire(False): return
      try:
        self.bandwidthScreen.erase()
        y, x = self.bandwidthScreen.getmaxyx()
        dlColor = controller.COLOR_ATTR[BANDWIDTH_GRAPH_COLOR_DL]
        ulColor = controller.COLOR_ATTR[BANDWIDTH_GRAPH_COLOR_UL]
        
        # current numeric measures
        self.bandwidthScreen.addstr(0, 0, ("Downloaded (%s/sec):" % controller.getSizeLabel(self.lastDownloadRate))[:x - 1], curses.A_BOLD | dlColor)
        if x > 35: self.bandwidthScreen.addstr(0, 35, ("Uploaded (%s/sec):" % controller.getSizeLabel(self.lastUploadRate))[:x - 36], curses.A_BOLD | ulColor)
        
        # graph bounds in KB (uses highest recorded value as max)
        if y > 1:self.bandwidthScreen.addstr(1, 0, ("%4s" % str(self.maxDownloadRate / 1024 / BANDWIDTH_GRAPH_SAMPLES))[:x - 1], dlColor)
        if y > 6: self.bandwidthScreen.addstr(6, 0, "   0"[:x - 1], dlColor)
        
        if x > 35:
          if y > 1: self.bandwidthScreen.addstr(1, 35, ("%4s" % str(self.maxUploadRate / 1024 / BANDWIDTH_GRAPH_SAMPLES))[:x - 36], ulColor)
          if y > 6: self.bandwidthScreen.addstr(6, 35, "   0"[:x - 36], ulColor)
        
        # creates bar graph of bandwidth usage over time
        for col in range(BANDWIDTH_GRAPH_COL):
          if col > x - 8: break
          bytesDownloaded = self.downloadRates[col + 1]
          colHeight = min(5, 5 * bytesDownloaded / self.maxDownloadRate)
          for row in range(colHeight):
            if y > (6 - row): self.bandwidthScreen.addstr(6 - row, col + 5, " ", curses.A_STANDOUT | dlColor)
        
        for col in range(BANDWIDTH_GRAPH_COL):
          if col > x - 42: break
          bytesUploaded = self.uploadRates[col + 1]
          colHeight = min(5, 5 * bytesUploaded / self.maxUploadRate)
          for row in range(colHeight):
            if y > (6 - row): self.bandwidthScreen.addstr(6 - row, col + 40, " ", curses.A_STANDOUT | ulColor)
          
        self.bandwidthScreen.refresh()
      finally:
        self.cursesLock.release()
  
  def setPaused(self, isPause):
    """
    If true, prevents bandwidth updates from being presented.
    """
    
    if isPause == self.isPaused: return
    
    self.isPaused = isPause
    if self.isPaused:
      if self.pauseBuffer == None:
        self.pauseBuffer = BandwidthMonitor(None, None)
      
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

