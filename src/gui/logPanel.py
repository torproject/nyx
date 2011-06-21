"""
Base class for implementing graphing functionality.
"""

import random
import sys
import time

from collections import deque

import gobject
import gtk

from TorCtl import TorCtl
from util import log, uiTools, torTools

from cli.logPanel import RUNLEVEL_EVENT_COLOR, LogEntry

class LogPanel:
  def __init__(self, builder):
    self.builder = builder

    self.msgLog = deque()

    log.LOG_LOCK.acquire()
    try:
      armRunlevels = [log.DEBUG, log.INFO, log.NOTICE, log.WARN, log.ERR]
      log.addListeners(armRunlevels, self._register_arm_event)
    finally:
      log.LOG_LOCK.release()

  def fill_log(self):
    liststore = self.builder.get_object('liststore_log')
    liststore.clear()

    for entry in self.msgLog:
      timeLabel = time.strftime('%H:%M:%S', time.localtime(entry.timestamp))
      row = (long(entry.timestamp), timeLabel, entry.type, entry.msg, entry.color)
      liststore.append(row)

  def register_event(self, event):
    self.msgLog.appendleft(event)
    self.fill_log()

  def pack_widgets(self):
    liststore = self.builder.get_object('liststore_log')

    liststore.set_sort_func(1, self._compare_rows)
    liststore.set_sort_column_id(1, gtk.SORT_DESCENDING)

  def _compare_rows(self, treemodel, iter1, iter2, data=None):
    timestamp_raw1 = treemodel.get(iter1, 0)
    timestamp_raw2 = treemodel.get(iter2, 0)

    return cmp(timestamp_raw1, timestamp_raw2)

  def _register_arm_event(self, level, msg, eventTime):
    eventColor = RUNLEVEL_EVENT_COLOR[level]
    self.register_event(LogEntry(eventTime, "ARM_%s" % level, msg, eventColor))

