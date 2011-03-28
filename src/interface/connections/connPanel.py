"""
Listing of the currently established connections tor has made.
"""

import time
import curses
import threading

from interface.connections import entries, connEntry, circEntry
from util import connections, enum, panel, torTools, uiTools

DEFAULT_CONFIG = {"features.connection.listingType": 0,
                  "features.connection.refreshRate": 5}

# height of the detail panel content, not counting top and bottom border
DETAILS_HEIGHT = 7

# listing types
Listing = enum.Enum(("IP_ADDRESS", "IP Address"), "HOSTNAME", "FINGERPRINT", "NICKNAME")

DEFAULT_SORT_ORDER = (entries.SortAttr.CATEGORY, entries.SortAttr.LISTING, entries.SortAttr.UPTIME)

class ConnectionPanel(panel.Panel, threading.Thread):
  """
  Listing of connections tor is making, with information correlated against
  the current consensus and other data sources.
  """
  
  def __init__(self, stdscr, config=None):
    panel.Panel.__init__(self, stdscr, "connections", 0)
    threading.Thread.__init__(self)
    self.setDaemon(True)
    
    self._sortOrdering = DEFAULT_SORT_ORDER
    self._config = dict(DEFAULT_CONFIG)
    
    if config:
      config.update(self._config, {
        "features.connection.listingType": (0, len(Listing.values()) - 1),
        "features.connection.refreshRate": 1})
      
      sortFields = entries.SortAttr.values()
      customOrdering = config.getIntCSV("features.connection.order", None, 3, 0, len(sortFields))
      
      if customOrdering:
        self._sortOrdering = [sortFields[i] for i in customOrdering]
    
    self._listingType = Listing.values()[self._config["features.connection.listingType"]]
    self._scroller = uiTools.Scroller(True)
    self._title = "Connections:" # title line of the panel
    self._entries = []          # last fetched display entries
    self._entryLines = []       # individual lines rendered from the entries listing
    self._showDetails = False   # presents the details panel if true
    
    self._lastUpdate = -1       # time the content was last revised
    self._isPaused = True       # prevents updates if true
    self._pauseTime = None      # time when the panel was paused
    self._halt = False          # terminates thread if true
    self._cond = threading.Condition()  # used for pausing the thread
    self.valsLock = threading.RLock()
    
    # Last sampling received from the ConnectionResolver, used to detect when
    # it changes.
    self._lastResourceFetch = -1
    
    # resolver for the command/pid associated with SOCKS and CONTROL connections
    self._appResolver = connections.AppResolver("arm")
    
    # rate limits appResolver queries to once per update
    self.appResolveSinceUpdate = False
    
    self._update()            # populates initial entries
    self._resolveApps(False)  # resolves initial SOCKS and CONTROL applications
    
    # mark the initially exitsing connection uptimes as being estimates
    for entry in self._entries:
      if isinstance(entry, connEntry.ConnectionEntry):
        entry.getLines()[0].isInitialConnection = True
    
    # TODO: should listen for tor shutdown
    # TODO: hasn't yet had its pausing functionality tested (for instance, the
    # key handler still accepts events when paused)
  
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
  
  def setSortOrder(self, ordering = None):
    """
    Sets the connection attributes we're sorting by and resorts the contents.
    
    Arguments:
      ordering - new ordering, if undefined then this resorts with the last
                 set ordering
    """
    
    self.valsLock.acquire()
    if ordering: self._sortOrdering = ordering
    self._entries.sort(key=lambda i: (i.getSortValues(self._sortOrdering, self._listingType)))
    
    self._entryLines = []
    for entry in self._entries:
      self._entryLines += entry.getLines()
    self.valsLock.release()
  
  def setListingType(self, listingType):
    """
    Sets the priority information presented by the panel.
    
    Arguments:
      listingType - Listing instance for the primary information to be shown
    """
    
    self.valsLock.acquire()
    self._listingType = listingType
    
    # if we're sorting by the listing then we need to resort
    if entries.SortAttr.LISTING in self._sortOrdering:
      self.setSortOrder()
    
    self.valsLock.release()
  
  def handleKey(self, key):
    self.valsLock.acquire()
    
    if uiTools.isScrollKey(key):
      pageHeight = self.getPreferredSize()[0] - 1
      if self._showDetails: pageHeight -= (DETAILS_HEIGHT + 1)
      isChanged = self._scroller.handleKey(key, self._entryLines, pageHeight)
      if isChanged: self.redraw(True)
    elif uiTools.isSelectionKey(key):
      self._showDetails = not self._showDetails
      self.redraw(True)
    
    self.valsLock.release()
  
  def run(self):
    """
    Keeps connections listing updated, checking for new entries at a set rate.
    """
    
    lastDraw = time.time() - 1
    while not self._halt:
      currentTime = time.time()
      
      if self._isPaused or currentTime - lastDraw < self._config["features.connection.refreshRate"]:
        self._cond.acquire()
        if not self._halt: self._cond.wait(0.2)
        self._cond.release()
      else:
        # updates content if their's new results, otherwise just redraws
        self._update()
        self.redraw(True)
        
        # we may have missed multiple updates due to being paused, showing
        # another panel, etc so lastDraw might need to jump multiple ticks
        drawTicks = (time.time() - lastDraw) / self._config["features.connection.refreshRate"]
        lastDraw += self._config["features.connection.refreshRate"] * drawTicks
  
  def draw(self, width, height):
    self.valsLock.acquire()
    
    # extra line when showing the detail panel is for the bottom border
    detailPanelOffset = DETAILS_HEIGHT + 1 if self._showDetails else 0
    isScrollbarVisible = len(self._entryLines) > height - detailPanelOffset - 1
    
    scrollLoc = self._scroller.getScrollLoc(self._entryLines, height - detailPanelOffset - 1)
    cursorSelection = self._scroller.getCursorSelection(self._entryLines)
    
    # draws the detail panel if currently displaying it
    if self._showDetails:
      # This is a solid border unless the scrollbar is visible, in which case a
      # 'T' pipe connects the border to the bar.
      uiTools.drawBox(self, 0, 0, width, DETAILS_HEIGHT + 2)
      if isScrollbarVisible: self.addch(DETAILS_HEIGHT + 1, 1, curses.ACS_TTEE)
      
      drawEntries = cursorSelection.getDetails(width)
      for i in range(min(len(drawEntries), DETAILS_HEIGHT)):
        drawEntries[i].render(self, 1 + i, 2)
    
    # title label with connection counts
    title = "Connection Details:" if self._showDetails else self._title
    self.addstr(0, 0, title, curses.A_STANDOUT)
    
    scrollOffset = 1
    if isScrollbarVisible:
      scrollOffset = 3
      self.addScrollBar(scrollLoc, scrollLoc + height - detailPanelOffset - 1, len(self._entryLines), 1 + detailPanelOffset)
    
    currentTime = self._pauseTime if self._pauseTime else time.time()
    for lineNum in range(scrollLoc, len(self._entryLines)):
      entryLine = self._entryLines[lineNum]
      
      # if this is an unresolved SOCKS or CONTROL entry then queue up
      # resolution for the applicaitions they belong to
      if isinstance(entryLine, connEntry.ConnectionLine) and entryLine.isUnresolvedApp():
        self._resolveApps()
      
      # hilighting if this is the selected line
      extraFormat = curses.A_STANDOUT if entryLine == cursorSelection else curses.A_NORMAL
      
      drawEntry = entryLine.getListingEntry(width - scrollOffset, currentTime, self._listingType)
      drawLine = lineNum + detailPanelOffset + 1 - scrollLoc
      drawEntry.render(self, drawLine, scrollOffset, extraFormat)
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
    self.appResolveSinceUpdate = False
    
    if self._lastResourceFetch != currentResolutionCount:
      self.valsLock.acquire()
      
      newEntries = [] # the new results we'll display
      
      # Fetches new connections and client circuits...
      # newConnections  [(local ip, local port, foreign ip, foreign port)...]
      # newCircuits     {circuitID => (status, purpose, path)...}
      
      newConnections = connResolver.getConnections()
      newCircuits = {}
      
      for circuitID, status, purpose, path in torTools.getConn().getCircuits():
        # Skips established single-hop circuits (these are for directory
        # fetches, not client circuits)
        if not (status == "BUILT" and len(path) == 1):
          newCircuits[circuitID] = (status, purpose, path)
      
      # Populates newEntries with any of our old entries that still exist.
      # This is both for performance and to keep from resetting the uptime
      # attributes. Note that CircEntries are a ConnectionEntry subclass so
      # we need to check for them first.
      
      for oldEntry in self._entries:
        if isinstance(oldEntry, circEntry.CircEntry):
          newEntry = newCircuits.get(oldEntry.circuitID)
          
          if newEntry:
            oldEntry.update(newEntry[0], newEntry[2])
            newEntries.append(oldEntry)
            del newCircuits[oldEntry.circuitID]
        elif isinstance(oldEntry, connEntry.ConnectionEntry):
          connLine = oldEntry.getLines()[0]
          connAttr = (connLine.local.getIpAddr(), connLine.local.getPort(),
                      connLine.foreign.getIpAddr(), connLine.foreign.getPort())
          
          if connAttr in newConnections:
            newEntries.append(oldEntry)
            newConnections.remove(connAttr)
      
      # Reset any display attributes for the entries we're keeping
      for entry in newEntries: entry.resetDisplay()
      
      # Adds any new connection and circuit entries.
      for lIp, lPort, fIp, fPort in newConnections:
        newConnEntry = connEntry.ConnectionEntry(lIp, lPort, fIp, fPort)
        if newConnEntry.getLines()[0].getType() != connEntry.Category.CIRCUIT:
          newEntries.append(newConnEntry)
      
      for circuitID in newCircuits:
        status, purpose, path = newCircuits[circuitID]
        newEntries.append(circEntry.CircEntry(circuitID, status, purpose, path))
      
      # Counts the relays in each of the categories. This also flushes the
      # type cache for all of the connections (in case its changed since last
      # fetched).
      
      categoryTypes = connEntry.Category.values()
      typeCounts = dict((type, 0) for type in categoryTypes)
      for entry in newEntries:
        if isinstance(entry, connEntry.ConnectionEntry):
          typeCounts[entry.getLines()[0].getType()] += 1
        elif isinstance(entry, circEntry.CircEntry):
          typeCounts[connEntry.Category.CIRCUIT] += 1
      
      # makes labels for all the categories with connections (ie,
      # "21 outbound", "1 control", etc)
      countLabels = []
      
      for category in categoryTypes:
        if typeCounts[category] > 0:
          countLabels.append("%i %s" % (typeCounts[category], category.lower()))
      
      if countLabels: self._title = "Connections (%s):" % ", ".join(countLabels)
      else: self._title = "Connections:"
      
      self._entries = newEntries
      
      self._entryLines = []
      for entry in self._entries:
        self._entryLines += entry.getLines()
      
      self.setSortOrder()
      self._lastResourceFetch = currentResolutionCount
      self.valsLock.release()
  
  def _resolveApps(self, flagQuery = True):
    """
    Triggers an asynchronous query for all unresolved SOCKS and CONTROL
    entries.
    
    Arguments:
      flagQuery - sets a flag to prevent further call from being respected
                  until the next update if true
    """
    
    if self.appResolveSinceUpdate: return
    
    # fetch the unresolved SOCKS and CONTROL lines
    unresolvedLines = []
    
    for line in self._entryLines:
      if isinstance(line, connEntry.ConnectionLine) and line.isUnresolvedApp():
        unresolvedLines.append(line)
    
    # Queue up resolution for the unresolved ports (skips if it's still working
    # on the last query).
    if not self._appResolver.isResolving:
      self._appResolver.resolve([line.foreign.getPort() for line in unresolvedLines])
    
    # The application resolver might have given up querying (for instance, if
    # the lsof lookups aren't working on this platform or lacks permissions).
    # The isAppResolving flag lets the unresolved entries indicate if there's
    # a lookup in progress for them or not.
    
    for line in unresolvedLines:
      line.isAppResolving = self._appResolver.isResolving
    
    # Fetches results. If the query finishes quickly then this is what we just
    # asked for, otherwise these belong to the last resolution.
    appResults = self._appResolver.getResults(0.02)
    
    for line in unresolvedLines:
      linePort = line.foreign.getPort()
      
      if linePort in appResults:
        # sets application attributes if there's a result with this as the
        # inbound port
        for inboundPort, outboundPort, cmd, pid in appResults[linePort]:
          if linePort == inboundPort:
            line.appName = cmd
            line.appPid = pid
            line.isAppResolving = False
    
    if flagQuery:
      self.appResolveSinceUpdate = True

