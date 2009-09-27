#!/usr/bin/env python
# summaryPanel.py -- Static system and Tor related information.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import os
import time
import socket
from TorCtl import TorCtl

import util

# minimum width for which panel attempts to double up contents (two columns to
# better use screen real estate)
MIN_DUAL_ROW_WIDTH = 140

FLAG_COLORS = {"Authority": "white",  "BadExit": "red",     "BadDirectory": "red",    "Exit": "cyan",
               "Fast": "yellow",      "Guard": "green",     "HSDir": "magenta",       "Named": "blue",
               "Stable": "blue",      "Running": "yellow",  "Unnamed": "magenta",     "Valid": "green",
               "V2Dir": "cyan",       "V3Dir": "white"}

VERSION_STATUS_COLORS = {"new": "blue",     "new in series": "blue",    "recommended": "green",
                         "old": "red",      "obsolete": "red",          "unrecommended": "red"}

class HeaderPanel(util.Panel):
  """
  Draws top area containing static information.
  
  arm - <System Name> (<OS> <Version>)         Tor <Tor Version>
  <Relay Nickname> - <IP Addr>:<ORPort>, [Dir Port: <DirPort>, ]Control Port (<open, password, cookie>): <ControlPort>
  cpu: <cpu%> mem: <mem> (<mem%>) uid: <uid> uptime: <upmin>:<upsec>
  fingerprint: <Fingerprint>
  
  Example:
  arm - odin (Linux 2.6.24-24-generic)         Tor 0.2.1.15-rc
  odin - 76.104.132.98:9001, Dir Port: 9030, Control Port (cookie): 9051
  cpu: 14.6%    mem: 42 MB (4.2%)    pid: 20060   uptime: 48:27
  fingerprint: BDAD31F6F318E0413833E8EBDA956F76E4D66788
  """
  
  def __init__(self, lock, conn, torPid):
    util.Panel.__init__(self, lock, 6)
    self.vals = {"pid": torPid}     # mapping of information to be presented
    self.conn = conn                # Tor control port connection
    self.isPaused = False
    self.isWide = False             # doubles up parameters to shorten section if room's available
    self.lastUpdate = -1            # time last stats was retrived
    self._updateParams()
  
  def recreate(self, stdscr, startY, maxX=-1):
    # might need to recreate twice so we have a window to get width
    if not self.win: util.Panel.recreate(self, stdscr, startY, maxX)
    
    self._resetBounds()
    self.isWide = self.maxX >= MIN_DUAL_ROW_WIDTH
    self.height = 4 if self.isWide else 6
    
    util.Panel.recreate(self, stdscr, startY, maxX)
  
  def redraw(self):
    if self.win:
      if not self.lock.acquire(False): return
      try:
        if not self.isPaused: self._updateParams()
        
        # extra erase/refresh is needed to avoid internal caching screwing up and
        # refusing to redisplay content in the case of graphical glitches - probably
        # an obscure curses bug...
        self.win.erase()
        self.win.refresh()
        
        self.clear()
        
        # Line 1
        self.addstr(0, 0, "arm - %s (%s %s)" % (self.vals["sys-name"], self.vals["sys-os"], self.vals["sys-version"]))
        
        versionStatus = self.vals["status/version/current"]
        versionColor = VERSION_STATUS_COLORS[versionStatus] if versionStatus in VERSION_STATUS_COLORS else "white"
        self.addfstr(0, 43, "Tor %s (<%s>%s</%s>)" % (self.vals["version"], versionColor, versionStatus, versionColor))
        
        # Line 2 (authentication label red if open, green if credentials required)
        dirPortLabel = "Dir Port: %s, " % self.vals["DirPort"] if self.vals["DirPort"] != "0" else ""
        
        if self.vals["IsPasswordAuthSet"]: controlPortAuthLabel = "password"
        elif self.vals["IsCookieAuthSet"]: controlPortAuthLabel = "cookie"
        else: controlPortAuthLabel = "open"
        controlPortAuthColor = "red" if controlPortAuthLabel == "open" else "green"
        
        labelStart = "%s - %s:%s, %sControl Port (" % (self.vals["Nickname"], self.vals["address"], self.vals["ORPort"], dirPortLabel)
        self.addfstr(1, 0, "%s<%s>%s</%s>): %s" % (labelStart, controlPortAuthColor, controlPortAuthLabel, controlPortAuthColor, self.vals["ControlPort"]))
        
        # Line 3 (system usage info) - line 1 right if wide
        y, x = 0 if self.isWide else 2, 75 if self.isWide else 0
        self.addstr(y, x, "cpu: %s%%" % self.vals["%cpu"])
        self.addstr(y, x + 13, "mem: %s (%s%%)" % (util.getSizeLabel(int(self.vals["rss"]) * 1024), self.vals["%mem"]))
        self.addstr(y, x + 34, "pid: %s" % (self.vals["pid"] if self.vals["etime"] else ""))
        self.addstr(y, x + 47, "uptime: %s" % self.vals["etime"])
        
        # Line 4 (fingerprint) - line 2 right if wide
        y, x = 1 if self.isWide else 3, 75 if self.isWide else 0
        self.addstr(y, x, "fingerprint: %s" % self.vals["fingerprint"])
        
        # Line 5 (flags) - line 3 left if wide
        flagLine = "flags: "
        for flag in self.vals["flags"]:
          flagColor = FLAG_COLORS[flag] if flag in FLAG_COLORS.keys() else "white"
          flagLine += "<b><%s>%s</%s></b>, " % (flagColor, flag, flagColor)
        
        if len(self.vals["flags"]) > 0: flagLine = flagLine[:-2]
        self.addfstr(2 if self.isWide else 4, 0, flagLine)
        
        # Line 3 right (exit policy) - not present if not wide
        if self.isWide:
          exitPolicy = self.vals["ExitPolicy"]
          
          # adds note when default exit policy is appended
          if exitPolicy == None: exitPolicy = "<default>"
          elif not exitPolicy.endswith("accept *:*") and not exitPolicy.endswith("reject *:*"):
            exitPolicy += ", <default>"
          
          policies = exitPolicy.split(", ")
          
          # color codes accepts to be green, rejects to be red, and default marker to be cyan
          isSimple = len(policies) <= 2 # if policy is short then it's kept verbose, otherwise 'accept' and 'reject' keywords removed
          for i in range(len(policies)):
            policy = policies[i].strip()
            displayedPolicy = policy if isSimple else policy.replace("accept", "").replace("reject", "").strip()
            if policy.startswith("accept"): policy = "<green><b>%s</b></green>" % displayedPolicy
            elif policy.startswith("reject"): policy = "<red><b>%s</b></red>" % displayedPolicy
            elif policy.startswith("<default>"): policy = "<cyan><b>%s</b></cyan>" % displayedPolicy
            policies[i] = policy
          exitPolicy = ", ".join(policies)
          
          self.addfstr(2, 75, "exit policy: %s" % exitPolicy)
        
        self.refresh()
      finally:
        self.lock.release()
  
  def setPaused(self, isPause):
    """
    If true, prevents updates from being presented.
    """
    
    self.isPaused = isPause
  
  def _updateParams(self, forceReload = False):
    """
    Updates mapping of static Tor settings and system information to their
    corresponding string values. Keys include:
    info - version, *address, *fingerprint, *flags, status/version/current
    sys - sys-name, sys-os, sys-version
    ps - *%cpu, *rss, *%mem, *pid, *etime
    config - Nickname, ORPort, DirPort, ControlPort, ExitPolicy
    config booleans - IsPasswordAuthSet, IsCookieAuthSet, IsAccountingEnabled
    
    * volatile parameter that'll be reset (otherwise won't be checked if
    already set)
    """
    
    infoFields = ["address", "fingerprint"] # keys for which get_info will be called
    if len(self.vals) <= 1 or forceReload:
      isConnClosed = False
      
      # first call (only contasns 'pid' mapping) - retrieve static params
      infoFields += ["version", "status/version/current"]
      
      # populates with some basic system information
      unameVals = os.uname()
      self.vals["sys-name"] = unameVals[1]
      self.vals["sys-os"] = unameVals[0]
      self.vals["sys-version"] = unameVals[2]
      
      # parameters from the user's torrc
      configFields = ["Nickname", "ORPort", "DirPort", "ControlPort", "ExitPolicy"]
      try: self.vals.update(dict([(key, self.conn.get_option(key)[0][1]) for key in configFields]))
      except TorCtl.TorCtlClosed: isConnClosed = True
      
      # simply keeps booleans for if authentication info is set
      try:
        self.vals["IsPasswordAuthSet"] = not self.conn.get_option("HashedControlPassword")[0][1] == None
        self.vals["IsCookieAuthSet"] = self.conn.get_option("CookieAuthentication")[0][1] == "1"
        self.vals["IsAccountingEnabled"] = self.conn.get_info('accounting/enabled')['accounting/enabled'] == "1"
      except TorCtl.TorCtlClosed: isConnClosed = True
      
      if isConnClosed:
        # tor connection closed - keep old values if available, otherwise set to empty string / false
        for field in configFields:
          if field not in self.vals: self.vals[field] = ""
        
        for field in ["IsPasswordAuthSet", "IsCookieAuthSet", "IsAccountingEnabled"]:
          if field not in self.vals: self.vals[field] = False
      
    # gets parameters that throw errors if unavailable
    for param in infoFields:
      try: self.vals.update(self.conn.get_info(param))
      except TorCtl.ErrorReply: self.vals[param] = "Unknown"
      except (TorCtl.TorCtlClosed, socket.error):
        # Tor shut down or crashed - keep last known values
        if not self.vals[param]: self.vals[param] = "Unknown"
    
    # flags held by relay
    self.vals["flags"] = []
    if self.vals["fingerprint"] != "Unknown":
      try: self.vals["flags"] = self.conn.get_network_status("id/%s" % self.vals["fingerprint"])[0].flags
      except (TorCtl.TorCtlClosed, TorCtl.ErrorReply, socket.error): pass
    
    psParams = ["%cpu", "rss", "%mem", "etime"]
    if self.vals["pid"]:
      # ps call provides header followed by params for tor
      psCall = os.popen('ps -p %s -o %s  2> /dev/null' % (self.vals["pid"], ",".join(psParams)))
      
      try: sampling = psCall.read().strip().split()[len(psParams):]
      except IOError: sampling = [] # ps call failed
      psCall.close()
    else:
      sampling = [] # no pid known - blank fields
    
    if len(sampling) < 4:
      # either ps failed or returned no tor instance, blank information except runtime
      if "etime" in self.vals: sampling = [""] * (len(psParams) - 1) + [self.vals["etime"]]
      else: sampling = [""] * len(psParams)
      
      # %cpu, rss, and %mem are better zeroed out
      for i in range(3): sampling[i] = "0"
    
    for i in range(len(psParams)):
      self.vals[psParams[i]] = sampling[i]
    
    self.lastUpdate = time.time()

