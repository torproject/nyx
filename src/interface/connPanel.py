#!/usr/bin/env python
# connPanel.py -- Lists network connections used by tor.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import time
import socket
import curses
from threading import RLock
from TorCtl import TorCtl

from util import log, connections, hostnames, panel, torTools, uiTools

# Scrubs private data from any connection that might belong to client or exit
# traffic. This is a little overly conservative, hiding anything that isn't
# identified as a relay and meets the following criteria:
# - Connection is inbound and relay's either a bridge (BridgeRelay is set) or 
#   guard (making it a probable client connection)
# - Outbound connection permitted by the exit policy (probable exit connection)
# 
# Note that relay etiquette says these are bad things to look at (ie, DON'T 
# CHANGE THIS UNLESS YOU HAVE A DAMN GOOD REASON!)
SCRUB_PRIVATE_DATA = True

# directory servers (IP, port) for tor version 0.2.1.24
# this comes from the dirservers array in src/or/config.c
DIR_SERVERS = [("86.59.21.38", "80"),         # tor26
               ("128.31.0.39", "9031"),       # moria1
               ("216.224.124.114", "9030"),   # ides
               ("80.190.246.100", "8180"),    # gabelmoo
               ("194.109.206.212", "80"),     # dizum
               ("193.23.244.244", "80"),      # dannenberg
               ("208.83.223.34", "443"),      # urras
               ("82.94.251.203", "80")]       # Tonga

# enums for listing types
LIST_IP, LIST_HOSTNAME, LIST_FINGERPRINT, LIST_NICKNAME = range(4)
LIST_LABEL = {LIST_IP: "IP Address", LIST_HOSTNAME: "Hostname", LIST_FINGERPRINT: "Fingerprint", LIST_NICKNAME: "Nickname"}

# attributes for connection types
TYPE_COLORS = {"inbound": "green", "outbound": "blue", "client": "cyan", "directory": "magenta", "control": "red", "family": "magenta", "localhost": "yellow"}
TYPE_WEIGHTS = {"inbound": 0, "outbound": 1, "client": 2, "directory": 3, "control": 4, "family": 5, "localhost": 6} # defines ordering

# enums for indexes of ConnPanel 'connections' fields
CONN_TYPE, CONN_L_IP, CONN_L_PORT, CONN_F_IP, CONN_F_PORT, CONN_COUNTRY, CONN_TIME, CONN_PRIVATE = range(8)

# labels associated to 'connectionCount' 
CONN_COUNT_LABELS = ["inbound", "outbound", "client", "directory", "control"]

# enums for sorting types (note: ordering corresponds to SORT_TYPES for easy lookup)
# TODO: add ORD_BANDWIDTH -> (ORD_BANDWIDTH, "Bandwidth", lambda x, y: ???)
ORD_TYPE, ORD_FOREIGN_LISTING, ORD_SRC_LISTING, ORD_DST_LISTING, ORD_COUNTRY, ORD_FOREIGN_PORT, ORD_SRC_PORT, ORD_DST_PORT, ORD_TIME = range(9)
SORT_TYPES = [(ORD_TYPE, "Connection Type",
                lambda x, y: TYPE_WEIGHTS[x[CONN_TYPE]] - TYPE_WEIGHTS[y[CONN_TYPE]]),
              (ORD_FOREIGN_LISTING, "Listing (Foreign)", None),
              (ORD_SRC_LISTING, "Listing (Source)", None),
              (ORD_DST_LISTING, "Listing (Dest.)", None),
              (ORD_COUNTRY, "Country Code",
                lambda x, y: cmp(x[CONN_COUNTRY], y[CONN_COUNTRY])),
              (ORD_FOREIGN_PORT, "Port (Foreign)",
                lambda x, y: int(x[CONN_F_PORT]) - int(y[CONN_F_PORT])),
              (ORD_SRC_PORT, "Port (Source)",
                lambda x, y: int(x[CONN_F_PORT] if x[CONN_TYPE] == "inbound" else x[CONN_L_PORT]) - int(y[CONN_F_PORT] if y[CONN_TYPE] == "inbound" else y[CONN_L_PORT])),
              (ORD_DST_PORT, "Port (Dest.)",
                lambda x, y: int(x[CONN_L_PORT] if x[CONN_TYPE] == "inbound" else x[CONN_F_PORT]) - int(y[CONN_L_PORT] if y[CONN_TYPE] == "inbound" else y[CONN_F_PORT])),
              (ORD_TIME, "Connection Time",
                lambda x, y: cmp(-x[CONN_TIME], -y[CONN_TIME]))]

# provides bi-directional mapping of sorts with their associated labels
def getSortLabel(sortType, withColor = False):
  """
  Provides label associated with a type of sorting. Throws ValueEror if no such
  sort exists. If adding color formatting this wraps with the following mappings:
  Connection Type     red
  Listing *           blue
  Port *              green
  Bandwidth           cyan
  Country Code        yellow
  """
  
  for (type, label, func) in SORT_TYPES:
    if sortType == type:
      color = None
      
      if withColor:
        if label == "Connection Type": color = "red"
        elif label.startswith("Listing"): color = "blue"
        elif label.startswith("Port"): color = "green"
        elif label == "Bandwidth": color = "cyan"
        elif label == "Country Code": color = "yellow"
        elif label == "Connection Time": color = "magenta"
      
      if color: return "<%s>%s</%s>" % (color, label, color)
      else: return label
  
  raise ValueError(sortType)

