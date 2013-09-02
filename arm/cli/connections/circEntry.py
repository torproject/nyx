"""
Connection panel entries for client circuits. This includes a header entry
followed by an entry for each hop in the circuit. For instance:

89.188.20.246:42667    -->  217.172.182.26 (de)       General / Built     8.6m (CIRCUIT)
|  85.8.28.4 (se)               98FBC3B2B93897A78CDD797EF549E6B62C9A8523    1 / Guard
|  91.121.204.76 (fr)           546387D93F8D40CFF8842BB9D3A8EC477CEDA984    2 / Middle
+- 217.172.182.26 (de)          5CFA9EA136C0EA0AC096E5CEA7EB674F1207CF86    3 / Exit
"""

import curses

from cli.connections import entries, connEntry
from util import torTools, uiTools

class CircEntry(connEntry.ConnectionEntry):
  def __init__(self, circuitID, status, purpose, path):
    connEntry.ConnectionEntry.__init__(self, "127.0.0.1", "0", "127.0.0.1", "0")
    
    self.circuitID = circuitID
    self.status = status
    
    # drops to lowercase except the first letter
    if len(purpose) >= 2:
      purpose = purpose[0].upper() + purpose[1:].lower()
    
    self.lines = [CircHeaderLine(self.circuitID, purpose)]
    
    # Overwrites attributes of the initial line to make it more fitting as the
    # header for our listing.
    
    self.lines[0].baseType = connEntry.Category.CIRCUIT
    
    self.update(status, path)
  
  def update(self, status, path):
    """
    Our status and path can change over time if the circuit is still in the
    process of being built. Updates these attributes of our relay.
    
    Arguments:
      status - new status of the circuit
      path   - list of fingerprints for the series of relays involved in the
               circuit
    """
    
    self.status = status
    self.lines = [self.lines[0]]
    conn = torTools.getConn()
    
    if status == "BUILT" and not self.lines[0].isBuilt:
      exitIp, exitORPort = conn.getRelayAddress(path[-1], ("192.168.0.1", "0"))
      self.lines[0].setExit(exitIp, exitORPort, path[-1])
    
    for i in range(len(path)):
      relayFingerprint = path[i]
      relayIp, relayOrPort = conn.getRelayAddress(relayFingerprint, ("192.168.0.1", "0"))
      
      if i == len(path) - 1:
        if status == "BUILT": placementType = "Exit"
        else: placementType = "Extending"
      elif i == 0: placementType = "Guard"
      else: placementType = "Middle"
      
      placementLabel = "%i / %s" % (i + 1, placementType)
      
      self.lines.append(CircLine(relayIp, relayOrPort, relayFingerprint, placementLabel))
    
    self.lines[-1].isLast = True

class CircHeaderLine(connEntry.ConnectionLine):
  """
  Initial line of a client entry. This has the same basic format as connection
  lines except that its etc field has circuit attributes.
  """
  
  def __init__(self, circuitID, purpose):
    connEntry.ConnectionLine.__init__(self, "127.0.0.1", "0", "0.0.0.0", "0", False, False)
    self.circuitID = circuitID
    self.purpose = purpose
    self.isBuilt = False
  
  def setExit(self, exitIpAddr, exitPort, exitFingerprint):
    connEntry.ConnectionLine.__init__(self, "127.0.0.1", "0", exitIpAddr, exitPort, False, False)
    self.isBuilt = True
    self.foreign.fingerprintOverwrite = exitFingerprint
  
  def getType(self):
    return connEntry.Category.CIRCUIT
  
  def getDestinationLabel(self, maxLength, includeLocale=False, includeHostname=False):
    if not self.isBuilt: return "Building..."
    return connEntry.ConnectionLine.getDestinationLabel(self, maxLength, includeLocale, includeHostname)
  
  def getEtcContent(self, width, listingType):
    """
    Attempts to provide all circuit related stats. Anything that can't be
    shown completely (not enough room) is dropped.
    """
    
    etcAttr = ["Purpose: %s" % self.purpose, "Circuit ID: %i" % self.circuitID]
    
    for i in range(len(etcAttr), -1, -1):
      etcLabel = ", ".join(etcAttr[:i])
      if len(etcLabel) <= width:
        return ("%%-%is" % width) % etcLabel
    
    return ""
  
  def getDetails(self, width):
    if not self.isBuilt:
      detailFormat = curses.A_BOLD | uiTools.getColor(connEntry.CATEGORY_COLOR[self.getType()])
      return [("Building Circuit...", detailFormat)]
    else: return connEntry.ConnectionLine.getDetails(self, width)

