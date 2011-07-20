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

class GuiConverter:
  cliToGuiMap = [ (cliCircEntry.CircHeaderLine, circEntry.CircHeaderLine),
                  (cliCircEntry.CircLine, circEntry.CircLine),
                  (cliConnEntry.ConnectionLine, connEntry.ConnectionLine)]

  def __init__(self):
    self._cache = {}

  def __call__(self, cliInstance):
    cacheKey = self._calculate_cache_key(cliInstance)

    if self._cache.has_key(cacheKey):
      return self._cache[cacheKey]

    line = None

    for (cliClass, guiClass) in GuiConverter.cliToGuiMap:
      if isinstance(cliInstance, cliClass):
        line = guiClass(cliInstance)
        self._cache[cacheKey] = line
        break

    return line

  def _calculate_cache_key(self, entryLine):
    local = (entryLine.local.ipAddr, entryLine.local.port)
    foreign = (entryLine.foreign.ipAddr, entryLine.foreign.port)

    return (entryLine.__class__, local, foreign)

convert_to_gui = GuiConverter()

class ConnectionPanel(CliConnectionPanel):
  def __init__(self, builder):
    CliConnectionPanel.__init__(self, None)

    self.builder = builder

    conn = torTools.getConn()
    torPid = conn.getMyPid()
    torCmdName = sysTools.getProcessName(torPid, 'tor')
    connections.getResolver(torCmdName, torPid, 'tor')

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
    for cliLine in self._entryLines:
      line = convert_to_gui(cliLine)

      treeIter = self._treestore_get_iter(line)

      if treeIter:
        if not isinstance(line, circEntry.CircLine):
          timeLabel = "%d s" % (time.time() - line.cliLine.startTime)
          treeStore.set_value(treeIter, 2, timeLabel)
      else:
        break

      index = index + 1

    if index == len(self._entryLines):
      self.valsLock.release()
      return True

    # one of the entries was not found in cache, clear and repopulate the treeStore
    treeStore.clear()
    headerIter = None

    for cliLine in self._entryLines:
      line = convert_to_gui(cliLine)

      if isinstance(line, connEntry.ConnectionLine) and line.cliLine.isUnresolvedApp():
        self._resolveApps()

      row = line.get_listing_row(self._listingType)

      if isinstance(line, circEntry.CircHeaderLine):
        currentIter = treeStore.append(None, row)
        headerIter = currentIter
      elif isinstance(line, circEntry.CircLine):
        currentIter = treeStore.append(headerIter, row)
      else:
        currentIter = treeStore.append(None, row)

    self.valsLock.release()

  def _treestore_get_iter(self, line):
    def match_func(model, treeIter, data):
      column, key = data
      value = model.get_value(treeIter, column)
      return value == key

    def search(model, treeIter, func, data):
      while treeIter:
        if func(model, treeIter, data):
          return treeIter
        result = search(model, model.iter_children(treeIter), func, data)
        if result: return result
        treeIter = model.iter_next(treeIter)
      return None

    treeStore = self.builder.get_object('treestore_conn')
    matchIter = search(treeStore, treeStore.iter_children(None), match_func, (5, line))

    return matchIter

