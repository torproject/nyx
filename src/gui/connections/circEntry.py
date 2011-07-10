"""
Connection panel entries for client circuits.
"""

import time

from cli.connections import circEntry
from util import gtkTools

class CircEntry(circEntry.CircEntry):
  @classmethod
  def convertToGui(self, instance):
    instance.__class__ = self

class CircHeaderLine(circEntry.CircHeaderLine):
  @classmethod
  def convertToGui(self, instance):
    instance.__class__ = self

  def getListingRow(self, listingType):
    local = "%s:%s" % (self.local.ipAddr, self.local.port)
    foreign = "%s:%s" % (self.foreign.ipAddr, self.foreign.port)
    timeLabel = "%d s" % (time.time() - self.startTime)
    theme = gtkTools.Theme()

    return (local, foreign, timeLabel, self.baseType, theme.colors['active'])

class CircLine(circEntry.CircLine):
  @classmethod
  def convertToGui(self, instance):
    instance.__class__ = self

  def getListingRow(self, listingType):
    local = "%s:%s" % (self.local.ipAddr, self.local.port)
    foreign = "%s:%s" % (self.foreign.ipAddr, self.foreign.port)
    timeLabel = "%d s" % (time.time() - self.startTime)
    theme = gtkTools.Theme()

    return (local, foreign, timeLabel, self.baseType, theme.colors['normal'])

