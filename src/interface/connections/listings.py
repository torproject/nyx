"""
Entries for connections related to the Tor process.
"""

import time

from util import connections, enum, hostnames, torTools, uiTools

# Connection Categories:
#   Inbound      Relay connection, coming to us.
#   Outbound     Relay connection, leaving us.
#   Exit         Outbound relay connection leaving the Tor network.
#   Client       Circuits for our client traffic.
#   Application  Socks connections using Tor.
#   Directory    Fetching tor consensus information.
#   Control      Tor controller (arm, vidalia, etc).

DestAttr = enum.Enum("NONE", "LOCALE", "HOSTNAME")
Category = enum.Enum("INBOUND", "OUTBOUND", "EXIT", "CLIENT", "APPLICATION", "DIRECTORY", "CONTROL")
CATEGORY_COLOR = {Category.INBOUND: "green",      Category.OUTBOUND: "blue",
                  Category.EXIT: "red",           Category.CLIENT: "cyan",
                  Category.APPLICATION: "yellow", Category.DIRECTORY: "magenta",
                  Category.CONTROL: "red"}

# static data for listing format
# <src>  -->  <dst>  <etc><padding>
LABEL_FORMAT = "%s  -->  %s  %s%s"
LABEL_MIN_PADDING = 2 # min space between listing label and following data

CONFIG = {"features.connection.showColumn.fingerprint": True,
          "features.connection.showColumn.nickname": True,
          "features.connection.showColumn.destination": True,
          "features.connection.showColumn.expanedIp": True}

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
    
    # if true, we treat the port as an ORPort when searching for matching
    # fingerprints (otherwise the ORPort is assumed to be unknown)
    self.isORPort = False
  
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
    
    myHostname = hostnames.resolve(self.ipAddr)
    if not myHostname: return default
    else: return myHostname
  
  def getLocale(self):
    """
    Provides the two letter country code for the IP address' locale. This
    proivdes None if it can't be determined.
    """
    
    conn = torTools.getConn()
    return conn.getInfo("ip-to-country/%s" % self.ipAddr)
  
  def getFingerprint(self):
    """
    Provides the fingerprint of the relay, returning "UNKNOWN" if it can't be
    determined.
    """
    
    conn = torTools.getConn()
    orPort = self.port if self.isORPort else None
    myFingerprint = conn.getRelayFingerprint(self.ipAddr, orPort)
    
    if myFingerprint: return myFingerprint
    else: return "UNKNOWN"
  
  def getNickname(self):
    """
    Provides the nickname of the relay, retuning "UNKNOWN" if it can't be
    determined.
    """
    
    conn = torTools.getConn()
    orPort = self.port if self.isORPort else None
    myFingerprint = conn.getRelayFingerprint(self.ipAddr, orPort)
    
    if myFingerprint: return conn.getRelayNickname(myFingerprint)
    else: return "UNKNOWN"