def getSortType(sortLabel):
  """
  Provides sort type associated with a given label. Throws ValueEror if label
  isn't recognized.
  """
  
  for (type, label, func) in SORT_TYPES:
    if sortLabel == label: return type
  raise ValueError(sortLabel)

class ConnPanel(TorCtl.PostEventListener, panel.Panel):
  """
  Lists tor related connection data.
  """
  
  def __init__(self, stdscr, conn, isDisabled):
    TorCtl.PostEventListener.__init__(self)
    panel.Panel.__init__(self, stdscr, "conn", 0)
    self.scroll = 0
    self.conn = conn                  # tor connection for querrying country codes
    self.listingType = LIST_IP        # information used in listing entries
    self.allowDNS = False             # permits hostname resolutions if true
    self.showLabel = True             # shows top label if true, hides otherwise
    self.showingDetails = False       # augments display to accomidate details window if true
    self.lastUpdate = -1              # time last stats was retrived
    self.localhostEntry = None        # special connection - tuple with (entry for this node, fingerprint)
    self.sortOrdering = [ORD_TYPE, ORD_FOREIGN_LISTING, ORD_FOREIGN_PORT]
    self.fingerprintLookupCache = {}                              # cache of (ip, port) -> fingerprint
    self.nicknameLookupCache = {}                                 # cache of (ip, port) -> nickname
    self.fingerprintMappings = _getFingerprintMappings(self.conn) # mappings of ip -> [(port, fingerprint, nickname), ...]
    self.providedGeoipWarning = False
    self.orconnStatusCache = []           # cache for 'orconn-status' calls
    self.orconnStatusCacheValid = False   # indicates if cache has been invalidated
    self.clientConnectionCache = None     # listing of nicknames for our client connections
    self.clientConnectionLock = RLock()   # lock for clientConnectionCache
    self.isDisabled = isDisabled          # prevent panel from updating entirely
    self.lastConnResults = None           # used to check if connection results have changed
    
    self.isCursorEnabled = True
    self.cursorSelection = None
    self.cursorLoc = 0              # fallback cursor location if selection disappears
    
    # parameters used for pausing
    self.isPaused = False
    self.pauseTime = 0              # time when paused
    self.connectionsBuffer = []     # location where connections are stored while paused
    self.connectionCountBuffer = []
    self.familyResolutionsBuffer = {}
    
    # mapping of ip/port to fingerprint of family entries, used in hack to short circuit (ip / port) -> fingerprint lookups
    self.familyResolutions = {}
    
    # mapping of family entries to fingerprints
    self.familyFingerprints = {}
    
    self.address = ""
    self.nickname = ""
    self.listenPort = "0"           # port used to identify inbound/outbound connections (from ORListenAddress if defined, otherwise ORPort)
    self.orPort = "0"
    self.dirPort = "0"
    self.controlPort = "0"
    self.family = []                # fingerpints of family entries
    self.isBridge = False           # true if BridgeRelay is set
    self.exitPolicy = ""
    self.exitRejectPrivate = True   # true if ExitPolicyRejectPrivate is 0
    
    self.resetOptions()
    
    # connection results are tuples of the form:
    # (type, local IP, local port, foreign IP, foreign port, country code)
    self.connections = []
    self.connectionsLock = RLock()    # limits modifications of connections
    
    # count of total inbound, outbound, client, directory, and control connections
    self.connectionCount = [0] * 5
    
    self.reset()
  
  def resetOptions(self):
    self.familyResolutions = {}
    self.familyFingerprints = {}
    
    try:
      self.address = "" # fetched when needed if unset
      self.nickname = self.conn.get_option("Nickname")[0][1]
      
      self.orPort = self.conn.get_option("ORPort")[0][1]
      self.dirPort = self.conn.get_option("DirPort")[0][1]
      self.controlPort = self.conn.get_option("ControlPort")[0][1]
      
      # uses ports to identify type of connections (ORListenAddress port overwrites ORPort if set)
      listenAddr = self.conn.get_option("ORListenAddress")[0][1]
      if listenAddr and ":" in listenAddr:
        self.listenPort = listenAddr[listenAddr.find(":") + 1:]
      else: self.listenPort = self.orPort
      
      # entry is None if not set, otherwise of the format "$<fingerprint>,$<fingerprint>"
      familyEntry = self.conn.get_option("MyFamily")[0][1]
      if familyEntry: self.family = familyEntry.split(",")
      else: self.family = []
      
      self.isBridge = self.conn.get_option("BridgeRelay")[0][1] == "1"
      
      policyEntries = torTools.getConn().getOption("ExitPolicy", multiple=True)
      if not policyEntries: policyEntries = [] # if ExitPolicy is undefined, policyEntries is None
      self.exitPolicy = ",".join(policyEntries)
      self.exitPolicy = self.exitPolicy.replace("\\t", " ").replace("\"", "")
      
      if self.exitPolicy: self.exitPolicy += "," + self.conn.get_info("exit-policy/default")["exit-policy/default"]
      else: self.exitPolicy = self.conn.get_info("exit-policy/default")["exit-policy/default"]
      
      self.exitRejectPrivate = self.conn.get_option("ExitPolicyRejectPrivate")[0][1] == "1"
      
      self._resolveFamilyEntries()
    except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed):
      self.nickname = ""
      self.listenPort = None
      self.orPort = "0"
      self.dirPort = "0"
      self.controlPort = "0"
      self.family = []
      self.isBridge = False
      self.exitPolicy = ""
      self.exitRejectPrivate = True
  
  # change in client circuits
  def circ_status_event(self, event):
    self.clientConnectionLock.acquire()
    self.clientConnectionCache = None
    self.clientConnectionLock.release()
  
  # when consensus changes update fingerprint mappings
  # TODO: should also be taking NS events into account
  def new_consensus_event(self, event):
    self.orconnStatusCacheValid = False
    self.fingerprintLookupCache.clear()
    self.nicknameLookupCache.clear()
    self.fingerprintMappings = _getFingerprintMappings(self.conn, event.nslist)
    if self.listingType != LIST_HOSTNAME: self.sortConnections()
  
  def new_desc_event(self, event):
    self.orconnStatusCacheValid = False
    self._resolveFamilyEntries()
    
    for fingerprint in event.idlist:
      # clears entries with this fingerprint from the cache
      if fingerprint in self.fingerprintLookupCache.values():
        invalidEntries = set(k for k, v in self.fingerprintLookupCache.iteritems() if v == fingerprint)
        for k in invalidEntries:
          # nicknameLookupCache keys are a subset of fingerprintLookupCache
          del self.fingerprintLookupCache[k]
          if k in self.nicknameLookupCache.keys(): del self.nicknameLookupCache[k]
      
      # gets consensus data for the new description
      try: nsData = self.conn.get_network_status("id/%s" % fingerprint)
      except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed): return
      
      if len(nsData) > 1:
        # multiple records for fingerprint (shouldn't happen)
        log.log(log.WARN, "Multiple consensus entries for fingerprint: %s" % fingerprint)
        return
      nsEntry = nsData[0]
      
      # updates fingerprintMappings with new data
      if nsEntry.ip in self.fingerprintMappings.keys():
        # if entry already exists with the same orport, remove it
        orportMatch = None
        for entryPort, entryFingerprint, entryNickname in self.fingerprintMappings[nsEntry.ip]:
          if entryPort == nsEntry.orport:
            orportMatch = (entryPort, entryFingerprint, entryNickname)
            break
        
        if orportMatch: self.fingerprintMappings[nsEntry.ip].remove(orportMatch)
        
        # add new entry
        self.fingerprintMappings[nsEntry.ip].append((nsEntry.orport, nsEntry.idhex, nsEntry.nickname))
      else:
        self.fingerprintMappings[nsEntry.ip] = [(nsEntry.orport, nsEntry.idhex, nsEntry.nickname)]
    if self.listingType != LIST_HOSTNAME: self.sortConnections()
  
  def reset(self):
    """
    Reloads connection results.
    """
    
    if self.isDisabled: return
    
    # inaccessable during startup so might need to be refetched
    try:
      if not self.address: self.address = self.conn.get_info("address")["address"]
    except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed): pass
    
    self.connectionsLock.acquire()
    self.clientConnectionLock.acquire()
    
    # temporary variables for connections and count
    connectionsTmp = []
    connectionCountTmp = [0] * 5
    familyResolutionsTmp = {}
    
    # used (with isBridge) to determine if inbound connections should be scrubbed
    isGuard = False
    try:
      myFingerprint = self.conn.get_info("fingerprint")
      nsCall = self.conn.get_network_status("id/%s" % myFingerprint)
      if nsCall: isGuard = "Guard" in nsCall[0].flags
      else: raise TorCtl.ErrorReply # network consensus couldn't be fetched
    except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed): pass
    
    try:
      if self.clientConnectionCache == None:
        # client connection cache was invalidated
        self.clientConnectionCache = _getClientConnections(self.conn)
      
      connTimes = {} # mapping of ip/port to connection time
      for entry in (self.connections if not self.isPaused else self.connectionsBuffer):
        connTimes[(entry[CONN_F_IP], entry[CONN_F_PORT])] = entry[CONN_TIME]
      
      results = connections.getResolver("tor").getConnections()
      if results == self.lastConnResults: return # contents haven't changed
      
      for lIp, lPort, fIp, fPort in results:
        fingerprint = self.getFingerprint(fIp, fPort)
        
        isPrivate = False
        if lPort in (self.listenPort, self.dirPort):
          type = "inbound"
          connectionCountTmp[0] += 1
          if SCRUB_PRIVATE_DATA and fIp not in self.fingerprintMappings.keys(): isPrivate = isGuard or self.isBridge
        elif lPort == self.controlPort:
          type = "control"
          connectionCountTmp[4] += 1
        else:
          nickname = self.getNickname(fIp, fPort)
          
          isClient = False
          for clientName in self.clientConnectionCache:
            if nickname == clientName or (len(clientName) > 1 and clientName[0] == "$" and fingerprint == clientName[1:]):
              isClient = True
              break
          
          if isClient:
            type = "client"
            connectionCountTmp[2] += 1
          elif (fIp, fPort) in DIR_SERVERS:
            type = "directory"
            connectionCountTmp[3] += 1
          else:
            type = "outbound"
            connectionCountTmp[1] += 1
            if SCRUB_PRIVATE_DATA and fIp not in self.fingerprintMappings.keys(): isPrivate = isExitAllowed(fIp, fPort, self.exitPolicy, self.exitRejectPrivate)
        
        # replace nat address with external version if available
        if self.address and type != "control": lIp = self.address
        
        try:
          countryCodeQuery = "ip-to-country/%s" % fIp
          countryCode = self.conn.get_info(countryCodeQuery)[countryCodeQuery]
        except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed):
          countryCode = "??"
          if not self.providedGeoipWarning:
            log.log(log.WARN, "Tor geoip database is unavailable.")
            self.providedGeoipWarning = True
        
        if (fIp, fPort) in connTimes: connTime = connTimes[(fIp, fPort)]
        else: connTime = time.time()
        
        connectionsTmp.append((type, lIp, lPort, fIp, fPort, countryCode, connTime, isPrivate))
      
      # appends localhost connection to allow user to look up their own consensus entry
      selfFingerprint = None
      try:
        selfFingerprint = self.conn.get_info("fingerprint")["fingerprint"]
      except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed): pass
      
      if self.address and selfFingerprint:
        try:
          countryCodeQuery = "ip-to-country/%s" % self.address
          selfCountryCode = self.conn.get_info(countryCodeQuery)[countryCodeQuery]
        except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed):
          selfCountryCode = "??"
        
        if (self.address, self.orPort) in connTimes: connTime = connTimes[(self.address, self.orPort)]
        else: connTime = time.time()
        
        self.localhostEntry = (("localhost", self.address, self.orPort, self.address, self.orPort, selfCountryCode, connTime, False), selfFingerprint)
        connectionsTmp.append(self.localhostEntry[0])
      else:
        self.localhostEntry = None
      
      # appends family connections
      tmpCounter = 0 # used for unique port of unresolved family entries (funky hack)
      for familyEntry in self.family:
        # TODO: turns out that "ns/name/<OR nickname>" accpets fingerprint
        # identifiers, so all this nickname -> fingerprint work is unnecessary,
        # but used for fingerprint lookup performance in draw... this could be
        # improved (might be completely unnecessary due to the fingerprint
        # lookup cache)
        fingerprint = None
        if familyEntry in self.familyFingerprints:
          fingerprint = self.familyFingerprints[familyEntry]
        
        try:
          if fingerprint: nsCall = self.conn.get_network_status("id/%s" % fingerprint)
          else: nsCall = self.conn.get_network_status("name/%s" % familyEntry)
          if nsCall: familyAddress, familyPort = nsCall[0].ip, nsCall[0].orport
          else: raise TorCtl.ErrorReply # network consensus couldn't be fetched
          
          countryCodeQuery = "ip-to-country/%s" % familyAddress
          familyCountryCode = self.conn.get_info(countryCodeQuery)[countryCodeQuery]
          
          if (familyAddress, familyPort) in connTimes: connTime = connTimes[(familyAddress, familyPort)]
          else: connTime = time.time()
          
          if fingerprint: familyResolutionsTmp[(familyAddress, familyPort)] = fingerprint
          connectionsTmp.append(("family", familyAddress, familyPort, familyAddress, familyPort, familyCountryCode, connTime, False))
        except (socket.error, TorCtl.ErrorReply):
          # use dummy entry for sorting - the draw function notes that entries are unknown
          portIdentifier = str(65536 + tmpCounter)
          if fingerprint: familyResolutionsTmp[("256.255.255.255", portIdentifier)] = fingerprint
          connectionsTmp.append(("family", "256.255.255.255", portIdentifier, "256.255.255.255", portIdentifier, "??", time.time(), False))
          tmpCounter += 1
        except TorCtl.TorCtlClosed:
          pass # connections aren't shown when control port is unavailable
      
      self.lastUpdate = time.time()
      
      # assigns results
      if self.isPaused:
        self.connectionsBuffer = connectionsTmp
        self.connectionCountBuffer = connectionCountTmp
        self.familyResolutionsBuffer = familyResolutionsTmp
      else:
        self.connections = connectionsTmp
        self.connectionCount = connectionCountTmp
        self.familyResolutions = familyResolutionsTmp
        
        # hostnames are sorted at draw - otherwise now's a good time
        if self.listingType != LIST_HOSTNAME: self.sortConnections()
      self.lastConnResults = results
    finally:
      self.connectionsLock.release()
      self.clientConnectionLock.release()
  
  def handleKey(self, key):
    # cursor or scroll movement
    
    #if key in (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE, curses.KEY_NPAGE):
    if uiTools.isScrollKey(key):
      pageHeight = self.getPreferredSize()[0] - 1
      if self.showingDetails: pageHeight -= 8
      
      self.connectionsLock.acquire()
      try:
        # determines location parameter to use
        if self.isCursorEnabled:
          try: currentLoc = self.connections.index(self.cursorSelection)
          except ValueError: currentLoc = self.cursorLoc # fall back to nearby entry
        else: currentLoc = self.scroll
        
        # location offset
        if key == curses.KEY_UP: shift = -1
        elif key == curses.KEY_DOWN: shift = 1
        elif key == curses.KEY_PPAGE: shift = -pageHeight + 1 if self.isCursorEnabled else -pageHeight
        elif key == curses.KEY_NPAGE: shift = pageHeight - 1 if self.isCursorEnabled else pageHeight
        elif key == curses.KEY_HOME: shift = -currentLoc
        elif key == curses.KEY_END: shift = len(self.connections) # always below the lower bound
        newLoc = currentLoc + shift
        
        # restricts to valid bounds
        maxLoc = len(self.connections) - 1 if self.isCursorEnabled else len(self.connections) - pageHeight
        newLoc = max(0, min(newLoc, maxLoc))
        
        # applies to proper parameter
        if self.isCursorEnabled and self.connections:
          self.cursorSelection, self.cursorLoc = self.connections[newLoc], newLoc
        else: self.scroll = newLoc
      finally:
        self.connectionsLock.release()
    elif key == ord('r') or key == ord('R'):
      self.allowDNS = not self.allowDNS
      if not self.allowDNS: hostnames.setPaused(True)
      elif self.listingType == LIST_HOSTNAME: hostnames.setPaused(False)
    else: return # skip following redraw
    self.redraw(True)
  
  def draw(self, subwindow, width, height):
    self.connectionsLock.acquire()
    try:
      # hostnames frequently get updated so frequent sorting needed
      if self.listingType == LIST_HOSTNAME: self.sortConnections()
      
      if self.showLabel:
        # notes the number of connections for each type if above zero
        countLabel = ""
        for i in range(len(self.connectionCount)):
          if self.connectionCount[i] > 0: countLabel += "%i %s, " % (self.connectionCount[i], CONN_COUNT_LABELS[i])
        if countLabel: countLabel = " (%s)" % countLabel[:-2] # strips ending ", " and encases in parentheses
        self.addstr(0, 0, "Connections%s:" % countLabel, curses.A_STANDOUT)
      
      if self.connections:
        listingHeight = height - 1
        currentTime = time.time() if not self.isPaused else self.pauseTime
        
        if self.showingDetails:
          listingHeight -= 8
          isScrollBarVisible = len(self.connections) > height - 9
          if width > 80: subwindow.hline(8, 80, curses.ACS_HLINE, width - 81)
        else:
          isScrollBarVisible = len(self.connections) > height - 1
        xOffset = 3 if isScrollBarVisible else 0 # content offset for scroll bar
        
        # ensure cursor location and scroll top are within bounds
        self.cursorLoc = max(min(self.cursorLoc, len(self.connections) - 1), 0)
        self.scroll = max(min(self.scroll, len(self.connections) - listingHeight), 0)
        
        if self.isCursorEnabled:
          # update cursorLoc with selection (or vice versa if selection not found)
          if self.cursorSelection not in self.connections:
            self.cursorSelection = self.connections[self.cursorLoc]
          else: self.cursorLoc = self.connections.index(self.cursorSelection)
          
          # shift scroll if necessary for cursor to be visible
          if self.cursorLoc < self.scroll: self.scroll = self.cursorLoc
          elif self.cursorLoc - listingHeight + 1 > self.scroll: self.scroll = self.cursorLoc - listingHeight + 1
        
        lineNum = (-1 * self.scroll) + 1
        for entry in self.connections:
          if lineNum >= 1:
            type = entry[CONN_TYPE]
            isPrivate = entry[CONN_PRIVATE]
            color = TYPE_COLORS[type]
            
            # adjustments to measurements for 'xOffset' are to account for scroll bar
            if self.listingType == LIST_IP:
              # base data requires 73 characters
              src = "%s:%s" % (entry[CONN_L_IP], entry[CONN_L_PORT])
              dst = "%s:%s %s" % (entry[CONN_F_IP], entry[CONN_F_PORT], "" if type == "control" else "(%s)" % entry[CONN_COUNTRY])
              
              if isPrivate: dst = "<scrubbed>"
              
              src, dst = "%-21s" % src, "%-26s" % dst
              
              etc = ""
              if width > 115 + xOffset:
                # show fingerprint (column width: 42 characters)
                etc += "%-40s  " % self.getFingerprint(entry[CONN_F_IP], entry[CONN_F_PORT])
                
              if width > 127 + xOffset:
                # show nickname (column width: remainder)
                nickname = self.getNickname(entry[CONN_F_IP], entry[CONN_F_PORT])
                nicknameSpace = width - 118 - xOffset
                
                # truncates if too long
                if len(nickname) > nicknameSpace: nickname = "%s..." % nickname[:nicknameSpace - 3]
                
                etc += ("%%-%is  " % nicknameSpace) % nickname
            elif self.listingType == LIST_HOSTNAME:
              # base data requires 80 characters
              src = "localhost:%-5s" % entry[CONN_L_PORT]
              
              # space available for foreign hostname (stretched to claim any free space)
              foreignHostnameSpace = width - 42 - xOffset
              
              etc = ""
              if width > 102 + xOffset:
                # shows ip/locale (column width: 22 characters)
                foreignHostnameSpace -= 22
                
                if isPrivate: ipEntry = "<scrubbed>"
                else: ipEntry = "%s %s" % (entry[CONN_F_IP], "" if type == "control" else "(%s)" % entry[CONN_COUNTRY])
                etc += "%-20s  " % ipEntry
              
              if width > 134 + xOffset:
                # show fingerprint (column width: 42 characters)
                foreignHostnameSpace -= 42
                etc += "%-40s  " % self.getFingerprint(entry[CONN_F_IP], entry[CONN_F_PORT])
              
              if width > 151 + xOffset:
                # show nickname (column width: min 17 characters, uses half of the remainder)
                nickname = self.getNickname(entry[CONN_F_IP], entry[CONN_F_PORT])
                nicknameSpace = 15 + (width - xOffset - 151) / 2
                foreignHostnameSpace -= (nicknameSpace + 2)
                
                if len(nickname) > nicknameSpace: nickname = "%s..." % nickname[:nicknameSpace - 3]
                etc += ("%%-%is  " % nicknameSpace) % nickname
              
              if isPrivate: dst = "<scrubbed>"
              else:
                try: hostname = hostnames.resolve(entry[CONN_F_IP])
                except ValueError: hostname = None
                
                # truncates long hostnames
                portDigits = len(str(entry[CONN_F_PORT]))
                if hostname and (len(hostname) + portDigits) > foreignHostnameSpace - 1:
                  hostname = hostname[:(foreignHostnameSpace - portDigits - 4)] + "..."
                
                dst = "%s:%s" % (hostname if hostname else entry[CONN_F_IP], entry[CONN_F_PORT])
              
              dst = ("%%-%is" % foreignHostnameSpace) % dst
            elif self.listingType == LIST_FINGERPRINT:
              # base data requires 75 characters
              src = "localhost"
              if entry[CONN_TYPE] == "control": dst = "localhost"
              else: dst = self.getFingerprint(entry[CONN_F_IP], entry[CONN_F_PORT])
              dst = "%-40s" % dst
              
              etc = ""
              if width > 92 + xOffset:
                # show nickname (column width: min 17 characters, uses remainder if extra room's available)
                nickname = self.getNickname(entry[CONN_F_IP], entry[CONN_F_PORT])
                nicknameSpace = width - 78 - xOffset if width < 126 else width - 106 - xOffset
                if len(nickname) > nicknameSpace: nickname = "%s..." % nickname[:nicknameSpace - 3]
                etc += ("%%-%is  " % nicknameSpace) % nickname
              
              if width > 125 + xOffset:
                # shows ip/port/locale (column width: 28 characters)
                if isPrivate: ipEntry = "<scrubbed>"
                else: ipEntry = "%s:%s %s" % (entry[CONN_F_IP], entry[CONN_F_PORT], "" if type == "control" else "(%s)" % entry[CONN_COUNTRY])
                etc += "%-26s  " % ipEntry
            else:
              # base data uses whatever extra room's available (using minimun of 50 characters)
              src = self.nickname
              if entry[CONN_TYPE] == "control": dst = self.nickname
              else: dst = self.getNickname(entry[CONN_F_IP], entry[CONN_F_PORT])
              
              # space available for foreign nickname
              foreignNicknameSpace = width - len(self.nickname) - 27 - xOffset
              
              etc = ""
              if width > 92 + xOffset:
                # show fingerprint (column width: 42 characters)
                foreignNicknameSpace -= 42
                etc += "%-40s  " % self.getFingerprint(entry[CONN_F_IP], entry[CONN_F_PORT])
              
              if width > 120 + xOffset:
                # shows ip/port/locale (column width: 28 characters)
                foreignNicknameSpace -= 28
                
                if isPrivate: ipEntry = "<scrubbed>"
                else: ipEntry = "%s:%s %s" % (entry[CONN_F_IP], entry[CONN_F_PORT], "" if type == "control" else "(%s)" % entry[CONN_COUNTRY])
                etc += "%-26s  " % ipEntry
              
              dst = ("%%-%is" % foreignNicknameSpace) % dst
            
            timeLabel = uiTools.getTimeLabel(currentTime - entry[CONN_TIME], 1)
            if type == "inbound": src, dst = dst, src
            elif type == "family" and int(entry[CONN_L_PORT]) > 65535:
              # this belongs to an unresolved family entry - replaces invalid data with "UNKNOWN"
              timeLabel = "---"
              
              if self.listingType == LIST_IP:
                src = "%-21s" % "UNKNOWN"
                dst = "%-26s" % "UNKNOWN"
              elif self.listingType == LIST_HOSTNAME:
                src = "%-15s" % "UNKNOWN"
                dst = ("%%-%is" % len(dst)) % "UNKNOWN"
                if len(etc) > 0: etc = etc.replace("256.255.255.255 (??)", "UNKNOWN" + " " * 13)
              else:
                ipStart = etc.find("256")
                if ipStart > -1: etc = etc[:ipStart] + ("%%-%is" % len(etc[ipStart:])) % "UNKNOWN"
            
            padding = width - (len(src) + len(dst) + len(etc) + 27) - xOffset # padding needed to fill full line
            lineEntry = "<%s>%s  -->  %s  %s%s%5s (<b>%s</b>)%s</%s>" % (color, src, dst, etc, " " * padding, timeLabel, type.upper(), " " * (9 - len(type)), color)
            
            if self.isCursorEnabled and entry == self.cursorSelection:
              lineEntry = "<h>%s</h>" % lineEntry
            
            yOffset = 0 if not self.showingDetails else 8
            self.addfstr(lineNum + yOffset, xOffset, lineEntry)
          lineNum += 1
        
        if isScrollBarVisible:
          topY = 9 if self.showingDetails else 1
          bottomEntry = self.scroll + height - 9 if self.showingDetails else self.scroll + height - 1
          self.addScrollBar(self.scroll, bottomEntry, len(self.connections), topY)
    finally:
      self.connectionsLock.release()
  
  def getFingerprint(self, ipAddr, port):
    """
    Makes an effort to match connection to fingerprint - if there's multiple
    potential matches or the IP address isn't found in the discriptor then
    returns "UNKNOWN".
    """
    
    # checks to see if this matches the localhost entry
    if self.localhostEntry and ipAddr == self.localhostEntry[0][CONN_L_IP] and port == self.localhostEntry[0][CONN_L_PORT]:
      return self.localhostEntry[1]
    
    # checks if this belongs to a family entry
    if (ipAddr, port) in self.familyResolutions.keys():
      return self.familyResolutions[(ipAddr, port)]
    
    port = int(port)
    if (ipAddr, port) in self.fingerprintLookupCache:
      return self.fingerprintLookupCache[(ipAddr, port)]
    else:
      match = None
      
      # orconn-status provides a listing of Tor's current connections - used to
      # eliminated ambiguity for outbound connections
      if not self.orconnStatusCacheValid:
        self.orconnStatusCache, isOdd = [], True
        self.orconnStatusCacheValid = True
        try:
          for entry in self.conn.get_info("orconn-status")["orconn-status"].split():
            if isOdd: self.orconnStatusCache.append(entry)
            isOdd = not isOdd
        except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed): self.orconnStatusCache = None
      
      if ipAddr in self.fingerprintMappings.keys():
        potentialMatches = self.fingerprintMappings[ipAddr]
        
        if len(potentialMatches) == 1: match = potentialMatches[0][1]
        else:
          # multiple potential matches - look for exact match with port
          for (entryPort, entryFingerprint, entryNickname) in potentialMatches:
            if entryPort == port:
              match = entryFingerprint
              break
        
        if not match:
          # still haven't found it - use trick from Mike's ConsensusTracker,
          # excluding possiblities that have...
          # ... lost their Running flag
          # ... list a bandwidth of 0
          # ... have 'opt hibernating' set
          operativeMatches = list(potentialMatches)
          for entryPort, entryFingerprint, entryNickname in potentialMatches:
            # gets router description to see if 'down' is set
            toRemove = False
            try:
              nsCall = self.conn.get_network_status("id/%s" % entryFingerprint)
              if not nsCall: raise TorCtl.ErrorReply() # network consensus couldn't be fetched
              else: nsEntry = nsCall[0]
              
              descLookupCmd = "desc/id/%s" % entryFingerprint
              descEntry = TorCtl.Router.build_from_desc(self.conn.get_info(descLookupCmd)[descLookupCmd].split("\n"), nsEntry)
              toRemove = descEntry.down
            except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed): pass # ns or desc lookup fails... also weird
            
            # eliminates connections not reported by orconn-status -
            # this has *very* little impact since few ips have multiple relays
            if self.orconnStatusCache and not toRemove: toRemove = entryNickname not in self.orconnStatusCache
            
            if toRemove: operativeMatches.remove((entryPort, entryFingerprint, entryNickname))
          
          if len(operativeMatches) == 1: match = operativeMatches[0][1]
      
      if not match: match = "UNKNOWN"
      
      self.fingerprintLookupCache[(ipAddr, port)] = match
      return match
  
  def getNickname(self, ipAddr, port):
    """
    Attempts to provide the nickname for an ip/port combination, "UNKNOWN"
    if this can't be determined.
    """
    
    if (ipAddr, port) in self.nicknameLookupCache:
      return self.nicknameLookupCache[(ipAddr, port)]
    else:
      match = self.getFingerprint(ipAddr, port)
      
      try:
        if match != "UNKNOWN":
          nsCall = self.conn.get_network_status("id/%s" % match)
          if nsCall: match = nsCall[0].nickname
          else: raise TorCtl.ErrorReply # network consensus couldn't be fetched
      except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed): return "UNKNOWN" # don't cache result
      
      self.nicknameLookupCache[(ipAddr, port)] = match
      return match
  
  def setPaused(self, isPause):
    """
    If true, prevents connection listing from being updated.
    """
    
    if isPause == self.isPaused: return
    
    self.isPaused = isPause
    if isPause:
      self.pauseTime = time.time()
      self.connectionsBuffer = list(self.connections)
      self.connectionCountBuffer = list(self.connectionCount)
      self.familyResolutionsBuffer = dict(self.familyResolutions)
    else:
      self.connections = list(self.connectionsBuffer)
      self.connectionCount = list(self.connectionCountBuffer)
      self.familyResolutions = dict(self.familyResolutionsBuffer)
      
      # pause buffer connections may be unsorted
      if self.listingType != LIST_HOSTNAME: self.sortConnections()
  
  def sortConnections(self):
    """
    Sorts connections according to currently set ordering. This takes into
    account secondary and tertiary sub-keys in case of ties.
    """
    
    # Current implementation is very inefficient, but since connection lists
    # are decently small (count get up to arounk 1k) this shouldn't be a big
    # whoop. Suggestions for improvements are welcome!
    
    sorts = []
    
    # wrapper function for using current listed data (for 'LISTING' sorts)
    if self.listingType == LIST_IP:
      listingWrapper = lambda ip, port: _ipToInt(ip)
    elif self.listingType == LIST_HOSTNAME:
      # alphanumeric hostnames followed by unresolved IP addresses
      listingWrapper = lambda ip, port: _getHostname(ip).upper() if _getHostname(ip) else "zzzzz%099i" % _ipToInt(ip)
    elif self.listingType == LIST_FINGERPRINT:
      # alphanumeric fingerprints followed by UNKNOWN entries
      listingWrapper = lambda ip, port: self.getFingerprint(ip, port) if self.getFingerprint(ip, port) != "UNKNOWN" else "zzzzz%099i" % _ipToInt(ip)
    elif self.listingType == LIST_NICKNAME:
      # alphanumeric nicknames followed by Unnamed then UNKNOWN entries
      listingWrapper = lambda ip, port: self.getNickname(ip, port) if self.getNickname(ip, port) not in ("UNKNOWN", "Unnamed") else "zzzzz%i%099i" % (0 if self.getNickname(ip, port) == "Unnamed" else 1, _ipToInt(ip))
    
    for entry in self.sortOrdering:
      if entry == ORD_FOREIGN_LISTING:
        sorts.append(lambda x, y: cmp(listingWrapper(x[CONN_F_IP], x[CONN_F_PORT]), listingWrapper(y[CONN_F_IP], y[CONN_F_PORT])))
      elif entry == ORD_SRC_LISTING:
        sorts.append(lambda x, y: cmp(listingWrapper(x[CONN_F_IP] if x[CONN_TYPE] == "inbound" else x[CONN_L_IP], x[CONN_F_PORT]), listingWrapper(y[CONN_F_IP] if y[CONN_TYPE] == "inbound" else y[CONN_L_IP], y[CONN_F_PORT])))
      elif entry == ORD_DST_LISTING:
        sorts.append(lambda x, y: cmp(listingWrapper(x[CONN_L_IP] if x[CONN_TYPE] == "inbound" else x[CONN_F_IP], x[CONN_F_PORT]), listingWrapper(y[CONN_L_IP] if y[CONN_TYPE] == "inbound" else y[CONN_F_IP], y[CONN_F_PORT])))
      else: sorts.append(SORT_TYPES[entry][2])
    
    self.connectionsLock.acquire()
    try: self.connections.sort(lambda x, y: _multisort(x, y, sorts))
    finally: self.connectionsLock.release()

  def _resolveFamilyEntries(self):
    """
    Populates mappings of the torrc family entries to their fingerprints.
    """
    
    self.familyFingerprints = {}
    
    for familyEntry in self.family:
      if familyEntry[0] == "$":
        # relay identified by fingerprint
        self.familyFingerprints[familyEntry] = familyEntry[1:]
      else:
        # relay identified by nickname
        descEntry = torTools.getConn().getInfo("desc/name/%s" % familyEntry)
        
        if descEntry:
          fingerprintStart = descEntry.find("opt fingerprint") + 16
          fingerprintEnd = descEntry.find("\n", fingerprintStart)
          fingerprint = descEntry[fingerprintStart:fingerprintEnd].replace(" ", "")
          
          self.familyFingerprints[familyEntry] = fingerprint

