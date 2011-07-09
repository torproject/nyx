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
from TorCtl import TorCtl
from util import connections, sysTools, uiTools, torTools

REFRESH_RATE = 3

def convertToGui(instance):
  cliToGuiMap = [ (cliCircEntry.CircEntry, circEntry.CircEntry),
                  (cliCircEntry.CircHeaderLine, circEntry.CircHeaderLine),
                  (cliCircEntry.CircLine, circEntry.CircLine),
                  (cliConnEntry.ConnectionEntry, connEntry.ConnectionEntry),
                  (cliConnEntry.ConnectionLine, connEntry.ConnectionLine)]

  for (cliClass, guiClass) in cliToGuiMap:
    if isinstance(instance, cliClass):
      guiClass.convertToGui(instance)
      break

def calculateCacheKey(entryLine):
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

    gobject.timeout_add(3000, self._fill_entries)

  def pack_widgets(self):
    pass

  def _fill_entries(self):
    self.valsLock.acquire()

    label = self.builder.get_object('label_conn_top')
    label.set_text(self._title)

    treestore = self.builder.get_object('treestore_conn')

    # first pass checks whether we have enough entries cached to not update the treeview
    index = 0
    for line in self._entryLines:
      convertToGui(line)
      cacheKey = calculateCacheKey(line)

      if self.cache.has_key(cacheKey):
        timeLabel = "%d s" % (time.time() - line.startTime)
        treestore.set_value(self.cache[cacheKey], 2, timeLabel)
      else:
        break

      index = index + 1

    if index == len(self._entryLines):
      self.valsLock.release()
      return True

    # one of the entries was not found in cache, clear and repopulate the treestore
    treestore.clear()
    headeriter = None

    for line in self._entryLines:
      convertToGui(line)

      if isinstance(line, connEntry.ConnectionLine) and line.isUnresolvedApp():
        self._resolveApps()

      row = line.getListingRow()

      if isinstance(line, circEntry.CircHeaderLine):
        currentiter = treestore.append(None, row)
        headeriter = currentiter
      elif isinstance(line, circEntry.CircLine):
        currentiter = treestore.append(headeriter, row)
      else:
        currentiter = treestore.append(None, row)

      cacheKey = calculateCacheKey(line)
      self.cache[cacheKey] = currentiter

    self.valsLock.release()

    return True

