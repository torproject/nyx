"""
Connection panel entries related to actual connections to or from the system
(ie, results seen by netstat, lsof, etc).
"""

import time
import curses

from util import connections, enum, torTools, uiTools
from cli.connections import entries

# Connection Categories:
#   Inbound      Relay connection, coming to us.
#   Outbound     Relay connection, leaving us.
#   Exit         Outbound relay connection leaving the Tor network.
#   Hidden       Connections to a hidden service we're providing.
#   Socks        Socks connections for applications using Tor.
#   Circuit      Circuits our tor client has created.
#   Directory    Fetching tor consensus information.
#   Control      Tor controller (arm, vidalia, etc).

Category = enum.Enum("INBOUND", "OUTBOUND", "EXIT", "HIDDEN", "SOCKS", "CIRCUIT", "DIRECTORY", "CONTROL")
CATEGORY_COLOR = {Category.INBOUND: "green",      Category.OUTBOUND: "blue",
                  Category.EXIT: "red",           Category.HIDDEN: "magenta",
                  Category.SOCKS: "yellow",       Category.CIRCUIT: "cyan",
                  Category.DIRECTORY: "magenta",  Category.CONTROL: "red"}

# static data for listing format
# <src>  -->  <dst>  <etc><padding>
LABEL_FORMAT = "%s  -->  %s  %s%s"
LABEL_MIN_PADDING = 2 # min space between listing label and following data

# sort value for scrubbed ip addresses
SCRUBBED_IP_VAL = 255 ** 4

CONFIG = {"features.connection.markInitialConnections": True,
          "features.connection.showIps": True,
          "features.connection.showExitPort": True,
          "features.connection.showColumn.fingerprint": True,
          "features.connection.showColumn.nickname": True,
          "features.connection.showColumn.destination": True,
          "features.connection.showColumn.expandedIp": True}

def loadConfig(config):
  config.update(CONFIG)

class Endpoint:
  """
  Collection of attributes associated with a connection endpoint. This is a
  thin wrapper for torUtil functions, making use of its caching for
  performance.
  """
  
  def __init__(self, ipAddr, port):
    self.ipAddr = ipAddr
    self.port = port
    
    # if true, we treat the port as an definitely not being an ORPort when
    # searching for matching fingerprints (otherwise we use it to possably
    # narrow results when unknown)
    self.isNotORPort = True
    
    # if set then this overwrites fingerprint lookups
    self.fingerprintOverwrite = None
  
  def getIpAddr(self):
    """
    Provides the IP address of the endpoint.
    """
    
    return self.ipAddr
  
  def getPort(self):
    """
    Provides the port of the endpoint.
    """
    
    return self.port
  
  def getHostname(self, default = None):
    """
    Provides the hostname associated with the relay's address. This is a
    non-blocking call and returns None if the address either can't be resolved
    or hasn't been resolved yet.
    
    Arguments:
      default - return value if no hostname is available
    """
    
    # TODO: skipping all hostname resolution to be safe for now
    #try:
    #  myHostname = hostnames.resolve(self.ipAddr)
    #except:
    #  # either a ValueError or IOError depending on the source of the lookup failure
    #  myHostname = None
    #
    #if not myHostname: return default
    #else: return myHostname
    
    return default
  
  def getLocale(self, default=None):
    """
    Provides the two letter country code for the IP address' locale.
    
    Arguments:
      default - return value if no locale information is available
    """
    
    conn = torTools.getConn()
    return conn.getInfo("ip-to-country/%s" % self.ipAddr, default)
  
  def getFingerprint(self):
    """
    Provides the fingerprint of the relay, returning "UNKNOWN" if it can't be
    determined.
    """
    
    if self.fingerprintOverwrite:
      return self.fingerprintOverwrite
    
    conn = torTools.getConn()
    myFingerprint = conn.getRelayFingerprint(self.ipAddr)
    
    # If there were multiple matches and our port is likely the ORPort then
    # try again with that to narrow the results.
    if not myFingerprint and not self.isNotORPort:
      myFingerprint = conn.getRelayFingerprint(self.ipAddr, self.port)
    
    if myFingerprint: return myFingerprint
    else: return "UNKNOWN"
  
  def getNickname(self):
    """
    Provides the nickname of the relay, retuning "UNKNOWN" if it can't be
    determined.
    """
    
    myFingerprint = self.getFingerprint()
    
    if myFingerprint != "UNKNOWN":
      conn = torTools.getConn()
      myNickname = conn.getRelayNickname(myFingerprint)
      
      if myNickname: return myNickname
      else: return "UNKNOWN"
    else: return "UNKNOWN"

