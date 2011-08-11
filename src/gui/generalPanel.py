"""
General panel.
"""

import random
import sys
import time

from collections import deque

import gobject
import gtk

from cli.headerPanel import (HeaderPanel as CliHeaderPanel, VERSION_STATUS_COLORS)
from util import connections, gtkTools, sysTools, torTools, uiTools
from TorCtl import TorCtl

class GeneralPanel(CliHeaderPanel):
  def __init__(self, builder):
    CliHeaderPanel.__init__(self, None, time.time())

    self.builder = builder
    self.filled = False
    self._isTorConnected = torTools.getConn().isAlive()

    gobject.idle_add(self._fill_entries)
    gobject.timeout_add(3000, self._timeout_fill_entries)

  def pack_widgets(self):
    return

  def _timeout_fill_entries(self):
    self._fill_entries()

    return True

  def _fill_entries(self):
    self.valsLock.acquire()

    listStore = self.builder.get_object('liststore_general')
    theme = gtkTools.Theme()

    listStore.clear()

    key = "arm"
    value = "%s (%s %s)" % (self.vals['sys/hostname'], self.vals['sys/os'], self.vals['sys/version'])
    row = (key, value, theme.colors['active'])
    listStore.append(row)

    versionColor = VERSION_STATUS_COLORS[self.vals["tor/versionStatus"]] if \
        self.vals["tor/versionStatus"] in VERSION_STATUS_COLORS else "black"
    key = "Tor"
    value = "%s (<span foreground=\"%s\">%s</span>)" % (self.vals['tor/version'], versionColor, self.vals['tor/versionStatus'])
    row = (key, value, theme.colors['active'])
    listStore.append(row)

    includeControlPort = True
    key = "Relaying"
    if self.vals["tor/orPort"]:
      myAddress = "Unknown"
      if self.vals["tor/orListenAddr"]: myAddress = self.vals["tor/orListenAddr"]
      elif self.vals["tor/address"]: myAddress = self.vals["tor/address"]

      dirPortLabel = ", Dir Port: %s" % self.vals["tor/dirPort"] if self.vals["tor/dirPort"] != "0" else ""

      value = "%s%s%s%s" % (self.vals["tor/nickname"], " - " + myAddress, ":" + self.vals["tor/orPort"], dirPortLabel)
    else:
      if self._isTorConnected:
        value = "Disabled"
      else:
        statusTime = torTools.getConn().getStatus()[1]

        if statusTime:
          statusTimeLabel = time.strftime("%H:%M %m/%d/%Y, ", time.localtime(statusTime))
        else: statusTimeLabel = ""

        value = "%s%s" % ("Tor Disconnected", statusTimeLabel)
        includeControlPort = False
    row = (key, value, theme.colors['active'])
    listStore.append(row)

    key = "Control Port"
    if includeControlPort:
      if self.vals["tor/isAuthPassword"]: authType = "password"
      elif self.vals["tor/isAuthCookie"]: authType = "cookie"
      else: authType = "open"

      authColor = "red" if authType == "open" else "green"
      value = "%s (<span foreground=\"%s\">%s</span>)" % (self.vals['tor/controlPort'], authColor, authType)
    row = (key, value, theme.colors['active'])
    listStore.append(row)

    if self.vals["stat/rss"] != "0": memoryLabel = uiTools.getSizeLabel(int(self.vals["stat/rss"]))
    else: memoryLabel = "0"

    uptimeLabel = "N/A"
    if self.vals["tor/startTime"]:
      if self.isPaused() or not self._isTorConnected:
        uptimeLabel = uiTools.getShortTimeLabel(self.getPauseTime() - self.vals["tor/startTime"])
      else:
        uptimeLabel = uiTools.getShortTimeLabel(time.time() - self.vals["tor/startTime"])

    key = "CPU"
    value = "%s%% Tor, %s%% arm" % (self.vals["stat/%torCpu"], self.vals["stat/%armCpu"])
    row = (key, value, theme.colors['active'])
    listStore.append(row)

    key = "Memory"
    value = "%s (%s%%)" % (memoryLabel, self.vals["stat/%mem"])
    row = (key, value, theme.colors['active'])
    listStore.append(row)

    key = "PID"
    value = "%s" % (self.vals["tor/pid"] if self._isTorConnected else "")
    row = (key, value, theme.colors['active'])
    listStore.append(row)

    key = "Uptime"
    value = uptimeLabel
    row = (key, value, theme.colors['active'])
    listStore.append(row)

    self.valsLock.release()

