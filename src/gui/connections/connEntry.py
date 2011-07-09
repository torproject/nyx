"""
Connection panel entries related to actual connections to or from the system
(ie, results seen by netstat, lsof, etc).
"""

from cli.connections import connEntry

class ConnectionEntry(connEntry.ConnectionEntry):
  @classmethod
  def convertToGui(self, instance):
    instance.__class__ = self

class ConnectionLine(connEntry.ConnectionLine):
  @classmethod
  def convertToGui(self, instance):
    instance.__class__ = self

