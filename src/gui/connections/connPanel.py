"""
Connections panel.
"""

import random
import sys
import time

from collections import deque

import gobject
import gtk

from cli.connections import connEntry
from cli.connections.connPanel import ConnectionPanel as CliConnectionPanel
from TorCtl import TorCtl
from util import connections, uiTools, torTools

REFRESH_RATE = 3

class ConnectionPanel(CliConnectionPanel):
  def __init__(self, builder):
    CliConnectionPanel.__init__(self, None)

    self.builder = builder

    self.resolver = connections.getResolver('tor')

    gobject.timeout_add(3000, self._fill_entries)

  def pack_widgets(self):
    pass

  def _fill_entries(self):
    self.valsLock.acquire()

    label = self.builder.get_object('label_conn_top')
    label.set_text(self._title)

    liststore = self.builder.get_object('liststore_conn')
    liststore.clear()

    for line in self._entryLines:
      if isinstance(line, connEntry.ConnectionLine) and line.isUnresolvedApp():
        self._resolveApps()

      local = "%s:%s" % (line.local.ipAddr, line.local.port)
      foreign = "%s:%s" % (line.foreign.ipAddr, line.foreign.port)
      timeLabel = "%d s" % (time.time() - line.startTime)
      row = (local, foreign, timeLabel, line.baseType, 'black')
      liststore.append(row)

    self.valsLock.release()

    return True

