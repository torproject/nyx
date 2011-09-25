"""
Base class for implementing graphing functionality.
"""

import random
import sys
import time

from collections import deque
from threading import RLock

import gobject
import gtk

from cli.logPanel import (expandEvents, setEventListening, getLogFileEntries,
                          LogEntry, TorEventObserver,
                          DEFAULT_CONFIG)
from util import gtkTools, log, torTools, uiTools
from TorCtl import TorCtl

RUNLEVEL_EVENT_COLOR = {log.DEBUG: 'insensitive', log.INFO: 'normal', log.NOTICE: 'normal',
                        log.WARN: 'active', log.ERR: 'active'}
STARTUP_EVENTS = 'N3'
REFRESH_RATE = 3

class LogPanel:
  def __init__(self, builder):
    self.builder = builder

    self._config = dict(DEFAULT_CONFIG)
    self._lastUpdate = 0

    self.lock = RLock()
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
      theme = gtkTools.Theme()
      armEventEntry = LogEntry(eventTime, "ARM_" + level, msg, theme.colors[RUNLEVEL_EVENT_COLOR[level]])
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

    gobject.idle_add(self.fill_log)

  def pack_widgets(self):
    listStore = self.builder.get_object('liststore_log')

    listStore.set_sort_func(1, self._compare_rows)
    listStore.set_sort_column_id(1, gtk.SORT_DESCENDING)

  def fill_log(self):
    if time.time() - self._lastUpdate < REFRESH_RATE:
      return

    listStore = self.builder.get_object('liststore_log')
    listStore.clear()

    self.lock.acquire()
    try:
      for entry in self.msgLog:
        timeLabel = time.strftime('%H:%M:%S', time.localtime(entry.timestamp))

        row = (long(entry.timestamp), timeLabel, entry.type, entry.msg, entry.color)
        listStore.append(row)
    finally:
      self.lock.release()

    self._lastUpdate = time.time()

  def register_event(self, event):
    if not event.type in self.loggedEvents:
      return

    self.lock.acquire()
    try:
      self.msgLog.appendleft(event)
    finally:
      self.lock.release()

    gobject.idle_add(self.fill_log)

  def _register_arm_event(self, level, msg, eventTime):
    theme = gtkTools.Theme()
    eventColor = theme.colors[RUNLEVEL_EVENT_COLOR[level]]
    self.register_event(LogEntry(eventTime, "ARM_%s" % level, msg, eventColor))

  def _register_torctl_event(self, level, msg):
    theme = gtkTools.Theme()
    eventColor = theme.colors[RUNLEVEL_EVENT_COLOR[level]]
    self.register_event(LogEntry(time.time(), "TORCTL_%s" % level, msg, eventColor))

  def _compare_rows(self, treeModel, iter1, iter2, data=None):
    timestampRaw1 = treeModel.get(iter1, 0)
    timestampRaw2 = treeModel.get(iter2, 0)

    return cmp(timestampRaw1, timestampRaw2)