# recursively checks primary, secondary, and tertiary sorting parameter in ties
def _multisort(conn1, conn2, sorts):
  comp = sorts[0](conn1, conn2)
  if comp or len(sorts) == 1: return comp
  else: return _multisort(conn1, conn2, sorts[1:])

def _getHostname(ipAddr):
  try: return hostnames.resolve(ipAddr)
  except ValueError: return None

# provides comparison int for sorting IP addresses
def _ipToInt(ipAddr):
  total = 0
  for comp in ipAddr.split("."):
    total *= 255
    total += int(comp)
  return total

# uses consensus data to map IP addresses to port / fingerprint combinations
def _getFingerprintMappings(conn, nsList = None):
  ipToFingerprint = {}
  
  if not nsList:
    try: nsList = conn.get_network_status()
    except (socket.error, TorCtl.TorCtlClosed, TorCtl.ErrorReply): nsList = []
    except TypeError: nsList = [] # TODO: temporary workaround for a TorCtl bug, remove when fixed
  
  for entry in nsList:
    if entry.ip in ipToFingerprint.keys(): ipToFingerprint[entry.ip].append((entry.orport, entry.idhex, entry.nickname))
    else: ipToFingerprint[entry.ip] = [(entry.orport, entry.idhex, entry.nickname)]
  
  return ipToFingerprint

