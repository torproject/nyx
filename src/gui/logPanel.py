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
      row = (timeLabel, entry.type, entry.msg, entry.color)
      liststore.append(row)

  def register_event(self, event):
    self.msgLog.appendleft(event)
    self.fill_log()

  def pack_widgets(self):
    pass

  def _register_arm_event(self, level, msg, eventTime):
    eventColor = RUNLEVEL_EVENT_COLOR[level]
    self.register_event(LogEntry(eventTime, "ARM_%s" % level, msg, eventColor))

