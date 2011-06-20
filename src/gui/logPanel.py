"""
Base class for implementing graphing functionality.
"""

import random
import sys

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

    textbuffer = self.builder.get_object('textbuffer_log')

    for color in RUNLEVEL_EVENT_COLOR.values():
      textbuffer.create_tag(color, foreground=color)

    gobject.timeout_add(1000, self.print_log)

  def print_log(self):
    textbuffer = self.builder.get_object('textbuffer_log')
    start, end = textbuffer.get_bounds()
    textbuffer.delete(start, end)

    for entry in self.msgLog:
      iter = textbuffer.get_iter_at_mark(textbuffer.get_insert())
      textbuffer.insert_with_tags_by_name(iter, entry.getDisplayMessage() + "\n", entry.color)

  def register_event(self, event):
    self.msgLog.appendleft(event)

  def pack_widgets(self):
    pass

  def _register_arm_event(self, level, msg, eventTime):
    eventColor = RUNLEVEL_EVENT_COLOR[level]
    self.register_event(LogEntry(eventTime, "ARM_%s" % level, msg, eventColor))

