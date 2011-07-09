"""
Connection panel entries related to actual connections to or from the system
(ie, results seen by netstat, lsof, etc).
"""

import time

from cli.connections import connEntry

class ConnectionEntry(connEntry.ConnectionEntry):
  @classmethod
  def convertToGui(self, instance):
    instance.__class__ = self

class ConnectionLine(connEntry.ConnectionLine):
  @classmethod
  def convertToGui(self, instance):
    instance.__class__ = self

  def getListingRow(self):
    local = "%s:%s" % (self.local.ipAddr, self.local.port)
    foreign = "%s:%s" % (self.foreign.ipAddr, self.foreign.port)
    timeLabel = "%d s" % (time.time() - self.startTime)

    return (local, foreign, timeLabel, self.baseType, 'black')

