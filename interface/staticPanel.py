#!/usr/bin/env python
# summaryPanel.py -- Static system and Tor related information.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import os
import curses
from TorCtl import TorCtl

import util

def getStaticInfo(conn):
  """
  Provides mapping of static Tor settings and system information to their
  corresponding string values. Keys include:
  info - version, config-file, address, fingerprint
  sys - sys-name, sys-os, sys-version
  config - Nickname, ORPort, DirPort, ControlPort, ExitPolicy, BandwidthRate, BandwidthBurst
  config booleans - IsPasswordAuthSet, IsCookieAuthSet
  """
  
  vals = conn.get_info(["version", "config-file"])
  
  # gets parameters that throw errors if unavailable
  for param in ["address", "fingerprint"]:
    try:
      vals.update(conn.get_info(param))
    except TorCtl.ErrorReply:
      vals[param] = "Unknown"
  
  # populates with some basic system information
  unameVals = os.uname()
  vals["sys-name"] = unameVals[1]
  vals["sys-os"] = unameVals[0]
  vals["sys-version"] = unameVals[2]
  
  # parameters from the user's torrc
  configFields = ["Nickname", "ORPort", "DirPort", "ControlPort", "ExitPolicy", "BandwidthRate", "BandwidthBurst"]
  vals.update(dict([(key, conn.get_option(key)[0][1]) for key in configFields]))
  
  # simply keeps booleans for if authentication info is set
  vals["IsPasswordAuthSet"] = not conn.get_option("HashedControlPassword")[0][1] == None
  vals["IsCookieAuthSet"] = conn.get_option("CookieAuthentication")[0][1] == "1"
  
  return vals

def drawSummary(scr, vals):
  """
  Draws top area containing static information.
  
  arm - <System Name> (<OS> <Version>)     Tor <Tor Version>
  <Relay Nickname> - <IP Addr>:<ORPort>, [Dir Port: <DirPort>, ]Control Port (<open, password, cookie>): <ControlPort>
  Fingerprint: <Fingerprint>
  Config: <Config>
  Exit Policy: <ExitPolicy>
  
  Example:
  arm - odin (Linux 2.6.24-24-generic)     Tor 0.2.0.34 (r18423)
  odin - 76.104.132.98:9001, Dir Port: 9030, Control Port (cookie): 9051
  Fingerprint: BDAD31F6F318E0413833E8EBDA956F76E4D66788
  Config: /home/atagar/.vidalia/torrc
  Exit Policy: reject *:*
  """
  
  # extra erase/refresh is needed to avoid internal caching screwing up and
  # refusing to redisplay content in the case of graphical glitches - probably
  # an obscure curses bug...
  scr.win.erase()
  scr.win.refresh()
  
  scr.clear()
  
  # Line 1
  scr.addstr(0, 0, "arm - %s (%s %s)" % (vals["sys-name"], vals["sys-os"], vals["sys-version"]))
  scr.addstr(0, 45, "Tor %s" % vals["version"])
  
  # Line 2 (authentication label red if open, green if credentials required)
  dirPortLabel = "Dir Port: %s, " % vals["DirPort"] if not vals["DirPort"] == None else ""
  
  # TODO: if both cookie and password are set then which takes priority?
  if vals["IsPasswordAuthSet"]: controlPortAuthLabel = "password"
  elif vals["IsCookieAuthSet"]: controlPortAuthLabel = "cookie"
  else: controlPortAuthLabel = "open"
  controlPortAuthColor = "red" if controlPortAuthLabel == "open" else "green"
  
  labelStart = "%s - %s:%s, %sControl Port (" % (vals["Nickname"], vals["address"], vals["ORPort"], dirPortLabel)
  scr.addstr(1, 0, labelStart)
  xLoc = len(labelStart)
  scr.addstr(1, xLoc, controlPortAuthLabel, util.getColor(controlPortAuthColor))
  xLoc += len(controlPortAuthLabel)
  scr.addstr(1, xLoc, "): %s" % vals["ControlPort"])
  
  # Lines 3-5
  scr.addstr(2, 0, "Fingerprint: %s" % vals["fingerprint"])
  scr.addstr(3, 0, "Config: %s" % vals["config-file"])
  exitPolicy = vals["ExitPolicy"]
  
  # adds note when default exit policy is appended
  if exitPolicy == None: exitPolicy = "<default>"
  elif not exitPolicy.endswith("accept *:*") and not exitPolicy.endswith("reject *:*"):
    exitPolicy += ", <default>"
  scr.addstr(4, 0, "Exit Policy: %s" % exitPolicy)
  
  scr.refresh()