class ConnectionEntry:
  """
  Represents a connection being made to or from this system. These only
  concern real connections so it only includes the inbound, outbound,
  directory, application, and controller categories.
  """
  
  def __init__(self, lIpAddr, lPort, fIpAddr, fPort):
    self.local = Endpoint(lIpAddr, lPort)
    self.foreign = Endpoint(fIpAddr, fPort)
    self.startTime = time.time()
    
    self._labelCache = ""
    self._labelCacheArgs = (None, None)
    
    # True if the connection has matched the properties of a client/directory
    # connection every time we've checked. The criteria we check is...
    #   client    - first hop in an established circuit
    #   directory - matches an established single-hop circuit (probably a
    #               directory mirror)
    
    self._possibleClient = True
    self._possibleDirectory = True
    
    conn = torTools.getConn()
    myOrPort = conn.getOption("ORPort")
    myDirPort = conn.getOption("DirPort")
    mySocksPort = conn.getOption("SocksPort", "9050")
    myCtlPort = conn.getOption("ControlPort")
    myAuthorities = conn.getMyDirAuthorities()
    
    # the ORListenAddress can overwrite the ORPort
    listenAddr = conn.getOption("ORListenAddress")
    if listenAddr and ":" in listenAddr:
      myOrPort = listenAddr[listenAddr.find(":") + 1:]
    
    if lPort in (myOrPort, myDirPort):
      self.baseType = Category.INBOUND
      self.local.isORPort = True
    elif lPort == mySocksPort:
      self.baseType = Category.APPLICATION
    elif lPort == myCtlPort:
      self.baseType = Category.CONTROL
    elif (fIpAddr, fPort) in myAuthorities:
      self.baseType = Category.DIRECTORY
    else:
      self.baseType = Category.OUTBOUND
      self.foreign.isORPort = True
  
  def getType(self):
    """
    Provides the category this connection belongs to. This isn't always static
    since it can rely on dynamic information (like the current consensus).
    """
    
    if self.baseType == Category.OUTBOUND:
      # Currently the only non-static categories are OUTBOUND vs...
      # - EXIT since this depends on the current consensus
      # - CLIENT if this is likely to belong to our guard usage
      # - DIRECTORY if this is a single-hop circuit (directory mirror?)
      # 
      # The exitability, circuits, and fingerprints are all cached by the
      # torTools util keeping this a quick lookup.
      
      conn = torTools.getConn()
      destFingerprint = self.foreign.getFingerprint()
      
      if destFingerprint == "UNKNOWN":
        # Not a known relay. This might be an exit connection.
        
        if conn.isExitingAllowed(self.foreign.getIpAddr(), self.foreign.getPort()):
          return Category.EXIT
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
          
          for status, _, path in myCircuits:
            if path[0] == destFingerprint and (status != "BUILT" or len(path) > 1):
              return Category.CLIENT # matched a probable guard connection
          
          # fell through, we can eliminate ourselves as a guard in the future
          self._possibleClient = False
        
        if self._possibleDirectory:
          # Checks if we match a built, single hop circuit.
          
          for status, _, path in myCircuits:
            if path[0] == destFingerprint and status == "BUILT" and len(path) == 1:
              return Category.DIRECTORY
          
          # fell through, eliminate ourselves as a directory connection
          self._possibleDirectory = False
    
    return self.baseType
  
  def getDestinationLabel(self, maxLength, extraAttr=DestAttr.NONE):
    """
    Provides a short description of the destination. This is made up of two
    components, the base <ip addr>:<port> and an extra piece of information in
    parentheses. The IP address is scrubbed from private connections.
    
    Extra information is...
    - the port's purpose for exit connections
    - the extraAttr if the address isn't private and isn't on the local network
    - nothing otherwise
    
    Arguments:
      maxLength - maximum length of the string returned
    """
    
    # destination of the connection
    if self.isPrivate():
      dstAddress = "<scrubbed>:%s" % self.foreign.getPort()
    else:
      dstAddress = "%s:%s" % (self.foreign.getIpAddr(), self.foreign.getPort())
    
    # Only append the extra info if there's at least a couple characters of
    # space (this is what's needed for the country codes).
    if len(dstAddress) + 5 <= maxLength:
      spaceAvailable = maxLength - len(dstAddress) - 3
      
      if self.getType() == Category.EXIT:
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
        if extraAttr == DestAttr.LOCALE:
          dstAddress += " (%s)" % self.foreign.getLocale()
        elif extraAttr == DestAttr.HOSTNAME:
          dstHostname = self.foreign.getHostname()
          
          if dstHostname:
            dstAddress += " (%s)" % uiTools.cropStr(dstHostname, spaceAvailable)
    
    return dstAddress[:maxLength]
  
  def isPrivate(self):
    """
    Returns true if the endpoint is private, possibly belonging to a client
    connection or exit traffic.
    """
    
    myType = self.getType()
    
    if myType == Category.INBOUND:
      # if the connection doesn't belong to a known relay then it might be
      # client traffic
      
      return self.foreign.getFingerprint() == "UNKNOWN"
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
  
  def getLabel(self, listingType, width):
    """
    Provides the formatted display string for this entry in the listing with
    the given constraints. Labels are made up of six components:
      <src>  -->  <dst>     <etc>     <uptime> (<type>)
    this provides the first three components padded to fill up to the uptime.
    
    Listing.IP:
      src - <internal addr:port> --> <external addr:port>
      dst - <destination addr:port>
      etc - <fingerprint> <nickname>
    
    Listing.HOSTNAME:
      src - localhost:<port>
      dst - <destination hostname:port>
      etc - <destination addr:port> <fingerprint> <nickname>
    
    Listing.FINGERPRINT:
      src - localhost
      dst - <destination fingerprint>
      etc - <nickname> <destination addr:port>
    
    Listing.NICKNAME:
      src - <source nickname>
      dst - <destination nickname>
      etc - <fingerprint> <destination addr:port>
    
    Arguments:
      listingType - primary attribute we're listing connections by
      width       - maximum length of the entry
    """
    
    # late import for the Listing enum (doing it in the header errors due to a
    # circular import)
    from interface.connections import connPanel
    
    # if our cached entries are still valid then use that
    if self._labelCacheArgs == (listingType, width):
      return self._labelCache
    
    conn = torTools.getConn()
    myType = self.getType()
    dstAddress = self.getDestinationLabel(26, DestAttr.LOCALE)
    
    # The required widths are the sum of the following:
    # - room for LABEL_FORMAT and LABEL_MIN_PADDING (11 characters)
    # - base data for the listing
    # - that extra field plus any previous
    
    usedSpace = len(LABEL_FORMAT % tuple([""] * 4)) + LABEL_MIN_PADDING
    
    src, dst, etc = "", "", ""
    if listingType == connPanel.Listing.IP:
      myExternalIpAddr = conn.getInfo("address", self.local.getIpAddr())
      addrDiffer = myExternalIpAddr != self.local.getIpAddr()
      
      srcAddress = "%s:%s" % (myExternalIpAddr, self.local.getPort())
      src = "%-21s" % srcAddress # ip:port = max of 21 characters
      dst = "%-26s" % dstAddress # ip:port (xx) = max of 26 characters
      
      usedSpace += len(src) + len(dst) # base data requires 47 characters
      
      if width > usedSpace + 42 and CONFIG["features.connection.showColumn.fingerprint"]:
        # show fingerprint (column width: 42 characters)
        etc += "%-40s  " % self.foreign.getFingerprint()
        usedSpace += 42
      
      if addrDiffer and width > usedSpace + 28 and CONFIG["features.connection.showColumn.expanedIp"]:
        # include the internal address in the src (extra 28 characters)
        internalAddress = "%s:%s" % (self.local.getIpAddr(), self.local.getPort())
        src = "%-21s  -->  %s" % (internalAddress, src)
        usedSpace += 28
      
      if width > usedSpace + 10 and CONFIG["features.connection.showColumn.nickname"]:
        # show nickname (column width: remainder)
        nicknameSpace = width - usedSpace
        nicknameLabel = uiTools.cropStr(self.foreign.getNickname(), nicknameSpace, 0)
        etc += ("%%-%is  " % nicknameSpace) % nicknameLabel
        usedSpace += nicknameSpace + 2
    elif listingType == connPanel.Listing.HOSTNAME:
      # 15 characters for source, and a min of 40 reserved for the destination
      src = "localhost:%-5s" % self.local.getPort()
      usedSpace += len(stc)
      minHostnameSpace = 40
      
      if width > usedSpace + minHostnameSpace + 28 and CONFIG["features.connection.showColumn.destination"]:
        # show destination ip/port/locale (column width: 28 characters)
        etc += "%-26s  " % dstAddress
        usedSpace += 28
      
      if width > usedSpace + minHostnameSpace + 42 and CONFIG["features.connection.showColumn.fingerprint"]:
        # show fingerprint (column width: 42 characters)
        etc += "%-40s  " % self.foreign.getFingerprint()
        usedSpace += 42
      
      if width > usedSpace + minHostnameSpace + 17 and CONFIG["features.connection.showColumn.nickname"]:
        # show nickname (column width: min 17 characters, uses half of the remainder)
        nicknameSpace = 15 + (width - (usedSpace + minHostnameSpace + 17)) / 2
        nicknameLabel = uiTools.cropStr(self.foreign.getNickname(), nicknameSpace, 0)
        etc += ("%%-%is  " % nicknameSpace) % nicknameLabel
        usedSpace += (nicknameSpace + 2)
      
      hostnameSpace = width - usedSpace
      usedSpace = width
      if self.isPrivate():
        dst = ("%%-%is" % hostnameSpace) % "<scrubbed>"
      else:
        hostname = self.foreign.getHostname(self.foreign.getIpAddr())
        port = self.foreign.getPort()
        
        # exclude space needed for the ':<port>'
        hostnameSpace -= len(port) + 1
        
        # truncates long hostnames and sets dst to <hostname>:<port>
        hostname = uiTools.cropStr(hostname, hostnameSpace, 0)
        dst = ("%%-%is:%%-5s" % hostnameSpace) % (hostname, port)
    elif listingType == connPanel.Listing.FINGERPRINT:
      src = "localhost"
      if myType == Category.CONTROL: dst = "localhost"
      else: dst = self.foreign.getFingerprint()
      dst = "%-40s" % dst
      
      usedSpace += len(src) + len(dst) # base data requires 49 characters
      
      if width > usedSpace + 17:
        # show nickname (column width: min 17 characters, consumes any remaining space)
        nicknameSpace = width - usedSpace
        
        # if there's room then also show a column with the destination
        # ip/port/locale (column width: 28 characters)
        isIpLocaleIncluded = width > usedSpace + 45
        isIpLocaleIncluded &= CONFIG["features.connection.showColumn.destination"]
        if isIpLocaleIncluded: nicknameSpace -= 28
        
        if CONFIG["features.connection.showColumn.nickname"]:
          nicknameSpace = width - usedSpace - 28 if isIpLocaleVisible else width - usedSpace
          nicknameLabel = uiTools.cropStr(self.foreign.getNickname(), nicknameSpace, 0)
          etc += ("%%-%is  " % nicknameSpace) % nicknameLabel
          usedSpace += nicknameSpace + 2
        
        if isIpLocaleIncluded:
          etc += "%-26s  " % dstAddress
          usedSpace += 28
    else:
      # base data requires 50 min characters
      src = self.local.getNickname()
      if myType == Category.CONTROL: dst = self.local.getNickname()
      else: dst = self.foreign.getNickname()
      minBaseSpace = 50
      
      if width > usedSpace + minBaseSpace + 42 and CONFIG["features.connection.showColumn.fingerprint"]:
        # show fingerprint (column width: 42 characters)
        etc += "%-40s  " % self.foreign.getFingerprint()
        usedSpace += 42
      
      if width > usedSpace + minBaseSpace + 28 and CONFIG["features.connection.showColumn.destination"]:
        # show destination ip/port/locale (column width: 28 characters)
        etc += "%-26s  " % dstAddress
        usedSpace += 28
      
      baseSpace = width - usedSpace
      if len(src) + len(dst) > baseSpace:
        src = uiTools.cropStr(src, baseSpace / 3)
        dst = uiTools.cropStr(dst, baseSpace - len(src))
      
      # pads dst entry to its max space
      dst = ("%%-%is" % (baseSpace - len(src))) % dst
    
    if myType == Category.INBOUND: src, dst = dst, src
    padding = " " * (width - usedSpace + LABEL_MIN_PADDING)
    self._labelCache = LABEL_FORMAT % (src, dst, etc, padding)
    self._labelCacheArgs = (listingType, width)
    
    return self._labelCache

