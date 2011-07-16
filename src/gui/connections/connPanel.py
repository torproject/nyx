"""
Connections panel.
"""

import random
import sys
import time

from collections import deque

import gobject
import gtk

from cli.connections import (circEntry as cliCircEntry, connEntry as cliConnEntry)
from cli.connections.connPanel import ConnectionPanel as CliConnectionPanel
from gui.connections import circEntry, connEntry
from util import connections, sysTools, uiTools, torTools
from TorCtl import TorCtl

REFRESH_RATE = 3

def convert_to_gui(instance):
  cliToGuiMap = [ (cliCircEntry.CircEntry, circEntry.CircEntry),
                  (cliCircEntry.CircHeaderLine, circEntry.CircHeaderLine),
                  (cliCircEntry.CircLine, circEntry.CircLine),
                  (cliConnEntry.ConnectionEntry, connEntry.ConnectionEntry),
                  (cliConnEntry.ConnectionLine, connEntry.ConnectionLine)]

  for (cliClass, guiClass) in cliToGuiMap:
    if isinstance(instance, cliClass):
      guiClass.convert_to_gui(instance)
      break

def calculate_cache_key(entryLine):
  local = (entryLine.local.ipAddr, entryLine.local.port)
  foreign = (entryLine.foreign.ipAddr, entryLine.foreign.port)

  return (entryLine.__class__, local, foreign)

class ConnectionPanel(CliConnectionPanel):
  def __init__(self, builder):
    CliConnectionPanel.__init__(self, None)

    self.builder = builder
    self.cache = {}

    conn = torTools.getConn()
    torPid = conn.getMyPid()
    torCmdName = sysTools.getProcessName(torPid, "tor")
    connections.getResolver(torCmdName, torPid, "tor")

    gobject.idle_add(self._fill_entries)
    gobject.timeout_add(3000, self._timeout_fill_entries)

  def pack_widgets(self):
    self.start()

  def _timeout_fill_entries(self):
    self._fill_entries()

    return True

  def _fill_entries(self):
    self.valsLock.acquire()

    label = self.builder.get_object('label_conn_top')
    label.set_text(self._title)

    treeStore = self.builder.get_object('treestore_conn')

    # first pass checks whether we have enough entries cached to not update the treeview
    index = 0
    for line in self._entryLines:
      convert_to_gui(line)
      cacheKey = calculate_cache_key(line)

      if self.cache.has_key(cacheKey):
        if not isinstance(line, circEntry.CircLine):
          timeLabel = "%d s" % (time.time() - line.startTime)
          treeStore.set_value(self.cache[cacheKey], 2, timeLabel)
      else:
        break

      index = index + 1

    if index == len(self._entryLines):
      self.valsLock.release()
      return True

    # one of the entries was not found in cache, clear and repopulate the treeStore
    treeStore.clear()
    headerIter = None

    for line in self._entryLines:
      convert_to_gui(line)

      if isinstance(line, connEntry.ConnectionLine) and line.isUnresolvedApp():
        self._resolveApps()

      row = line.get_listing_row(self._listingType)

      if isinstance(line, circEntry.CircHeaderLine):
        currentIter = treeStore.append(None, row)
        headerIter = currentIter
      elif isinstance(line, circEntry.CircLine):
        currentIter = treeStore.append(headerIter, row)
      else:
        currentIter = treeStore.append(None, row)

      cacheKey = calculate_cache_key(line)
      self.cache[cacheKey] = currentIter

    self.valsLock.release()

