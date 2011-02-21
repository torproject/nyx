"""
Listing of the currently established connections tor has made.
"""

import time
import curses
import threading

from interface.connections import listings
from util import connections, enum, log, panel, torTools, uiTools

REDRAW_RATE = 10 # TODO: make a config option

DEFAULT_CONFIG = {}

# height of the detail panel content, not counting top and bottom border
DETAILS_HEIGHT = 7

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
    self._showDetails = False   # presents the details panel if true
    
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
  
  def handleKey(self, key):
    self.valsLock.acquire()
    
    if uiTools.isScrollKey(key):
      pageHeight = self.getPreferredSize()[0] - 1
      if self._showDetails: pageHeight -= (DETAILS_HEIGHT + 1)
      isChanged = self.scroller.handleKey(key, self._connections, pageHeight)
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
      
      if self._isPaused or currentTime - lastDraw < REDRAW_RATE:
        self._cond.acquire()
        if not self._halt: self._cond.wait(0.2)
        self._cond.release()
      else:
        # updates content if their's new results, otherwise just redraws
        self._update()
        self.redraw(True)
        lastDraw += REDRAW_RATE
  
  def draw(self, width, height):
    self.valsLock.acquire()
    
    # extra line when showing the detail panel is for the bottom border
    detailPanelOffset = DETAILS_HEIGHT + 1 if self._showDetails else 0
    isScrollbarVisible = len(self._connections) > height - detailPanelOffset - 1
    
    scrollLoc = self.scroller.getScrollLoc(self._connections, height - detailPanelOffset - 1)
    cursorSelection = self.scroller.getCursorSelection(self._connections)
    
    # draws the detail panel if currently displaying it
    if self._showDetails:
      self._drawSelectionPanel(cursorSelection, width, isScrollbarVisible)
    
    # title label with connection counts
    title = "Connection Details:" if self._showDetails else self._title
    self.addstr(0, 0, title, curses.A_STANDOUT)
    
    scrollOffset = 0
    if isScrollbarVisible:
      scrollOffset = 3
      self.addScrollBar(scrollLoc, scrollLoc + height - detailPanelOffset - 1, len(self._connections), 1 + detailPanelOffset)
    
    currentTime = self._pauseTime if self._pauseTime else time.time()
    for lineNum in range(scrollLoc, len(self._connections)):
      entry = self._connections[lineNum]
      drawLine = lineNum + detailPanelOffset + 1 - scrollLoc
      
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
  
  def _drawSelectionPanel(self, selection, width, isScrollbarVisible):
    """
    Renders a panel for details on the selected connnection.
    """
    
    # This is a solid border unless the scrollbar is visible, in which case a
    # 'T' pipe connects the border to the bar.
    uiTools.drawBox(self, 0, 0, width, DETAILS_HEIGHT + 2)
    if isScrollbarVisible: self.addch(DETAILS_HEIGHT + 1, 1, curses.ACS_TTEE)
    
    selectionFormat = curses.A_BOLD | uiTools.getColor(listings.CATEGORY_COLOR[selection.getType()])
    lines = [""] * 7
    
    lines[0] = "address: %s" % selection.getDestinationLabel(width - 11, listings.DestAttr.NONE)
    lines[1] = "locale: %s" % ("??" if selection.isPrivate() else selection.foreign.getLocale())
    
    # Remaining data concerns the consensus results, with three possible cases:
    # - if there's a single match then display its details
    # - if there's multiple potenial relays then list all of the combinations
    #   of ORPorts / Fingerprints
    # - if no consensus data is available then say so (probably a client or
    #   exit connection)
    
    fingerprint = selection.foreign.getFingerprint()
    conn = torTools.getConn()
    
    if fingerprint != "UNKNOWN":
      # single match - display information available about it
      nsEntry = conn.getConsensusEntry(fingerprint)
      descEntry = conn.getDescriptorEntry(fingerprint)
      
      # append the fingerprint to the second line
      lines[1] = "%-13sfingerprint: %s" % (lines[1], fingerprint)
      
      if nsEntry:
        # example consensus entry:
        # r murble R8sCM1ar1sS2GulQYFVmvN95xsk RJr6q+wkTFG+ng5v2bdCbVVFfA4 2011-02-21 00:25:32 195.43.157.85 443 0
        # s Exit Fast Guard Named Running Stable Valid
        # w Bandwidth=2540
        # p accept 20-23,43,53,79-81,88,110,143,194,443
        
        nsLines = nsEntry.split("\n")
        
        firstLineComp = nsLines[0].split(" ")
        if len(firstLineComp) >= 9:
          _, nickname, _, _, pubDate, pubTime, _, orPort, dirPort = firstLineComp[:9]
        else: nickname, pubDate, pubTime, orPort, dirPort = "", "", "", "", ""
        
        flags = nsLines[1][2:]
        microExit = nsLines[3][2:]
        
        dirPortLabel = "" if dirPort == "0" else "dirport: %s" % dirPort
        lines[2] = "nickname: %-25s orport: %-10s %s" % (nickname, orPort, dirPortLabel)
        lines[3] = "published: %s %s" % (pubDate, pubTime)
        lines[4] = "flags: %s" % flags.replace(" ", ", ")
        lines[5] = "exit policy: %s" % microExit.replace(",", ", ")
      
      if descEntry:
        torVersion, patform, contact = "", "", ""
        
        for descLine in descEntry.split("\n"):
          if descLine.startswith("platform"):
            # has the tor version and platform, ex:
            # platform Tor 0.2.1.29 (r318f470bc5f2ad43) on Linux x86_64
            
            torVersion = descLine[13:descLine.find(" ", 13)]
            platform = descLine[descLine.rfind(" on ") + 4:]
          elif descLine.startswith("contact"):
            contact = descLine[8:]
            
            # clears up some highly common obscuring
            for alias in (" at ", " AT "): contact = contact.replace(alias, "@")
            for alias in (" dot ", " DOT "): contact = contact.replace(alias, ".")
            
            break # contact lines come after the platform
        
        lines[3] = "%-36s os: %-14s version: %s" % (lines[3], platform, torVersion)
        
        # contact information is an optional field
        if contact: lines[6] = "contact: %s" % contact
    else:
      allMatches = conn.getRelayFingerprint(selection.foreign.getIpAddr(), getAllMatches = True)
      
      if allMatches:
        # multiple matches
        lines[2] = "Muliple matches, possible fingerprints are:"
        
        for i in range(len(allMatches)):
          isLastLine = i == 3
          
          relayPort, relayFingerprint = allMatches[i]
          lineText = "%i. or port: %-5s fingerprint: %s" % (i, relayPort, relayFingerprint)
          
          # if there's multiple lines remaining at the end then give a count
          remainingRelays = len(allMatches) - i
          if isLastLine and remainingRelays > 1:
            lineText = "... %i more" % remainingRelays
          
          lines[3 + i] = lineText
          
          if isLastLine: break
      else:
        # no consensus entry for this ip address
        lines[2] = "No consensus data found"
    
    for i in range(len(lines)):
      lineText = uiTools.cropStr(lines[i], width - 2)
      self.addstr(1 + i, 2, lineText, selectionFormat)