# provides client relays we're currently attached to (first hops in circuits)
# this consists of the nicknames and ${fingerprint} if unnamed
def _getClientConnections(conn):
  clients = []
  
  try:
    for line in conn.get_info("circuit-status")["circuit-status"].split("\n"):
      components = line.split()
      if len(components) > 3: clients += [components[2].split(",")[0]]
  except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed): pass
  
  return clients

def isExitAllowed(ip, port, exitPolicy, isPrivateRejected):
  """
  Determines if a given connection is a permissable exit with the given 
  policy or not (True if it's allowed to be an exit connection, False 
  otherwise).
  
  NOTE: this is a little tricky and liable to need some tweaks
  """
  
  # might not be set when first starting up
  if not exitPolicy: return True
  
  # TODO: move into a utility and craft some unit tests (this is very error 
  # prone...)
  
  # TODO: currently doesn't consider ExitPolicyRejectPrivate (which prevents 
  # connections to private networks and local ip)
  for entry in exitPolicy.split(","):
    entry = entry.strip()
    
    isAccept = entry.startswith("accept")
    entry = entry[7:] # strips off "accept " or "reject "
    
    # parses ip address (with mask if provided) and port
    if ":" in entry:
      entryIP = entry[:entry.find(":")]
      entryPort = entry[entry.find(":") + 1:]
    else:
      entryIP = entry
      entryPort = "*"
    
    #raise AssertionError(str(exitPolicy) + " - " + entryIP + ":" + entryPort)
    isIPMatch = entryIP == ip or entryIP[0] == "*"
    
    if not "-" in entryPort:
      # single port
      isPortMatch = entryPort == str(port) or entryPort[0] == "*"
    else:
      # port range
      minPort = int(entryPort[:entryPort.find("-")])
      maxPort = int(entryPort[entryPort.find("-") + 1:])
      isPortMatch = port >= minPort and port <= maxPort
    
    # TODO: Currently being lazy and considering subnet masks or 'private' 
    # keyword to be equivilant to wildcard if it would reject, and none 
    # if it would accept (ie, being conservative with acceptance). Would be 
    # nice to fix at some point.
    if not isAccept: isIPMatch |= "/" in entryIP or entryIP == "private"
    
    if isIPMatch and isPortMatch: return isAccept
  
  # we shouldn't ever fall through due to default exit policy
  log.log(log.WARN, "Exit policy left connection uncategorized: %s:%i" % (ip, port))
  return False

