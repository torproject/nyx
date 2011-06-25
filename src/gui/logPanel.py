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

from cli.logPanel import (expandEvents, setEventListening, getLogFileEntries,
                          LogEntry, TorEventObserver,
                          DEFAULT_CONFIG, RUNLEVEL_EVENT_COLOR)

STARTUP_EVENTS = 'N3'

class LogPanel:
  def __init__(self, builder):
    self.builder = builder

    self._config = dict(DEFAULT_CONFIG)
    self.msgLog = deque()
    self.loggedEvents = setEventListening(expandEvents(STARTUP_EVENTS))

    torEventBacklog = deque()
    if self._config["features.log.prepopulate"]:
      setRunlevels = list(set.intersection(set(self.loggedEvents), set(log.Runlevel.values())))
      readLimit = self._config["features.log.prepopulateReadLimit"]
      addLimit = self._config["cache.logPanel.size"]
      torEventBacklog = deque(getLogFileEntries(setRunlevels, readLimit, addLimit, self._config))

    armRunlevels = [log.DEBUG, log.INFO, log.NOTICE, log.WARN, log.ERR]
    log.addListeners(armRunlevels, self._register_arm_event)

    setRunlevels = []
    for i in range(len(armRunlevels)):
      if "ARM_" + log.Runlevel.values()[i] in self.loggedEvents:
        setRunlevels.append(armRunlevels[i])

    armEventBacklog = deque()
    for level, msg, eventTime in log._getEntries(setRunlevels):
      armEventEntry = LogEntry(eventTime, "ARM_" + level, msg, RUNLEVEL_EVENT_COLOR[level])
      armEventBacklog.appendleft(armEventEntry)

    while armEventBacklog or torEventBacklog:
      if not armEventBacklog:
        self.msgLog.append(torEventBacklog.popleft())
      elif not torEventBacklog:
        self.msgLog.append(armEventBacklog.popleft())
      elif armEventBacklog[0].timestamp < torEventBacklog[0].timestamp:
        self.msgLog.append(torEventBacklog.popleft())
      else:
        self.msgLog.append(armEventBacklog.popleft())

    conn = torTools.getConn()
    conn.addEventListener(TorEventObserver(self.register_event))
    conn.addTorCtlListener(self._register_torctl_event)

  def pack_widgets(self):
    liststore = self.builder.get_object('liststore_log')

    liststore.set_sort_func(1, self._compare_rows)
    liststore.set_sort_column_id(1, gtk.SORT_DESCENDING)

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

  def _register_arm_event(self, level, msg, eventTime):
    eventColor = RUNLEVEL_EVENT_COLOR[level]
    self.register_event(LogEntry(eventTime, "ARM_%s" % level, msg, eventColor))

  def _register_torctl_event(self, level, msg):
    eventColor = RUNLEVEL_EVENT_COLOR[level]
    self.register_event(LogEntry(time.time(), "TORCTL_%s" % level, msg, eventColor))

  def _compare_rows(self, treemodel, iter1, iter2, data=None):
    timestamp_raw1 = treemodel.get(iter1, 0)
    timestamp_raw2 = treemodel.get(iter2, 0)

    return cmp(timestamp_raw1, timestamp_raw2)

