"""
Connection panel entries for client circuits.
"""

import time

from cli.connections import entries
from gui.connections import connEntry
from util import gtkTools, uiTools

class CircHeaderLine(connEntry.ConnectionLine):
  def __init__(self, cliLine):
    connEntry.ConnectionLine.__init__(self, cliLine)

  def get_listing_row(self, listingType):
    row = connEntry.ConnectionLine.get_listing_row(self, listingType)
    theme = gtkTools.Theme()
    return row[:-2] + (theme.colors['active'], self)

class CircLine(connEntry.ConnectionLine):
  def __init__(self, cliLine):
    connEntry.ConnectionLine.__init__(self, cliLine)

    self.parentIter = None

  def get_listing_row(self, listingType):
    dst, etc = "", ""

    if listingType == entries.ListingType.IP_ADDRESS:
      dst = self.cliLine.getDestinationLabel(100, includeLocale=True)
      etc = self.cliLine.foreign.getNickname()

    theme = gtkTools.Theme()

    return (dst, etc, self.cliLine.placementLabel, self.cliLine.getType(), theme.colors['insensitive'], self)