class ConnectionEntry(entries.ConnectionPanelEntry):
  """
  Represents a connection being made to or from this system. These only
  concern real connections so it includes the inbound, outbound, directory,
  application, and controller categories.
  """
  
  def __init__(self, lIpAddr, lPort, fIpAddr, fPort):
    entries.ConnectionPanelEntry.__init__(self)
    self.lines = [ConnectionLine(lIpAddr, lPort, fIpAddr, fPort)]
  
  def getSortValue(self, attr, listingType):
    """
    Provides the value of a single attribute used for sorting purposes.
    """
    
    connLine = self.lines[0]
    if attr == entries.SortAttr.IP_ADDRESS:
      if connLine.isPrivate(): return SCRUBBED_IP_VAL # orders at the end
      return connLine.sortIpAddr
    elif attr == entries.SortAttr.PORT:
      return connLine.sortPort
    elif attr == entries.SortAttr.HOSTNAME:
      if connLine.isPrivate(): return ""
      return connLine.foreign.getHostname("")
    elif attr == entries.SortAttr.FINGERPRINT:
      return connLine.foreign.getFingerprint()
    elif attr == entries.SortAttr.NICKNAME:
      myNickname = connLine.foreign.getNickname()
      if myNickname == "UNKNOWN": return "z" * 20 # orders at the end
      else: return myNickname.lower()
    elif attr == entries.SortAttr.CATEGORY:
      return Category.indexOf(connLine.getType())
    elif attr == entries.SortAttr.UPTIME:
      return connLine.startTime
    elif attr == entries.SortAttr.COUNTRY:
      if connections.isIpAddressPrivate(self.lines[0].foreign.getIpAddr()): return ""
      else: return connLine.foreign.getLocale("")
    else:
      return entries.ConnectionPanelEntry.getSortValue(self, attr, listingType)

