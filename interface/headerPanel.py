#!/usr/bin/env python
# summaryPanel.py -- Static system and Tor related information.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import os
import time
import socket
from TorCtl import TorCtl

from util import panel, uiTools

# minimum width for which panel attempts to double up contents (two columns to
# better use screen real estate)
MIN_DUAL_ROW_WIDTH = 140

FLAG_COLORS = {"Authority": "white",  "BadExit": "red",     "BadDirectory": "red",    "Exit": "cyan",
               "Fast": "yellow",      "Guard": "green",     "HSDir": "magenta",       "Named": "blue",
               "Stable": "blue",      "Running": "yellow",  "Unnamed": "magenta",     "Valid": "green",
               "V2Dir": "cyan",       "V3Dir": "white"}

VERSION_STATUS_COLORS = {"new": "blue",      "new in series": "blue",  "recommended": "green",  "old": "red",
                         "obsolete": "red",  "unrecommended": "red",   "unknown": "cyan"}

class HeaderPanel(panel.Panel):
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
  
  def __init__(self, stdscr, conn, torPid):
    panel.Panel.__init__(self, stdscr, 0, 6)
    self.vals = {"pid": torPid}     # mapping of information to be presented
    self.conn = conn                # Tor control port connection
    self.isPaused = False
    self.isWide = False             # doubles up parameters to shorten section if room's available
    self.rightParamX = 0            # offset used for doubled up parameters
    self.lastUpdate = -1            # time last stats was retrived
    self._updateParams()
    self.getPreferredSize() # hack to force properly initialize size (when using wide version)
  
  def getPreferredSize(self):
    # width partially determines height (panel has two layouts)
    panelHeight, panelWidth = panel.Panel.getPreferredSize(self)
    self.isWide = panelWidth >= MIN_DUAL_ROW_WIDTH
    self.rightParamX = max(panelWidth / 2, 75) if self.isWide else 0
    self.setHeight(4 if self.isWide else 6)
    return panel.Panel.getPreferredSize(self)
  
  def draw(self, subwindow, width, height):
    if not self.isPaused: self._updateParams()
    
    # TODO: remove after a few revisions if this issue can't be reproduced
    #   (seemed to be a freak ui problem...)
    
    # extra erase/refresh is needed to avoid internal caching screwing up and
    # refusing to redisplay content in the case of graphical glitches - probably
    # an obscure curses bug...
    #self.win.erase()
    #self.win.refresh()
    
    #self.clear()
    
    # Line 1 (system and tor version information)
    systemNameLabel = "arm - %s " % self.vals["sys-name"]
    systemVersionLabel = "%s %s" % (self.vals["sys-os"], self.vals["sys-version"])
    
    # wraps systemVersionLabel in parentheses and truncates if too long
    versionLabelMaxWidth = 40 - len(systemNameLabel)
    if len(systemNameLabel) > 40:
      # we only have room for the system name label
      systemNameLabel = systemNameLabel[:39] + "..."
      systemVersionLabel = ""
    elif len(systemVersionLabel) > versionLabelMaxWidth:
      # not enough room to show full version
      systemVersionLabel = "(%s...)" % systemVersionLabel[:versionLabelMaxWidth - 3].strip()
    else:
      # enough room for everything
      systemVersionLabel = "(%s)" % systemVersionLabel
    
    self.addstr(0, 0, "%s%s" % (systemNameLabel, systemVersionLabel))
    
    versionStatus = self.vals["status/version/current"]
    versionColor = VERSION_STATUS_COLORS[versionStatus] if versionStatus in VERSION_STATUS_COLORS else "white"
    
    # truncates torVersionLabel if too long
    torVersionLabel = self.vals["version"]
    versionLabelMaxWidth =  (self.rightParamX if self.isWide else width) - 51 - len(versionStatus)
    if len(torVersionLabel) > versionLabelMaxWidth:
      torVersionLabel = torVersionLabel[:versionLabelMaxWidth - 1].strip() + "-"
    
    self.addfstr(0, 43, "Tor %s (<%s>%s</%s>)" % (torVersionLabel, versionColor, versionStatus, versionColor))
    
    # Line 2 (authentication label red if open, green if credentials required)
    dirPortLabel = "Dir Port: %s, " % self.vals["DirPort"] if self.vals["DirPort"] != "0" else ""
    
    if self.vals["IsPasswordAuthSet"]: controlPortAuthLabel = "password"
    elif self.vals["IsCookieAuthSet"]: controlPortAuthLabel = "cookie"
    else: controlPortAuthLabel = "open"
    controlPortAuthColor = "red" if controlPortAuthLabel == "open" else "green"
    
    labelStart = "%s - %s:%s, %sControl Port (" % (self.vals["Nickname"], self.vals["address"], self.vals["ORPort"], dirPortLabel)
    self.addfstr(1, 0, "%s<%s>%s</%s>): %s" % (labelStart, controlPortAuthColor, controlPortAuthLabel, controlPortAuthColor, self.vals["ControlPort"]))
    
    # Line 3 (system usage info) - line 1 right if wide
    y, x = (0, self.rightParamX) if self.isWide else (2, 0)
    self.addstr(y, x, "cpu: %s%%" % self.vals["%cpu"])
    self.addstr(y, x + 13, "mem: %s (%s%%)" % (uiTools.getSizeLabel(int(self.vals["rss"]) * 1024), self.vals["%mem"]))
    self.addstr(y, x + 34, "pid: %s" % (self.vals["pid"] if self.vals["etime"] else ""))
    self.addstr(y, x + 47, "uptime: %s" % self.vals["etime"])
    
    # Line 4 (fingerprint) - line 2 right if wide
    y, x = (1, self.rightParamX) if self.isWide else (3, 0)
    self.addstr(y, x, "fingerprint: %s" % self.vals["fingerprint"])
    
    # Line 5 (flags) - line 3 left if wide
    flagLine = "flags: "
    for flag in self.vals["flags"]:
      flagColor = FLAG_COLORS[flag] if flag in FLAG_COLORS.keys() else "white"
      flagLine += "<b><%s>%s</%s></b>, " % (flagColor, flag, flagColor)
    
    if len(self.vals["flags"]) > 0: flagLine = flagLine[:-2]
    self.addfstr(2 if self.isWide else 4, 0, flagLine)
    
    # Line 3 right (exit policy) - only present if wide
    if self.isWide:
      exitPolicy = self.vals["ExitPolicy"]
      
      # adds note when default exit policy is appended
      # TODO: the following catch-all policies arne't quite exhaustive
      if exitPolicy == None: exitPolicy = "<default>"
      elif not (exitPolicy.endswith("accept *:*") or exitPolicy.endswith("accept *")) and not (exitPolicy.endswith("reject *:*") or exitPolicy.endswith("reject *")):
        exitPolicy += ", <default>"
      
      policies = exitPolicy.split(", ")
      
      # color codes accepts to be green, rejects to be red, and default marker to be cyan
      # TODO: instead base this on if there's space available for the full verbose version
      isSimple = len(policies) <= 2 # if policy is short then it's kept verbose, otherwise 'accept' and 'reject' keywords removed
      for i in range(len(policies)):
        policy = policies[i].strip()
        displayedPolicy = policy if isSimple else policy.replace("accept", "").replace("reject", "").strip()
        if policy.startswith("accept"): policy = "<green><b>%s</b></green>" % displayedPolicy
        elif policy.startswith("reject"): policy = "<red><b>%s</b></red>" % displayedPolicy
        elif policy.startswith("<default>"): policy = "<cyan><b>%s</b></cyan>" % displayedPolicy
        policies[i] = policy
      exitPolicy = ", ".join(policies)
      
      self.addfstr(2, self.rightParamX, "exit policy: %s" % exitPolicy)
  
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
      lookupFailed = False
      
      # first call (only contasns 'pid' mapping) - retrieve static params
      infoFields += ["version", "status/version/current"]
      
      # populates with some basic system information
      unameVals = os.uname()
      self.vals["sys-name"] = unameVals[1]
      self.vals["sys-os"] = unameVals[0]
      self.vals["sys-version"] = unameVals[2]
      
      try:
        # parameters from the user's torrc
        configFields = ["Nickname", "ORPort", "DirPort", "ControlPort"]
        self.vals.update(dict([(key, self.conn.get_option(key)[0][1]) for key in configFields]))
        
        # fetch exit policy (might span over multiple lines)
        exitPolicyEntries = []
        for (key, value) in self.conn.get_option("ExitPolicy"):
          if value: exitPolicyEntries.append(value)
        
        self.vals["ExitPolicy"] = ", ".join(exitPolicyEntries)
        
        # simply keeps booleans for if authentication info is set
        self.vals["IsPasswordAuthSet"] = not self.conn.get_option("HashedControlPassword")[0][1] == None
        self.vals["IsCookieAuthSet"] = self.conn.get_option("CookieAuthentication")[0][1] == "1"
        self.vals["IsAccountingEnabled"] = self.conn.get_info('accounting/enabled')['accounting/enabled'] == "1"
      except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed): lookupFailed = True
      
      if lookupFailed:
        # tor connection closed or gave error - keep old values if available, otherwise set to empty string / false
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
        if not param in self.vals.keys() or not self.vals[param]: self.vals[param] = "Unknown"
    
    # if ORListenAddress is set overwrites 'address' (and possibly ORPort)
    try:
      listenAddr = self.conn.get_option("ORListenAddress")[0][1]
      if listenAddr:
        if ":" in listenAddr:
          # both ip and port overwritten
          self.vals["address"] = listenAddr[:listenAddr.find(":")]
          self.vals["ORPort"] = listenAddr[listenAddr.find(":") + 1:]
        else:
          self.vals["address"] = listenAddr
    except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed): pass
    
    # flags held by relay
    self.vals["flags"] = []
    if self.vals["fingerprint"] != "Unknown":
      try:
        nsCall = self.conn.get_network_status("id/%s" % self.vals["fingerprint"])
        if nsCall: self.vals["flags"] = nsCall[0].flags
        else: raise TorCtl.ErrorReply # network consensus couldn't be fetched
      except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed): pass
    
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

