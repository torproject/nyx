"""
Connections panel.
"""

import random
import sys
import time

from collections import deque

import gobject
import gtk

from cli.connections import (circEntry as cliCircEntry, connEntry as cliConnEntry, entries)
from cli.connections.connPanel import ConnectionPanel as CliConnectionPanel
from gui.connections import circEntry, connEntry
from util import connections, gtkTools, sysTools, uiTools, torTools
from TorCtl import TorCtl

def convert_to_gui(cliInstance):
  cliToGuiMap = [ (cliCircEntry.CircHeaderLine, circEntry.CircHeaderLine),
                  (cliCircEntry.CircLine, circEntry.CircLine),
                  (cliConnEntry.ConnectionLine, connEntry.ConnectionLine)]

  line = None

  for (cliClass, guiClass) in cliToGuiMap:
    if isinstance(cliInstance, cliClass):
      line = guiClass(cliInstance)
      break

  return line

class EntryLines(gtkTools.TreeWrapper):
  def __init__(self, container, model=None, listingType=None):
    gtkTools.TreeWrapper.__init__(self, container, model)

    self._listingType = listingType if listingType else entries.ListingType.IP_ADDRESS

  def _model_append(self, cliLine):
    if not self.model:
      return

    row = self._create_row_from_value(cliLine)
    line = convert_to_gui(cliLine)

    if isinstance(line, circEntry.CircHeaderLine):
      self.headerIter = self.model.append(None, row)
    elif isinstance(line, circEntry.CircLine):
      self.model.append(self.headerIter, row)
    else:
      self.model.append(None, row)

  def _create_row_from_value(self, cliLine):
    line = convert_to_gui(cliLine)
    row = line.get_listing_row(self._listingType)

    return row

class ConnectionPanel(CliConnectionPanel):
  def __init__(self, builder):
    CliConnectionPanel.__init__(self, None)

    self.builder = builder

    conn = torTools.getConn()
    torPid = conn.getMyPid()
    torCmdName = sysTools.getProcessName(torPid, 'tor')
    connections.getResolver(torCmdName, torPid, 'tor')

    treeStore = self.builder.get_object('treestore_conn')
    self._wrappedEntryLines = EntryLines(self._entryLines, treeStore)

  @property
  def _entryLines(self):
    if hasattr(self, '_wrappedEntryLines'):
      return self._wrappedEntryLines.container
    else:
      return []

  @_entryLines.setter
  def _entryLines(self, value):
    if hasattr(self, '_wrappedEntryLines'):
      self._wrappedEntryLines.empty()
      for entry in value:
        self._wrappedEntryLines.append(entry)
    else:
      self._wrappedEntryLines = EntryLines(value)

  def pack_widgets(self):
    self.start()