class ConnectionLine(entries.ConnectionPanelLine):
  """
  Display component of the ConnectionEntry.
  """
  
  def __init__(self, lIpAddr, lPort, fIpAddr, fPort, includePort=True, includeExpandedIpAddr=True):
    entries.ConnectionPanelLine.__init__(self)
    
    self.local = Endpoint(lIpAddr, lPort)
    self.foreign = Endpoint(fIpAddr, fPort)
    self.startTime = time.time()
    self.isInitialConnection = False
    
    # overwrite the local fingerprint with ours
    conn = torTools.getConn()
    self.local.fingerprintOverwrite = conn.getInfo("fingerprint")
    
    # True if the connection has matched the properties of a client/directory
    # connection every time we've checked. The criteria we check is...
    #   client    - first hop in an established circuit
    #   directory - matches an established single-hop circuit (probably a
    #               directory mirror)
    
    self._possibleClient = True
    self._possibleDirectory = True
    
    # attributes for SOCKS, HIDDEN, and CONTROL connections
    self.appName = None
    self.appPid = None
    self.isAppResolving = False
    
    myOrPort = conn.getOption("ORPort")
    myDirPort = conn.getOption("DirPort")
    mySocksPort = conn.getOption("SocksPort", "9050")
    myCtlPort = conn.getOption("ControlPort")
    myHiddenServicePorts = conn.getHiddenServicePorts()
    
    # the ORListenAddress can overwrite the ORPort
    listenAddr = conn.getOption("ORListenAddress")
    if listenAddr and ":" in listenAddr:
      myOrPort = listenAddr[listenAddr.find(":") + 1:]
    
    if lPort in (myOrPort, myDirPort):
      self.baseType = Category.INBOUND
      self.local.isNotORPort = False
    elif lPort == mySocksPort:
      self.baseType = Category.SOCKS
    elif fPort in myHiddenServicePorts:
      self.baseType = Category.HIDDEN
    elif lPort == myCtlPort:
      self.baseType = Category.CONTROL
    else:
      self.baseType = Category.OUTBOUND
      self.foreign.isNotORPort = False
    
    self.cachedType = None
    
    # includes the port or expanded ip address field when displaying listing
    # information if true
    self.includePort = includePort
    self.includeExpandedIpAddr = includeExpandedIpAddr
    
    # cached immutable values used for sorting
    self.sortIpAddr = connections.ipToInt(self.foreign.getIpAddr())
    self.sortPort = int(self.foreign.getPort())
  
  def getListingEntry(self, width, currentTime, listingType):
    """
    Provides the tuple list for this connection's listing. Lines are composed
    of the following components:
      <src>  -->  <dst>     <etc>     <uptime> (<type>)
    
    ListingType.IP_ADDRESS:
      src - <internal addr:port> --> <external addr:port>
      dst - <destination addr:port>
      etc - <fingerprint> <nickname>
    
    ListingType.HOSTNAME:
      src - localhost:<port>
      dst - <destination hostname:port>
      etc - <destination addr:port> <fingerprint> <nickname>
    
    ListingType.FINGERPRINT:
      src - localhost
      dst - <destination fingerprint>
      etc - <nickname> <destination addr:port>
    
    ListingType.NICKNAME:
      src - <source nickname>
      dst - <destination nickname>
      etc - <fingerprint> <destination addr:port>
    
    Arguments:
      width       - maximum length of the line
      currentTime - unix timestamp for what the results should consider to be
                    the current time
      listingType - primary attribute we're listing connections by
    """
    
    # fetch our (most likely cached) display entry for the listing
    myListing = entries.ConnectionPanelLine.getListingEntry(self, width, currentTime, listingType)
    
    # fill in the current uptime and return the results
    if CONFIG["features.connection.markInitialConnections"]:
      timePrefix = "+" if self.isInitialConnection else " "
    else: timePrefix = ""
    
    timeLabel = timePrefix + "%5s" % uiTools.getTimeLabel(currentTime - self.startTime, 1)
    myListing[2] = (timeLabel, myListing[2][1])
    
    return myListing
  
  def isUnresolvedApp(self):
    """
    True if our display uses application information that hasn't yet been resolved.
    """
    
    return self.appName == None and self.getType() in (Category.SOCKS, Category.HIDDEN, Category.CONTROL)
  
  def _getListingEntry(self, width, currentTime, listingType):
    entryType = self.getType()
    
    # Lines are split into the following components in reverse:
    # init gap - " "
    # content  - "<src>  -->  <dst>     <etc>     "
    # time     - "<uptime>"
    # preType  - " ("
    # category - "<type>"
    # postType - ")   "
    
    lineFormat = uiTools.getColor(CATEGORY_COLOR[entryType])
    timeWidth = 6 if CONFIG["features.connection.markInitialConnections"] else 5
    
    drawEntry = [(" ", lineFormat),
                 (self._getListingContent(width - (12 + timeWidth) - 1, listingType), lineFormat),
                 (" " * timeWidth, lineFormat),
                 (" (", lineFormat),
                 (entryType.upper(), lineFormat | curses.A_BOLD),
                 (")" + " " * (9 - len(entryType)), lineFormat)]
    return drawEntry
  
  def _getDetails(self, width):
    """
    Provides details on the connection, correlated against available consensus
    data.
    
    Arguments:
      width - available space to display in
    """
    
    detailFormat = curses.A_BOLD | uiTools.getColor(CATEGORY_COLOR[self.getType()])
    return [(line, detailFormat) for line in self._getDetailContent(width)]
  
  def resetDisplay(self):
    entries.ConnectionPanelLine.resetDisplay(self)
    self.cachedType = None
  
  def isPrivate(self):
    """
    Returns true if the endpoint is private, possibly belonging to a client
    connection or exit traffic.
    """
    
    if not CONFIG["features.connection.showIps"]: return True
    
    # This is used to scrub private information from the interface. Relaying
    # etiquette (and wiretapping laws) say these are bad things to look at so
    # DON'T CHANGE THIS UNLESS YOU HAVE A DAMN GOOD REASON!
    
    myType = self.getType()
    
    if myType == Category.INBOUND:
      # if we're a guard or bridge and the connection doesn't belong to a
      # known relay then it might be client traffic
      
      conn = torTools.getConn()
      if "Guard" in conn.getMyFlags([]) or conn.getOption("BridgeRelay") == "1":
        allMatches = conn.getRelayFingerprint(self.foreign.getIpAddr(), getAllMatches = True)
        return allMatches == []
    elif myType == Category.EXIT:
      # DNS connections exiting us aren't private (since they're hitting our
      # resolvers). Everything else, however, is.
      
      # TODO: Ideally this would also double check that it's a UDP connection
      # (since DNS is the only UDP connections Tor will relay), however this
      # will take a bit more work to propagate the information up from the
      # connection resolver.
      return self.foreign.getPort() != "53"
    
    # for everything else this isn't a concern
    return False
  
  def getType(self):
    """
    Provides our best guess at the current type of the connection. This
    depends on consensus results, our current client circuits, etc. Results
    are cached until this entry's display is reset.
    """
    
    # caches both to simplify the calls and to keep the type consistent until
    # we want to reflect changes
    if not self.cachedType:
      if self.baseType == Category.OUTBOUND:
        # Currently the only non-static categories are OUTBOUND vs...
        # - EXIT since this depends on the current consensus
        # - CIRCUIT if this is likely to belong to our guard usage
        # - DIRECTORY if this is a single-hop circuit (directory mirror?)
        # 
        # The exitability, circuits, and fingerprints are all cached by the
        # torTools util keeping this a quick lookup.
        
        conn = torTools.getConn()
        destFingerprint = self.foreign.getFingerprint()
        
        if destFingerprint == "UNKNOWN":
          # Not a known relay. This might be an exit connection.
          
          if conn.isExitingAllowed(self.foreign.getIpAddr(), self.foreign.getPort()):
            self.cachedType = Category.EXIT
        elif self._possibleClient or self._possibleDirectory:
          # This belongs to a known relay. If we haven't eliminated ourselves as
          # a possible client or directory connection then check if it still
          # holds true.
          
          myCircuits = conn.getCircuits()
          
          if self._possibleClient:
            # Checks that this belongs to the first hop in a circuit that's
            # either unestablished or longer than a single hop (ie, anything but
            # a built 1-hop connection since those are most likely a directory
            # mirror).
            
            for _, status, _, path in myCircuits:
              if path[0] == destFingerprint and (status != "BUILT" or len(path) > 1):
                self.cachedType = Category.CIRCUIT # matched a probable guard connection
            
            # if we fell through, we can eliminate ourselves as a guard in the future
            if not self.cachedType:
              self._possibleClient = False
          
          if self._possibleDirectory:
            # Checks if we match a built, single hop circuit.
            
            for _, status, _, path in myCircuits:
              if path[0] == destFingerprint and status == "BUILT" and len(path) == 1:
                self.cachedType = Category.DIRECTORY
            
            # if we fell through, eliminate ourselves as a directory connection
            if not self.cachedType:
              self._possibleDirectory = False
      
      if not self.cachedType:
        self.cachedType = self.baseType
    
    return self.cachedType
  
  def getEtcContent(self, width, listingType):
    """
    Provides the optional content for the connection.
    
    Arguments:
      width       - maximum length of the line
      listingType - primary attribute we're listing connections by
    """
    
    # for applications show the command/pid
    if self.getType() in (Category.SOCKS, Category.HIDDEN, Category.CONTROL):
      displayLabel = ""
      
      if self.appName:
        if self.appPid: displayLabel = "%s (%s)" % (self.appName, self.appPid)
        else: displayLabel = self.appName
      elif self.isAppResolving:
        displayLabel = "resolving..."
      else: displayLabel = "UNKNOWN"
      
      if len(displayLabel) < width:
        return ("%%-%is" % width) % displayLabel
      else: return ""
    
    # for everything else display connection/consensus information
    dstAddress = self.getDestinationLabel(26, includeLocale = True)
    etc, usedSpace = "", 0
    if listingType == entries.ListingType.IP_ADDRESS:
      if width > usedSpace + 42 and CONFIG["features.connection.showColumn.fingerprint"]:
        # show fingerprint (column width: 42 characters)
        etc += "%-40s  " % self.foreign.getFingerprint()
        usedSpace += 42
      
      if width > usedSpace + 10 and CONFIG["features.connection.showColumn.nickname"]:
        # show nickname (column width: remainder)
        nicknameSpace = width - usedSpace
        nicknameLabel = uiTools.cropStr(self.foreign.getNickname(), nicknameSpace, 0)
        etc += ("%%-%is  " % nicknameSpace) % nicknameLabel
        usedSpace += nicknameSpace + 2
    elif listingType == entries.ListingType.HOSTNAME:
      if width > usedSpace + 28 and CONFIG["features.connection.showColumn.destination"]:
        # show destination ip/port/locale (column width: 28 characters)
        etc += "%-26s  " % dstAddress
        usedSpace += 28
      
      if width > usedSpace + 42 and CONFIG["features.connection.showColumn.fingerprint"]:
        # show fingerprint (column width: 42 characters)
        etc += "%-40s  " % self.foreign.getFingerprint()
        usedSpace += 42
      
      if width > usedSpace + 17 and CONFIG["features.connection.showColumn.nickname"]:
        # show nickname (column width: min 17 characters, uses half of the remainder)
        nicknameSpace = 15 + (width - (usedSpace + 17)) / 2
        nicknameLabel = uiTools.cropStr(self.foreign.getNickname(), nicknameSpace, 0)
        etc += ("%%-%is  " % nicknameSpace) % nicknameLabel
        usedSpace += (nicknameSpace + 2)
    elif listingType == entries.ListingType.FINGERPRINT:
      if width > usedSpace + 17:
        # show nickname (column width: min 17 characters, consumes any remaining space)
        nicknameSpace = width - usedSpace - 2
        
        # if there's room then also show a column with the destination
        # ip/port/locale (column width: 28 characters)
        isIpLocaleIncluded = width > usedSpace + 45
        isIpLocaleIncluded &= CONFIG["features.connection.showColumn.destination"]
        if isIpLocaleIncluded: nicknameSpace -= 28
        
        if CONFIG["features.connection.showColumn.nickname"]:
          nicknameLabel = uiTools.cropStr(self.foreign.getNickname(), nicknameSpace, 0)
          etc += ("%%-%is  " % nicknameSpace) % nicknameLabel
          usedSpace += nicknameSpace + 2
        
        if isIpLocaleIncluded:
          etc += "%-26s  " % dstAddress
          usedSpace += 28
    else:
      if width > usedSpace + 42 and CONFIG["features.connection.showColumn.fingerprint"]:
        # show fingerprint (column width: 42 characters)
        etc += "%-40s  " % self.foreign.getFingerprint()
        usedSpace += 42
      
      if width > usedSpace + 28 and CONFIG["features.connection.showColumn.destination"]:
        # show destination ip/port/locale (column width: 28 characters)
        etc += "%-26s  " % dstAddress
        usedSpace += 28
    
    return ("%%-%is" % width) % etc
  
  def _getListingContent(self, width, listingType):
    """
    Provides the source, destination, and extra info for our listing.
    
    Arguments:
      width       - maximum length of the line
      listingType - primary attribute we're listing connections by
    """
    
    conn = torTools.getConn()
    myType = self.getType()
    dstAddress = self.getDestinationLabel(26, includeLocale = True)
    
    # The required widths are the sum of the following:
    # - room for LABEL_FORMAT and LABEL_MIN_PADDING (11 characters)
    # - base data for the listing
    # - that extra field plus any previous
    
    usedSpace = len(LABEL_FORMAT % tuple([""] * 4)) + LABEL_MIN_PADDING
    localPort = ":%s" % self.local.getPort() if self.includePort else ""
    
    src, dst, etc = "", "", ""
    if listingType == entries.ListingType.IP_ADDRESS:
      myExternalIpAddr = conn.getInfo("address", self.local.getIpAddr())
      addrDiffer = myExternalIpAddr != self.local.getIpAddr()
      
      # Expanding doesn't make sense, if the connection isn't actually
      # going through Tor's external IP address. As there isn't a known
      # method for checking if it is, we're checking the type instead.
      #
      # This isn't entirely correct. It might be a better idea to check if
      # the source and destination addresses are both private, but that might
      # not be perfectly reliable either.
      
      isExpansionType = not myType in (Category.SOCKS, Category.HIDDEN, Category.CONTROL)
      
      if isExpansionType: srcAddress = myExternalIpAddr + localPort
      else: srcAddress = self.local.getIpAddr() + localPort
      
      if myType in (Category.SOCKS, Category.CONTROL):
        # Like inbound connections these need their source and destination to
        # be swapped. However, this only applies when listing by IP or hostname
        # (their fingerprint and nickname are both for us). Reversing the
        # fields here to keep the same column alignments.
        
        src = "%-21s" % dstAddress
        dst = "%-26s" % srcAddress
      else:
        src = "%-21s" % srcAddress # ip:port = max of 21 characters
        dst = "%-26s" % dstAddress # ip:port (xx) = max of 26 characters
      
      usedSpace += len(src) + len(dst) # base data requires 47 characters
      
      # Showing the fingerprint (which has the width of 42) has priority over
      # an expanded address field. Hence check if we either have space for
      # both or wouldn't be showing the fingerprint regardless.
      
      isExpandedAddrVisible = width > usedSpace + 28
      if isExpandedAddrVisible and CONFIG["features.connection.showColumn.fingerprint"]:
        isExpandedAddrVisible = width < usedSpace + 42 or width > usedSpace + 70
      
      if addrDiffer and isExpansionType and isExpandedAddrVisible and self.includeExpandedIpAddr and CONFIG["features.connection.showColumn.expandedIp"]:
        # include the internal address in the src (extra 28 characters)
        internalAddress = self.local.getIpAddr() + localPort
        
        # If this is an inbound connection then reverse ordering so it's:
        # <foreign> --> <external> --> <internal>
        # when the src and dst are swapped later
        
        if myType == Category.INBOUND: src = "%-21s  -->  %s" % (src, internalAddress)
        else: src = "%-21s  -->  %s" % (internalAddress, src)
        
        usedSpace += 28
      
      etc = self.getEtcContent(width - usedSpace, listingType)
      usedSpace += len(etc)
    elif listingType == entries.ListingType.HOSTNAME:
      # 15 characters for source, and a min of 40 reserved for the destination
      # TODO: when actually functional the src and dst need to be swapped for
      # SOCKS and CONTROL connections
      src = "localhost%-6s" % localPort
      usedSpace += len(src)
      minHostnameSpace = 40
      
      etc = self.getEtcContent(width - usedSpace - minHostnameSpace, listingType)
      usedSpace += len(etc)
      
      hostnameSpace = width - usedSpace
      usedSpace = width # prevents padding at the end
      if self.isPrivate():
        dst = ("%%-%is" % hostnameSpace) % "<scrubbed>"
      else:
        hostname = self.foreign.getHostname(self.foreign.getIpAddr())
        portLabel = ":%-5s" % self.foreign.getPort() if self.includePort else ""
        
        # truncates long hostnames and sets dst to <hostname>:<port>
        hostname = uiTools.cropStr(hostname, hostnameSpace, 0)
        dst = ("%%-%is" % hostnameSpace) % (hostname + portLabel)
    elif listingType == entries.ListingType.FINGERPRINT:
      src = "localhost"
      if myType == Category.CONTROL: dst = "localhost"
      else: dst = self.foreign.getFingerprint()
      dst = "%-40s" % dst
      
      usedSpace += len(src) + len(dst) # base data requires 49 characters
      
      etc = self.getEtcContent(width - usedSpace, listingType)
      usedSpace += len(etc)
    else:
      # base data requires 50 min characters
      src = self.local.getNickname()
      if myType == Category.CONTROL: dst = self.local.getNickname()
      else: dst = self.foreign.getNickname()
      minBaseSpace = 50
      
      etc = self.getEtcContent(width - usedSpace - minBaseSpace, listingType)
      usedSpace += len(etc)
      
      baseSpace = width - usedSpace
      usedSpace = width # prevents padding at the end
      
      if len(src) + len(dst) > baseSpace:
        src = uiTools.cropStr(src, baseSpace / 3)
        dst = uiTools.cropStr(dst, baseSpace - len(src))
      
      # pads dst entry to its max space
      dst = ("%%-%is" % (baseSpace - len(src))) % dst
    
    if myType == Category.INBOUND: src, dst = dst, src
    padding = " " * (width - usedSpace + LABEL_MIN_PADDING)
    return LABEL_FORMAT % (src, dst, etc, padding)
  
  def _getDetailContent(self, width):
    """
    Provides a list with detailed information for this connection.
    
    Arguments:
      width - max length of lines
    """
    
    lines = [""] * 7
    lines[0] = "address: %s" % self.getDestinationLabel(width - 11)
    lines[1] = "locale: %s" % ("??" if self.isPrivate() else self.foreign.getLocale("??"))
    
    # Remaining data concerns the consensus results, with three possible cases:
    # - if there's a single match then display its details
    # - if there's multiple potential relays then list all of the combinations
    #   of ORPorts / Fingerprints
    # - if no consensus data is available then say so (probably a client or
    #   exit connection)
    
    fingerprint = self.foreign.getFingerprint()
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
        
        flags = "unknown"
        if len(nsLines) >= 2 and nsLines[1].startswith("s "):
          flags = nsLines[1][2:]
        
        exitPolicy = conn.getRelayExitPolicy(fingerprint)
        
        if exitPolicy: policyLabel = exitPolicy.getSummary()
        else: policyLabel = "unknown"
        
        dirPortLabel = "" if dirPort == "0" else "dirport: %s" % dirPort
        lines[2] = "nickname: %-25s orport: %-10s %s" % (nickname, orPort, dirPortLabel)
        lines[3] = "published: %s %s" % (pubTime, pubDate)
        lines[4] = "flags: %s" % flags.replace(" ", ", ")
        lines[5] = "exit policy: %s" % policyLabel
      
      if descEntry:
        torVersion, platform, contact = "", "", ""
        
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
        
        lines[3] = "%-35s os: %-14s version: %s" % (lines[3], platform, torVersion)
        
        # contact information is an optional field
        if contact: lines[6] = "contact: %s" % contact
    else:
      allMatches = conn.getRelayFingerprint(self.foreign.getIpAddr(), getAllMatches = True)
      
      if allMatches:
        # multiple matches
        lines[2] = "Multiple matches, possible fingerprints are:"
        
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
    
    # crops any lines that are too long
    for i in range(len(lines)):
      lines[i] = uiTools.cropStr(lines[i], width - 2)
    
    return lines
  
  def getDestinationLabel(self, maxLength, includeLocale=False, includeHostname=False):
    """
    Provides a short description of the destination. This is made up of two
    components, the base <ip addr>:<port> and an extra piece of information in
    parentheses. The IP address is scrubbed from private connections.
    
    Extra information is...
    - the port's purpose for exit connections
    - the locale and/or hostname if set to do so, the address isn't private,
      and isn't on the local network
    - nothing otherwise
    
    Arguments:
      maxLength       - maximum length of the string returned
      includeLocale   - possibly includes the locale
      includeHostname - possibly includes the hostname
    """
    
    # the port and port derived data can be hidden by config or without includePort
    includePort = self.includePort and (CONFIG["features.connection.showExitPort"] or self.getType() != Category.EXIT)
    
    # destination of the connection
    ipLabel = "<scrubbed>" if self.isPrivate() else self.foreign.getIpAddr()
    portLabel = ":%s" % self.foreign.getPort() if includePort else ""
    dstAddress = ipLabel + portLabel
    
    # Only append the extra info if there's at least a couple characters of
    # space (this is what's needed for the country codes).
    if len(dstAddress) + 5 <= maxLength:
      spaceAvailable = maxLength - len(dstAddress) - 3
      
      if self.getType() == Category.EXIT and includePort:
        purpose = connections.getPortUsage(self.foreign.getPort())
        
        if purpose:
          # BitTorrent is a common protocol to truncate, so just use "Torrent"
          # if there's not enough room.
          if len(purpose) > spaceAvailable and purpose == "BitTorrent":
            purpose = "Torrent"
          
          # crops with a hyphen if too long
          purpose = uiTools.cropStr(purpose, spaceAvailable, endType = uiTools.Ending.HYPHEN)
          
          dstAddress += " (%s)" % purpose
      elif not connections.isIpAddressPrivate(self.foreign.getIpAddr()):
        extraInfo = []
        conn = torTools.getConn()
        
        if includeLocale and not conn.isGeoipUnavailable():
          foreignLocale = self.foreign.getLocale("??")
          extraInfo.append(foreignLocale)
          spaceAvailable -= len(foreignLocale) + 2
        
        if includeHostname:
          dstHostname = self.foreign.getHostname()
          
          if dstHostname:
            # determines the full space available, taking into account the ", "
            # dividers if there's multiple pieces of extra data
            
            maxHostnameSpace = spaceAvailable - 2 * len(extraInfo)
            dstHostname = uiTools.cropStr(dstHostname, maxHostnameSpace)
            extraInfo.append(dstHostname)
            spaceAvailable -= len(dstHostname)
        
        if extraInfo:
          dstAddress += " (%s)" % ", ".join(extraInfo)
    
    return dstAddress[:maxLength]

