"""
Entries for connections related to the Tor process.
"""

import time

from util import connections, enum, hostnames, torTools, uiTools

# Connection Categories:
#   Inbound     Relay connection, coming to us.
#   Outbound    Relay connection, leaving us.
#   DNS         Relayed dns queries.
#   Socks       Application client connection.
#   Client      Circuits for our client traffic.
#   Directory   Fetching tor consensus information.
#   Control     Tor controller (arm, vidalia, etc).

# TODO: add recognizing of CLIENT connection type
Category = enum.Enum("INBOUND", "OUTBOUND", "DNS", "SOCKS", "CLIENT", "DIRECTORY", "CONTROL")
CATEGORY_COLOR = {Category.INBOUND: "green", Category.OUTBOUND: "blue",
                  Category.DNS: "blue",      Category.SOCKS: "cyan",
                  Category.CLIENT: "cyan",   Category.DIRECTORY: "magenta",
                  Category.CONTROL: "red"}

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
      self.type = Category.INBOUND
      self.local.isORPort = True
    elif lPort == mySocksPort:
      self.type = Category.SOCKS
    elif lPort == myCtlPort:
      self.type = Category.CONTROL
    elif (fIpAddr, fPort) in myAuthorities:
      self.type = Category.DIRECTORY
    elif fPort == "53":
      # TODO: also check if this was a UDP connection (gonna take a bit more work...)
      self.type = Category.DNS
    else:
      self.type = Category.OUTBOUND
      self.foreign.isORPort = True
  
  def isPrivate(self):
    """
    Returns true if the endpoint is private, possibly belonging to a client
    connection or exit traffic.
    """
    
    if self.type == Category.INBOUND:
      # if the connection doesn't belong to a known relay then it might be
      # client traffic
      
      return self.foreign.getFingerprint() == "UNKNOWN"
    elif self.type == Category.OUTBOUND:
      # if it's both not a relay and obeys our exit policy then it may belong
      # to exit traffic
      
      conn = torTools.getConn()
      isExitingAllowed = conn.isExitingAllowed(self.foreign.getIpAddr(), self.foreign.getPort())
      return self.foreign.getFingerprint() == "UNKNOWN" and isExitingAllowed
    else:
      # for control, application, and directory connections this isn't a concern
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
    
    # destination of the connection
    dstAddress = "<scrubbed>"
    if not self.isPrivate():
      dstAddress = "%s:%s" % (self.foreign.getIpAddr(), self.foreign.getPort())
      
      # if this isn't on the local network then also include the country
      if not connections.isIpAddressPrivate(self.foreign.getIpAddr()):
        dstAddress += " (%s)" % self.foreign.getLocale()
    
    src, dst, etc = "", "", ""
    if listingType == connPanel.Listing.IP:
      # base data requires 73 characters
      myExternalIpAddr = conn.getInfo("address", self.local.getIpAddr())
      addrDiffer = myExternalIpAddr != self.local.getIpAddr()
      
      srcAddress = "%s:%s" % (myExternalIpAddr, self.local.getPort())
      src = "%-21s" % srcAddress # ip:port = max of 21 characters
      dst = "%-26s" % dstAddress # ip:port (xx) = max of 26 characters
      
      if width > 115:
        # show fingerprint (column width: 42 characters)
        etc += "%-40s  " % self.foreign.getFingerprint()
      
      if addrDiffer and width > 143:
        # include the internal address in the src (extra 28 characters)
        internalAddress = "%s:%s" % (self.local.getIpAddr(), self.local.getPort())
        src = "%-21s  -->  %s" % (internalAddress, src)
      
      if (not addrDiffer and width > 143) or width > 155:
        # show nickname (column width: remainder)
        nicknameSpace = width - 146
        nicknameLabel = uiTools.cropStr(self.foreign.getNickname(), nicknameSpace, 0)
        etc += ("%%-%is  " % nicknameSpace) % nicknameLabel
    elif listingType == connPanel.Listing.HOSTNAME:
      # base data requires 80 characters
      src = "localhost:%-5s" % self.local.getPort()
      
      # space available for foreign hostname (stretched to claim any free space)
      hostnameSpace = width - 42
      
      if width > 108:
        # show destination ip/port/locale (column width: 28 characters)
        hostnameSpace -= 28
        etc += "%-26s  " % dstAddress
      
      if width > 134:
        # show fingerprint (column width: 42 characters)
        hostnameSpace -= 42
        etc += "%-40s  " % self.foreign.getFingerprint()
      
      if width > 151:
        # show nickname (column width: min 17 characters, uses half of the remainder)
        nicknameSpace = 15 + (width - 151) / 2
        hostnameSpace -= (nicknameSpace + 2)
        nicknameLabel = uiTools.cropStr(self.foreign.getNickname(), nicknameSpace, 0)
        etc += ("%%-%is  " % nicknameSpace) % nicknameLabel
      
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
      # base data requires 75 characters
      src = "localhost"
      if self.type == Category.CONTROL: dst = "localhost"
      else: dst = self.foreign.getFingerprint()
      dst = "%-40s" % dst
      
      if width > 92:
        # show nickname (column width: min 17 characters, uses remainder if extra room's available)
        nicknameSpace = width - 78 if width < 126 else width - 106
        nicknameLabel = uiTools.cropStr(self.foreign.getNickname(), nicknameSpace, 0)
        etc += ("%%-%is  " % nicknameSpace) % nicknameLabel
      
      if width > 125:
        # show destination ip/port/locale (column width: 28 characters)
        etc += "%-26s  " % dstAddress
    else:
      # base data uses whatever extra room's available (using minimun of 50 characters)
      src = self.local.getNickname()
      if self.type == Category.CONTROL: dst = self.local.getNickname()
      else: dst = self.foreign.getNickname()
      
      # space available for foreign nickname
      nicknameSpace = width - len(src) - 27
      
      if width > 92:
        # show fingerprint (column width: 42 characters)
        nicknameSpace -= 42
        etc += "%-40s  " % self.foreign.getFingerprint()
      
      if width > 120:
        # show destination ip/port/locale (column width: 28 characters)
        nicknameSpace -= 28
        etc += "%-26s  " % dstAddress
      
      dst = ("%%-%is" % nicknameSpace) % dst
    
    if self.type == Category.INBOUND: src, dst = dst, src
    padding = width - len(src) - len(dst) - len(etc) - 27
    self._labelCache = "%s  -->  %s  %s%s" % (src, dst, etc, " " * padding)
    self._labelCacheArgs = (listingType, width)
    
    return self._labelCache

