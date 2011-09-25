"""
Connection panel entries related to actual connections to or from the system
(ie, results seen by netstat, lsof, etc).
"""

import time

import gobject

from cli.connections import entries
from cli.connections.connEntry import Category, CONFIG
from util import gtkTools, torTools, uiTools

class ConnectionLine(gobject.GObject):
  def __init__(self, cliLine):
    gobject.GObject.__init__(self)

    self.cliLine = cliLine

  def get_listing_row(self, listingType):
    conn = torTools.getConn()
    myType = self.cliLine.getType()
    dstAddress = self.cliLine.getDestinationLabel(26, includeLocale = True)
    localPort = ":%s" % self.cliLine.local.getPort() if self.cliLine.includePort else ""

    src, dst, etc = "", "", ""

    if listingType == entries.ListingType.IP_ADDRESS:
      myExternalIpAddr = conn.getInfo("address", self.cliLine.local.getIpAddr())
      addrDiffer = myExternalIpAddr != self.cliLine.local.getIpAddr()

      isExpansionType = not myType in (Category.SOCKS, Category.HIDDEN, Category.CONTROL)

      if isExpansionType: srcAddress = myExternalIpAddr + localPort
      else: srcAddress = self.cliLine.local.getIpAddr() + localPort

      if myType in (Category.SOCKS, Category.CONTROL):
        src = dstAddress
        dst = srcAddress
      else:
        src = srcAddress
        dst = dstAddress

      if addrDiffer and isExpansionType and self.cliLine.includeExpandedIpAddr and CONFIG["features.connection.showColumn.expandedIp"]:
        internalAddress = self.cliLine.local.getIpAddr() + localPort

        if myType == Category.INBOUND: (src, dst) =  (src, internalAddress)
        else: (src, dst) =  (internalAddress, src)

      etc = self.cliLine.getEtcContent(100, listingType)
    else:
      src = "%s:%s" % (self.cliLine.local.ipAddr, self.cliLine.local.port)
      dst = "%s:%s" % (self.cliLine.foreign.ipAddr, self.cliLine.foreign.port)

    timeLabel = uiTools.getTimeLabel(time.time() - self.cliLine.startTime)
    theme = gtkTools.Theme()

    return (src, dst, timeLabel, self.cliLine.getType(), theme.colors['insensitive'], self)

