"""
Connections panel.
"""

import random
import sys
import time

from collections import deque

import gobject
import gtk

from cli.connections import circEntry, connEntry
from cli.connections.connPanel import ConnectionPanel as CliConnectionPanel
from TorCtl import TorCtl
from util import connections, sysTools, uiTools, torTools

REFRESH_RATE = 3

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
    for (index, line) in enumerate(self._entryLines):
      local = "%s:%s" % (line.local.ipAddr, line.local.port)
      foreign = "%s:%s" % (line.foreign.ipAddr, line.foreign.port)
      cachekey = (local, foreign)
      if self.cache.has_key(cachekey):
        timeLabel = "%d s" % (time.time() - line.startTime)
        treestore.set_value(self.cache[cachekey], 2, timeLabel)
      else:
        break

    if index == len(self._entryLines) - 1:
      return

    treestore.clear()

    headeriter = None

    for line in self._entryLines:
      if isinstance(line, connEntry.ConnectionLine) and line.isUnresolvedApp():
        self._resolveApps()

      local = "%s:%s" % (line.local.ipAddr, line.local.port)
      foreign = "%s:%s" % (line.foreign.ipAddr, line.foreign.port)
      timeLabel = "%d s" % (time.time() - line.startTime)
      row = (local, foreign, timeLabel, line.baseType, 'black')

      if isinstance(line, circEntry.CircHeaderLine):
        currentiter = treestore.append(None, row)
        headeriter = currentiter
      elif isinstance(line, circEntry.CircLine):
        currentiter = treestore.append(headeriter, row)
      else:
        currentiter = treestore.append(None, row)

      cachekey = (local, foreign)
      self.cache[cachekey] = currentiter

    self.valsLock.release()

    return True

