"""
Connection panel entries for client circuits.
"""

import time

from cli.connections import circEntry, entries
from gui.connections import connEntry
from util import gtkTools, uiTools

class CircEntry(circEntry.CircEntry):
  @classmethod
  def convert_to_gui(self, instance):
    instance.__class__ = self

class CircHeaderLine(circEntry.CircHeaderLine, connEntry.ConnectionLine):
  @classmethod
  def convert_to_gui(self, instance):
    instance.__class__ = self

  def get_listing_row(self, listingType):
    row = connEntry.ConnectionLine.get_listing_row(self, listingType)
    theme = gtkTools.Theme()
    return row[:-1] + (theme.colors['active'],)

class CircLine(circEntry.CircLine, connEntry.ConnectionLine):
  @classmethod
  def convert_to_gui(self, instance):
    instance.__class__ = self

  def get_listing_row(self, listingType):
    dst, etc = "", ""

    if listingType == entries.ListingType.IP_ADDRESS:
      dst = self.getDestinationLabel(100, includeLocale=True)
      etc = self.foreign.getNickname()

    theme = gtkTools.Theme()

    return (dst, etc, self.placementLabel, self.getType(), theme.colors['insensitive'])

