"""
Connection panel entries for client circuits.
"""

from cli.connections import circEntry

class CircEntry(circEntry.CircEntry):
  @classmethod
  def convertToGui(self, instance):
    instance.__class__ = self

class CircHeaderLine(circEntry.CircHeaderLine):
  @classmethod
  def convertToGui(self, instance):
    instance.__class__ = self

class CircLine(circEntry.CircLine):
  @classmethod
  def convertToGui(self, instance):
    instance.__class__ = self

