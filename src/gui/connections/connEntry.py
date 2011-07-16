"""
Connection panel entries related to actual connections to or from the system
(ie, results seen by netstat, lsof, etc).
"""

import time

from cli.connections import connEntry, entries
from cli.connections.connEntry import Category, CONFIG
from util import gtkTools, torTools, uiTools

class ConnectionEntry(connEntry.ConnectionEntry):
  @classmethod
  def convert_to_gui(self, instance):
    instance.__class__ = self

class ConnectionLine(connEntry.ConnectionLine):
  @classmethod
  def convert_to_gui(self, instance):
    instance.__class__ = self

  def get_listing_row(self, listingType):
    conn = torTools.getConn()
    myType = self.getType()
    dstAddress = self.getDestinationLabel(26, includeLocale = True)
    localPort = ":%s" % self.local.getPort() if self.includePort else ""

    src, dst, etc = "", "", ""

    if listingType == entries.ListingType.IP_ADDRESS:
      myExternalIpAddr = conn.getInfo("address", self.local.getIpAddr())
      addrDiffer = myExternalIpAddr != self.local.getIpAddr()

      isExpansionType = not myType in (Category.SOCKS, Category.HIDDEN, Category.CONTROL)

      if isExpansionType: srcAddress = myExternalIpAddr + localPort
      else: srcAddress = self.local.getIpAddr() + localPort

      if myType in (Category.SOCKS, Category.CONTROL):
        src = dstAddress
        dst = srcAddress
      else:
        src = srcAddress
        dst = dstAddress

      if addrDiffer and isExpansionType and self.includeExpandedIpAddr and CONFIG["features.connection.showColumn.expandedIp"]:
        internalAddress = self.local.getIpAddr() + localPort

        if myType == Category.INBOUND: (src, dst) =  (src, internalAddress)
        else: (src, dst) =  (internalAddress, src)

      etc = self.getEtcContent(100, listingType)
    else:
      src = "%s:%s" % (self.local.ipAddr, self.local.port)
      dst = "%s:%s" % (self.foreign.ipAddr, self.foreign.port)

    timeLabel = uiTools.getTimeLabel(time.time() - self.startTime)
    theme = gtkTools.Theme()

    return (src, dst, timeLabel, self.getType(), theme.colors['insensitive'])

