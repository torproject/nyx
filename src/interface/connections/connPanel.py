"""
Listing of the currently established connections tor has made.
"""

import time
import curses
import threading

from interface.connections import listings
from util import connections, enum, log, panel, uiTools

DEFAULT_CONFIG = {}

# listing types
Listing = enum.Enum(("IP", "IP Address"), "HOSTNAME", "FINGERPRINT", "NICKNAME")

class ConnectionPanel(panel.Panel, threading.Thread):
  """
  Listing of connections tor is making, with information correlated against
  the current consensus and other data sources.
  """
  
  def __init__(self, stdscr, config=None):
    panel.Panel.__init__(self, stdscr, "connections", 0)
    threading.Thread.__init__(self)
    self.setDaemon(True)
    
    #self.sortOrdering = DEFAULT_SORT_ORDER
    self._config = dict(DEFAULT_CONFIG)
    if config:
      config.update(self._config)
      
      # TODO: test and add to the sample armrc
      #self.sortOrdering = config.getIntCSV("features.connections.order", self.sortOrdering, 3, 0, 6)
    
    self.scroller = uiTools.Scroller(True)
    self._title = "Connections:" # title line of the panel
    self._connections = []      # last fetched connections
    
    self._lastUpdate = -1       # time the content was last revised
    self._isPaused = True       # prevents updates if true
    self._pauseTime = None      # time when the panel was paused
    self._halt = False          # terminates thread if true
    self._cond = threading.Condition()  # used for pausing the thread
    
    # Last sampling received from the ConnectionResolver, used to detect when
    # it changes.
    self._lastResourceFetch = -1
    
    self.valsLock = threading.RLock()
    
    self._update() # populates initial entries
    
    # TODO: should listen for tor shutdown
  
  def setPaused(self, isPause):
    """
    If true, prevents the panel from updating.
    """
    
    if not self._isPaused == isPause:
      self._isPaused = isPause
      
      if isPause: self._pauseTime = time.time()
      else: self._pauseTime = None
      
      # redraws so the display reflects any changes between the last update
      # and being paused
      self.redraw(True)
  
  def handleKey(self, key):
    self.valsLock.acquire()
    
    if uiTools.isScrollKey(key):
      pageHeight = self.getPreferredSize()[0] - 1
      isChanged = self.scroller.handleKey(key, self._connections, pageHeight)
      if isChanged: self.redraw(True)
    
    self.valsLock.release()
  
  def run(self):
    """
    Keeps connections listing updated, checking for new entries at a set rate.
    """
    
    lastDraw = time.time() - 1
    while not self._halt:
      currentTime = time.time()
      
      if self._isPaused or currentTime - lastDraw < 1:
        self._cond.acquire()
        if not self._halt: self._cond.wait(0.2)
        self._cond.release()
      else:
        # updates content if their's new results, otherwise just redraws
        self._update()
        self.redraw(True)
        lastDraw += 1
  
  def draw(self, width, height):
    self.valsLock.acquire()
    
    # title label with connection counts
    self.addstr(0, 0, self._title, curses.A_STANDOUT)
    
    scrollLoc = self.scroller.getScrollLoc(self._connections, height - 1)
    cursorSelection = self.scroller.getCursorSelection(self._connections)
    
    scrollOffset = 0
    if len(self._connections) > height - 1:
      scrollOffset = 3
      self.addScrollBar(scrollLoc, scrollLoc + height - 1, len(self._connections), 1)
    
    currentTime = self._pauseTime if self._pauseTime else time.time()
    for lineNum in range(scrollLoc, len(self._connections)):
      entry = self._connections[lineNum]
      drawLine = lineNum + 1 - scrollLoc
      
      entryType = entry.getType()
      lineFormat = uiTools.getColor(listings.CATEGORY_COLOR[entryType])
      if entry == cursorSelection: lineFormat |= curses.A_STANDOUT
      
      # Lines are split into three components (prefix, category, and suffix)
      # since the category includes the bold attribute (otherwise, all use
      # lineFormat).
      xLoc = scrollOffset
      
      # prefix (entry data which is largely static, plus the time label)
      entryLabel = entry.getLabel(Listing.IP, width - scrollOffset)
      timeLabel = uiTools.getTimeLabel(currentTime - entry.startTime, 1)
      prefixLabel = "%s%5s (" % (entryLabel, timeLabel)
      
      self.addstr(drawLine, xLoc, prefixLabel, lineFormat)
      xLoc += len(prefixLabel)
      
      # category
      self.addstr(drawLine, xLoc, entryType.upper(), lineFormat | curses.A_BOLD)
      xLoc += len(entryType)
      
      # suffix (ending parentheses plus padding so lines are the same length)
      self.addstr(drawLine, xLoc, ")" + " " * (9 - len(entryType)), lineFormat)
      
      if drawLine >= height: break
    
    self.valsLock.release()
  
  def stop(self):
    """
    Halts further resolutions and terminates the thread.
    """
    
    self._cond.acquire()
    self._halt = True
    self._cond.notifyAll()
    self._cond.release()
  
  def _update(self):
    """
    Fetches the newest resolved connections.
    """
    
    connResolver = connections.getResolver("tor")
    currentResolutionCount = connResolver.getResolutionCount()
    
    if self._lastResourceFetch != currentResolutionCount:
      self.valsLock.acquire()
      currentConnections = connResolver.getConnections()
      newConnections = []
      
      # preserves any ConnectionEntries they already exist
      for conn in self._connections:
        connAttr = (conn.local.getIpAddr(), conn.local.getPort(),
                    conn.foreign.getIpAddr(), conn.foreign.getPort())
        
        if connAttr in currentConnections:
          newConnections.append(conn)
          currentConnections.remove(connAttr)
      
      # add new entries for any additions
      for lIp, lPort, fIp, fPort in currentConnections:
        newConnections.append(listings.ConnectionEntry(lIp, lPort, fIp, fPort))
      
      # if it's changed then sort the results
      #if newConnections != self._connections:
      #  newConnections.sort(key=lambda i: (i.getAll(self.sortOrdering)))
      
      # counts the relays in each of the categories
      categoryTypes = listings.Category.values()
      typeCounts = dict((type, 0) for type in categoryTypes)
      for conn in newConnections: typeCounts[conn.getType()] += 1
      
      # makes labels for all the categories with connections (ie,
      # "21 outbound", "1 control", etc)
      countLabels = []
      
      for category in categoryTypes:
        if typeCounts[category] > 0:
          countLabels.append("%i %s" % (typeCounts[category], category.lower()))
      
      if countLabels: self._title = "Connections (%s):" % ", ".join(countLabels)
      else: self._title = "Connections:"
      
      self._connections = newConnections
      self._lastResourceFetch = currentResolutionCount
      self.valsLock.release()

