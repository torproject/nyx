"""
Listing of the currently established connections tor has made.
"""

import re
import time
import curses
import threading

import cli.popups

from cli.connections import countPopup, descriptorPopup, entries, connEntry, circEntry
from util import connections, enum, panel, torTools, uiTools

DEFAULT_CONFIG = {"features.connection.resolveApps": True,
                  "features.connection.listingType": 0,
                  "features.connection.refreshRate": 5,
                  "features.connection.showIps": True}

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
      
      # defaults our listing selection to fingerprints if ip address
      # displaying is disabled
      if not self._config["features.connection.showIps"] and self._config["features.connection.listingType"] == 0:
        self._config["features.connection.listingType"] = 2
      
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
    self._isTorRunning = True   # indicates if tor is currently running or not
    self._haltTime = None       # time when tor was stopped
    self._halt = False          # terminates thread if true
    self._cond = threading.Condition()  # used for pausing the thread
    self.valsLock = threading.RLock()
    
    # Tracks exiting port and client country statistics
    self._clientLocaleUsage = {}
    self._exitPortUsage = {}
    
    # If we're a bridge and been running over a day then prepopulates with the
    # last day's clients.
    
    conn = torTools.getConn()
    bridgeClients = conn.getInfo("status/clients-seen")
    
    if bridgeClients:
      # Response has a couple arguments...
      # TimeStarted="2011-08-17 15:50:49" CountrySummary=us=16,de=8,uk=8
      
      countrySummary = None
      for arg in bridgeClients.split():
        if arg.startswith("CountrySummary="):
          countrySummary = arg[15:]
          break
      
      if countrySummary:
        for entry in countrySummary.split(","):
          if re.match("^..=[0-9]+$", entry):
            locale, count = entry.split("=", 1)
            self._clientLocaleUsage[locale] = int(count)
    
    # Last sampling received from the ConnectionResolver, used to detect when
    # it changes.
    self._lastResourceFetch = -1
    
    # resolver for the command/pid associated with SOCKS, HIDDEN, and CONTROL connections
    self._appResolver = connections.AppResolver("arm")
    
    # rate limits appResolver queries to once per update
    self.appResolveSinceUpdate = False
    
    # mark the initially exitsing connection uptimes as being estimates
    for entry in self._entries:
      if isinstance(entry, connEntry.ConnectionEntry):
        entry.getLines()[0].isInitialConnection = True
    
    # listens for when tor stops so we know to stop reflecting changes
    conn.addStatusListener(self.torStateListener)
  
  def torStateListener(self, conn, eventType):
    """
    Freezes the connection contents when Tor stops.
    
    Arguments:
      conn      - tor controller
      eventType - type of event detected
    """
    
    self._isTorRunning = eventType in (torTools.State.INIT, torTools.State.RESET)
    
    if self._isTorRunning: self._haltTime = None
    else: self._haltTime = time.time()
    
    self.redraw(True)
  
  def getPauseTime(self):
    """
    Provides the time Tor stopped if it isn't running. Otherwise this is the
    time we were last paused.
    """
    
    if self._haltTime: return self._haltTime
    else: return panel.Panel.getPauseTime(self)
  
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
  
  def getListingType(self):
    """
    Provides the priority content we list connections by.
    """
    
    return self._listingType
  
  def setListingType(self, listingType):
    """
    Sets the priority information presented by the panel.
    
    Arguments:
      listingType - Listing instance for the primary information to be shown
    """
    
    if self._listingType == listingType: return
    
    self.valsLock.acquire()
    self._listingType = listingType
    
    # if we're sorting by the listing then we need to resort
    if entries.SortAttr.LISTING in self._sortOrdering:
      self.setSortOrder()
    
    self.valsLock.release()
  
  def isClientsAllowed(self):
    """
    True if client connections are permissable, false otherwise.
    """
    
    conn = torTools.getConn()
    return "Guard" in conn.getMyFlags([]) or conn.getOption("BridgeRelay") == "1"
  
  def isExitsAllowed(self):
    """
    True if exit connections are permissable, false otherwise.
    """
    
    policy = torTools.getConn().getExitPolicy()
    return policy and policy.isExitingAllowed()
  
  def showSortDialog(self):
    """
    Provides the sort dialog for our connections.
    """
    
    # set ordering for connection options
    titleLabel = "Connection Ordering:"
    options = entries.SortAttr.values()
    oldSelection = self._sortOrdering
    optionColors = dict([(attr, entries.SORT_COLORS[attr]) for attr in options])
    results = cli.popups.showSortDialog(titleLabel, options, oldSelection, optionColors)
    if results: self.setSortOrder(results)
  
  def handleKey(self, key):
    self.valsLock.acquire()
    
    isKeystrokeConsumed = True
    if uiTools.isScrollKey(key):
      pageHeight = self.getPreferredSize()[0] - 1
      if self._showDetails: pageHeight -= (DETAILS_HEIGHT + 1)
      isChanged = self._scroller.handleKey(key, self._entryLines, pageHeight)
      if isChanged: self.redraw(True)
    elif uiTools.isSelectionKey(key):
      self._showDetails = not self._showDetails
      self.redraw(True)
    elif key == ord('s') or key == ord('S'):
      self.showSortDialog()
    elif key == ord('u') or key == ord('U'):
      # provides a menu to pick the connection resolver
      title = "Resolver Util:"
      options = ["auto"] + connections.Resolver.values()
      connResolver = connections.getResolver("tor")
      
      currentOverwrite = connResolver.overwriteResolver
      if currentOverwrite == None: oldSelection = 0
      else: oldSelection = options.index(currentOverwrite)
      
      selection = cli.popups.showMenu(title, options, oldSelection)
      
      # applies new setting
      if selection != -1:
        selectedOption = options[selection] if selection != 0 else None
        connResolver.overwriteResolver = selectedOption
    elif key == ord('l') or key == ord('L'):
      # provides a menu to pick the primary information we list connections by
      title = "List By:"
      options = entries.ListingType.values()
      
      # dropping the HOSTNAME listing type until we support displaying that content
      options.remove(cli.connections.entries.ListingType.HOSTNAME)
      
      oldSelection = options.index(self._listingType)
      selection = cli.popups.showMenu(title, options, oldSelection)
      
      # applies new setting
      if selection != -1: self.setListingType(options[selection])
    elif key == ord('d') or key == ord('D'):
      # presents popup for raw consensus data
      descriptorPopup.showDescriptorPopup(self)
    elif (key == ord('c') or key == ord('C')) and self.isClientsAllowed():
      countPopup.showCountDialog(countPopup.CountType.CLIENT_LOCALE, self._clientLocaleUsage)
    elif (key == ord('e') or key == ord('E')) and self.isExitsAllowed():
      countPopup.showCountDialog(countPopup.CountType.EXIT_PORT, self._exitPortUsage)
    else: isKeystrokeConsumed = False
    
    self.valsLock.release()
    return isKeystrokeConsumed
  
  def run(self):
    """
    Keeps connections listing updated, checking for new entries at a set rate.
    """
    
    lastDraw = time.time() - 1
    
    # Fetches out initial connection results. The wait is so this doesn't
    # run during arm's interface initialization (otherwise there's a
    # noticeable pause before the first redraw).
    self._cond.acquire()
    self._cond.wait(0.2)
    self._cond.release()
    self._update()            # populates initial entries
    self._resolveApps(False)  # resolves initial applications
    
    while not self._halt:
      currentTime = time.time()
      
      if self.isPaused() or not self._isTorRunning or currentTime - lastDraw < self._config["features.connection.refreshRate"]:
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
  
  def getHelp(self):
    resolverUtil = connections.getResolver("tor").overwriteResolver
    if resolverUtil == None: resolverUtil = "auto"
    
    options = []
    options.append(("up arrow", "scroll up a line", None))
    options.append(("down arrow", "scroll down a line", None))
    options.append(("page up", "scroll up a page", None))
    options.append(("page down", "scroll down a page", None))
    options.append(("enter", "show connection details", None))
    options.append(("d", "raw consensus descriptor", None))
    
    if self.isClientsAllowed():
      options.append(("c", "client locale usage summary", None))
    
    if self.isExitsAllowed():
      options.append(("e", "exit port usage summary", None))
    
    options.append(("l", "listed identity", self._listingType.lower()))
    options.append(("s", "sort ordering", None))
    options.append(("u", "resolving utility", resolverUtil))
    return options
  
  def getSelection(self):
    """
    Provides the currently selected connection entry.
    """
    
    return self._scroller.getCursorSelection(self._entryLines)
  
  def draw(self, width, height):
    self.valsLock.acquire()
    
    # if we don't have any contents then refuse to show details
    if not self._entries: self._showDetails = False
    
    # extra line when showing the detail panel is for the bottom border
    detailPanelOffset = DETAILS_HEIGHT + 1 if self._showDetails else 0
    isScrollbarVisible = len(self._entryLines) > height - detailPanelOffset - 1
    
    scrollLoc = self._scroller.getScrollLoc(self._entryLines, height - detailPanelOffset - 1)
    cursorSelection = self.getSelection()
    
    # draws the detail panel if currently displaying it
    if self._showDetails and cursorSelection:
      # This is a solid border unless the scrollbar is visible, in which case a
      # 'T' pipe connects the border to the bar.
      uiTools.drawBox(self, 0, 0, width, DETAILS_HEIGHT + 2)
      if isScrollbarVisible: self.addch(DETAILS_HEIGHT + 1, 1, curses.ACS_TTEE)
      
      drawEntries = cursorSelection.getDetails(width)
      for i in range(min(len(drawEntries), DETAILS_HEIGHT)):
        self.addstr(1 + i, 2, drawEntries[i][0], drawEntries[i][1])
    
    # title label with connection counts
    if self.isTitleVisible():
      title = "Connection Details:" if self._showDetails else self._title
      self.addstr(0, 0, title, curses.A_STANDOUT)
    
    scrollOffset = 0
    if isScrollbarVisible:
      scrollOffset = 2
      self.addScrollBar(scrollLoc, scrollLoc + height - detailPanelOffset - 1, len(self._entryLines), 1 + detailPanelOffset)
    
    if self.isPaused() or not self._isTorRunning:
      currentTime = self.getPauseTime()
    else: currentTime = time.time()
    
    for lineNum in range(scrollLoc, len(self._entryLines)):
      entryLine = self._entryLines[lineNum]
      
      # if this is an unresolved SOCKS, HIDDEN, or CONTROL entry then queue up
      # resolution for the applicaitions they belong to
      if isinstance(entryLine, connEntry.ConnectionLine) and entryLine.isUnresolvedApp():
        self._resolveApps()
      
      # hilighting if this is the selected line
      extraFormat = curses.A_STANDOUT if entryLine == cursorSelection else curses.A_NORMAL
      
      drawLine = lineNum + detailPanelOffset + 1 - scrollLoc
      
      prefix = entryLine.getListingPrefix()
      for i in range(len(prefix)):
        self.addch(drawLine, scrollOffset + i, prefix[i])
      
      xOffset = scrollOffset + len(prefix)
      drawEntry = entryLine.getListingEntry(width - scrollOffset - len(prefix), currentTime, self._listingType)
      
      for msg, attr in drawEntry:
        attr |= extraFormat
        self.addstr(drawLine, xOffset, msg, attr)
        xOffset += len(msg)
      
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
    
    self.appResolveSinceUpdate = False
    
    # if we don't have an initialized resolver then this is a no-op
    if not connections.isResolverAlive("tor"): return
    
    connResolver = connections.getResolver("tor")
    currentResolutionCount = connResolver.getResolutionCount()
    
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
      newConnLine = newConnEntry.getLines()[0]
      
      if newConnLine.getType() != connEntry.Category.CIRCUIT:
        newEntries.append(newConnEntry)
        
        # updates exit port and client locale usage information
        if newConnLine.isPrivate():
          if newConnLine.getType() == connEntry.Category.INBOUND:
            # client connection, update locale information
            clientLocale = newConnLine.foreign.getLocale()
            
            if clientLocale:
              self._clientLocaleUsage[clientLocale] = self._clientLocaleUsage.get(clientLocale, 0) + 1
          elif newConnLine.getType() == connEntry.Category.EXIT:
            exitPort = newConnLine.foreign.getPort()
            self._exitPortUsage[exitPort] = self._exitPortUsage.get(exitPort, 0) + 1
    
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
    Triggers an asynchronous query for all unresolved SOCKS, HIDDEN, and
    CONTROL entries.
    
    Arguments:
      flagQuery - sets a flag to prevent further call from being respected
                  until the next update if true
    """
    
    if self.appResolveSinceUpdate or not self._config["features.connection.resolveApps"]: return
    unresolvedLines = [l for l in self._entryLines if isinstance(l, connEntry.ConnectionLine) and l.isUnresolvedApp()]
    
    # get the ports used for unresolved applications
    appPorts = []
    
    for line in unresolvedLines:
      appConn = line.local if line.getType() == connEntry.Category.HIDDEN else line.foreign
      appPorts.append(appConn.getPort())
    
    # Queue up resolution for the unresolved ports (skips if it's still working
    # on the last query).
    if appPorts and not self._appResolver.isResolving:
      self._appResolver.resolve(appPorts)
    
    # Fetches results. If the query finishes quickly then this is what we just
    # asked for, otherwise these belong to an earlier resolution.
    #
    # The application resolver might have given up querying (for instance, if
    # the lsof lookups aren't working on this platform or lacks permissions).
    # The isAppResolving flag lets the unresolved entries indicate if there's
    # a lookup in progress for them or not.
    
    appResults = self._appResolver.getResults(0.2)
    
    for line in unresolvedLines:
      isLocal = line.getType() == connEntry.Category.HIDDEN
      linePort = line.local.getPort() if isLocal else line.foreign.getPort()
      
      if linePort in appResults:
        # sets application attributes if there's a result with this as the
        # inbound port
        for inboundPort, outboundPort, cmd, pid in appResults[linePort]:
          appPort = outboundPort if isLocal else inboundPort
          
          if linePort == appPort:
            line.appName = cmd
            line.appPid = pid
            line.isAppResolving = False
      else:
        line.isAppResolving = self._appResolver.isResolving
    
    if flagQuery:
      self.appResolveSinceUpdate = True