class CircLine(connEntry.ConnectionLine):
  """
  An individual hop in a circuit. This overwrites the displayed listing, but
  otherwise makes use of the ConnectionLine attributes (for the detail display,
  caching, etc).
  """
  
  def __init__(self, fIpAddr, fPort, fFingerprint, placementLabel):
    connEntry.ConnectionLine.__init__(self, "127.0.0.1", "0", fIpAddr, fPort)
    self.foreign.fingerprintOverwrite = fFingerprint
    self.placementLabel = placementLabel
    self.includePort = False
    
    # determines the sort of left hand bracketing we use
    self.isLast = False
  
  def getType(self):
    return connEntry.Category.CIRCUIT
  
  def getListingPrefix(self):
    if self.isLast: return (ord(' '), curses.ACS_LLCORNER, curses.ACS_HLINE, ord(' '))
    else: return (ord(' '), curses.ACS_VLINE, ord(' '), ord(' '))
  
  def getListingEntry(self, width, currentTime, listingType):
    """
    Provides the [(msg, attr)...] listing for this relay in the circuilt
    listing. Lines are composed of the following components:
      <bracket> <dst> <etc> <placement label>
    
    The dst and etc entries largely match their ConnectionEntry counterparts.
    
    Arguments:
      width       - maximum length of the line
      currentTime - the current unix time (ignored)
      listingType - primary attribute we're listing connections by
    """
    
    return entries.ConnectionPanelLine.getListingEntry(self, width, currentTime, listingType)
  
  def _getListingEntry(self, width, currentTime, listingType):
    lineFormat = uiTools.getColor(connEntry.CATEGORY_COLOR[self.getType()])
    
    # The required widths are the sum of the following:
    # initial space (1 character)
    # bracketing (3 characters)
    # placementLabel (14 characters)
    # gap between etc and placement label (5 characters)
    
    baselineSpace = 14 + 5
    
    dst, etc = "", ""
    if listingType == entries.ListingType.IP_ADDRESS:
      # TODO: include hostname when that's available
      # dst width is derived as:
      # src (21) + dst (26) + divider (7) + right gap (2) - bracket (3) = 53 char
      dst = "%-53s" % self.getDestinationLabel(53, includeLocale = True)
      
      # fills the nickname into the empty space here
      dst = "%s%-25s   " % (dst[:25], uiTools.cropStr(self.foreign.getNickname(), 25, 0))
      
      etc = self.getEtcContent(width - baselineSpace - len(dst), listingType)
    elif listingType == entries.ListingType.HOSTNAME:
      # min space for the hostname is 40 characters
      etc = self.getEtcContent(width - baselineSpace - 40, listingType)
      dstLayout = "%%-%is" % (width - baselineSpace - len(etc))
      dst = dstLayout % self.foreign.getHostname(self.foreign.getIpAddr())
    elif listingType == entries.ListingType.FINGERPRINT:
      # dst width is derived as:
      # src (9) + dst (40) + divider (7) + right gap (2) - bracket (3) = 55 char
      dst = "%-55s" % self.foreign.getFingerprint()
      etc = self.getEtcContent(width - baselineSpace - len(dst), listingType)
    else:
      # min space for the nickname is 56 characters
      etc = self.getEtcContent(width - baselineSpace - 56, listingType)
      dstLayout = "%%-%is" % (width - baselineSpace - len(etc))
      dst = dstLayout % self.foreign.getNickname()
    
    return ((dst + etc, lineFormat),
            (" " * (width - baselineSpace - len(dst) - len(etc) + 5), lineFormat),
            ("%-14s" % self.placementLabel, lineFormat))

