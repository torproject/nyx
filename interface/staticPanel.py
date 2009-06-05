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
  config - Nickname, ORPort, DirPort, ControlPort, ExitPolicy
  config booleans - IsPasswordAuthSet, IsCookieAuthSet, IsAccountingEnabled
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
  configFields = ["Nickname", "ORPort", "DirPort", "ControlPort", "ExitPolicy"]
  vals.update(dict([(key, conn.get_option(key)[0][1]) for key in configFields]))
  
  # simply keeps booleans for if authentication info is set
  vals["IsPasswordAuthSet"] = not conn.get_option("HashedControlPassword")[0][1] == None
  vals["IsCookieAuthSet"] = conn.get_option("CookieAuthentication")[0][1] == "1"
  
  vals["IsAccountingEnabled"] = conn.get_info('accounting/enabled')['accounting/enabled'] == "1"
  
  return vals

class SummaryPanel(util.Panel):
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
  
  def __init__(self, lock, vals):
    util.Panel.__init__(self, lock, 6)
    self.vals = vals          # mapping of information to be presented
  
  def redraw(self):
    i = 1
    
    if self.win:
      # extra erase/refresh is needed to avoid internal caching screwing up and
      # refusing to redisplay content in the case of graphical glitches - probably
      # an obscure curses bug...
      self.win.erase()
      self.win.refresh()
      
      self.clear()
      
      # Line 1
      self.addstr(0, 0, "arm - %s (%s %s)" % (self.vals["sys-name"], self.vals["sys-os"], self.vals["sys-version"]))
      self.addstr(0, 45, "Tor %s" % self.vals["version"])
      
      # Line 2 (authentication label red if open, green if credentials required)
      dirPortLabel = "Dir Port: %s, " % self.vals["DirPort"] if not self.vals["DirPort"] == None else ""
      
      # TODO: if both cookie and password are set then which takes priority?
      if self.vals["IsPasswordAuthSet"]: controlPortAuthLabel = "password"
      elif self.vals["IsCookieAuthSet"]: controlPortAuthLabel = "cookie"
      else: controlPortAuthLabel = "open"
      controlPortAuthColor = "red" if controlPortAuthLabel == "open" else "green"
      
      labelStart = "%s - %s:%s, %sControl Port (" % (self.vals["Nickname"], self.vals["address"], self.vals["ORPort"], dirPortLabel)
      self.addstr(1, 0, labelStart)
      xLoc = len(labelStart)
      self.addstr(1, xLoc, controlPortAuthLabel, util.getColor(controlPortAuthColor))
      xLoc += len(controlPortAuthLabel)
      self.addstr(1, xLoc, "): %s" % self.vals["ControlPort"])
      
      # Lines 3-5
      self.addstr(2, 0, "Fingerprint: %s" % self.vals["fingerprint"])
      self.addstr(3, 0, "Config: %s" % self.vals["config-file"])
      exitPolicy = self.vals["ExitPolicy"]
      
      # adds note when default exit policy is appended
      if exitPolicy == None: exitPolicy = "<default>"
      elif not exitPolicy.endswith("accept *:*") and not exitPolicy.endswith("reject *:*"):
        exitPolicy += ", <default>"
      self.addstr(4, 0, "Exit Policy: %s" % exitPolicy)
      
      self.refresh()

