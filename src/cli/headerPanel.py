"""
Top panel for every page, containing basic system and tor related information.
If there's room available then this expands to present its information in two
columns, otherwise it's laid out as follows:
  arm - <hostname> (<os> <sys/version>)         Tor <tor/version> (<new, old, recommended, etc>)
  <nickname> - <address>:<orPort>, [Dir Port: <dirPort>, ]Control Port (<open, password, cookie>): <controlPort>
  cpu: <cpu%> mem: <mem> (<mem%>) uid: <uid> uptime: <upmin>:<upsec>
  fingerprint: <fingerprint>

Example:
  arm - odin (Linux 2.6.24-24-generic)         Tor 0.2.1.19 (recommended)
  odin - 76.104.132.98:9001, Dir Port: 9030, Control Port (cookie): 9051
  cpu: 14.6%    mem: 42 MB (4.2%)    pid: 20060   uptime: 48:27
  fingerprint: BDAD31F6F318E0413833E8EBDA956F76E4D66788
"""

import os
import time
import curses
import threading

import TorCtl.TorCtl

import starter
import cli.popups
import cli.controller

from util import log, panel, sysTools, torTools, uiTools

# minimum width for which panel attempts to double up contents (two columns to
# better use screen real estate)
MIN_DUAL_COL_WIDTH = 141

FLAG_COLORS = {"Authority": "white",  "BadExit": "red",     "BadDirectory": "red",    "Exit": "cyan",
               "Fast": "yellow",      "Guard": "green",     "HSDir": "magenta",       "Named": "blue",
               "Stable": "blue",      "Running": "yellow",  "Unnamed": "magenta",     "Valid": "green",
               "V2Dir": "cyan",       "V3Dir": "white"}

VERSION_STATUS_COLORS = {"new": "blue", "new in series": "blue", "obsolete": "red", "recommended": "green",  
                         "old": "red",  "unrecommended": "red",  "unknown": "cyan"}

DEFAULT_CONFIG = {"startup.interface.ipAddress": "127.0.0.1",
                  "startup.interface.port": 9051,
                  "startup.interface.socket": "/var/run/tor/control",
                  "features.showFdUsage": False,
                  "log.fdUsageSixtyPercent": log.NOTICE,
                  "log.fdUsageNinetyPercent": log.WARN}

class HeaderPanel(panel.Panel, threading.Thread):
  """
  Top area contenting tor settings and system information. Stats are stored in
  the vals mapping, keys including:
    tor/  version, versionStatus, nickname, orPort, dirPort, controlPort,
          socketPath, exitPolicy, isAuthPassword (bool), isAuthCookie (bool),
          orListenAddr, *address, *fingerprint, *flags, pid, startTime,
          *fdUsed, fdLimit, isFdLimitEstimate
    sys/  hostname, os, version
    stat/ *%torCpu, *%armCpu, *rss, *%mem
  
  * volatile parameter that'll be reset on each update
  """
  
  def __init__(self, stdscr, startTime, config = None):
    panel.Panel.__init__(self, stdscr, "header", 0)
    threading.Thread.__init__(self)
    self.setDaemon(True)
    
    self._config = dict(DEFAULT_CONFIG)
    if config: config.update(self._config)
    
    self._isTorConnected = torTools.getConn().isAlive()
    self._lastUpdate = -1       # time the content was last revised
    self._halt = False          # terminates thread if true
    self._cond = threading.Condition()  # used for pausing the thread
    
    # Time when the panel was paused or tor was stopped. This is used to
    # freeze the uptime statistic (uptime increments normally when None).
    self._haltTime = None
    
    # The last arm cpu usage sampling taken. This is a tuple of the form:
    # (total arm cpu time, sampling timestamp)
    # 
    # The initial cpu total should be zero. However, at startup the cpu time
    # in practice is often greater than the real time causing the initially
    # reported cpu usage to be over 100% (which shouldn't be possible on
    # single core systems).
    # 
    # Setting the initial cpu total to the value at this panel's init tends to
    # give smoother results (staying in the same ballpark as the second
    # sampling) so fudging the numbers this way for now.
    
    self._armCpuSampling = (sum(os.times()[:3]), startTime)
    
    # Last sampling received from the ResourceTracker, used to detect when it
    # changes.
    self._lastResourceFetch = -1
    
    # flag to indicate if we've already given file descriptor warnings
    self._isFdSixtyPercentWarned = False
    self._isFdNinetyPercentWarned = False
    
    self.vals = {}
    self.valsLock = threading.RLock()
    self._update(True)
    
    # listens for tor reload (sighup) events
    torTools.getConn().addStatusListener(self.resetListener)
  
  def getHeight(self):
    """
    Provides the height of the content, which is dynamically determined by the
    panel's maximum width.
    """
    
    isWide = self.getParent().getmaxyx()[1] >= MIN_DUAL_COL_WIDTH
    if self.vals["tor/orPort"]: return 4 if isWide else 6
    else: return 3 if isWide else 4
  
  def sendNewnym(self):
    """
    Requests a new identity and provides a visual queue.
    """
    
    torTools.getConn().sendNewnym()
    
    # If we're wide then the newnym label in this panel will give an
    # indication that the signal was sent. Otherwise use a msg.
    isWide = self.getParent().getmaxyx()[1] >= MIN_DUAL_COL_WIDTH
    if not isWide: cli.popups.showMsg("Requesting a new identity", 1)
  
  def handleKey(self, key):
    isKeystrokeConsumed = True
    
    if key in (ord('n'), ord('N')) and torTools.getConn().isNewnymAvailable():
      self.sendNewnym()
    elif key in (ord('r'), ord('R')) and not self._isTorConnected:
      torctlConn = None
      allowPortConnection, allowSocketConnection, _ = starter.allowConnectionTypes()
      
      if os.path.exists(self._config["startup.interface.socket"]) and allowSocketConnection:
        try: torctlConn = torTools.connect_socket(self._config["startup.interface.socket"])
        except IOError, exc:
          if not allowPortConnection:
            cli.popups.showMsg("Unable to reconnect (%s)" % exc, 3)
      elif not allowPortConnection:
        cli.popups.showMsg("Unable to reconnect (socket '%s' doesn't exist)" % self._config["startup.interface.socket"], 3)
      
      if not torctlConn and allowPortConnection:
        # TODO: This has diverged from starter.py's connection, for instance it
        # doesn't account for relative cookie paths or multiple authentication
        # methods. We can't use the starter.py's connection function directly
        # due to password prompts, but we could certainly make this mess more
        # manageable.
        
        try:
          ctlAddr, ctlPort = self._config["startup.interface.ipAddress"], self._config["startup.interface.port"]
          tmpConn, authType, authValue = TorCtl.TorCtl.preauth_connect(ctlAddr, ctlPort)
          
          if authType == TorCtl.TorCtl.AUTH_TYPE.PASSWORD:
            authValue = cli.popups.inputPrompt("Controller Password: ")
            if not authValue: raise IOError() # cancel reconnection
          elif authType == TorCtl.TorCtl.AUTH_TYPE.COOKIE:
            authCookieSize = os.path.getsize(authValue)
            if authCookieSize != 32:
              raise IOError("authentication cookie '%s' is the wrong size (%i bytes instead of 32)" % (authValue, authCookieSize))
          
          tmpConn.authenticate(authValue)
          torctlConn = tmpConn
        except Exception, exc:
          # attempts to use the wizard port too
          try:
            cli.controller.getController().getTorManager().connectManagedInstance()
            log.log(log.NOTICE, "Reconnected to Tor's control port")
            cli.popups.showMsg("Tor reconnected", 1)
          except:
            # displays notice for the first failed connection attempt
            if exc.args: cli.popups.showMsg("Unable to reconnect (%s)" % exc, 3)
      
      if torctlConn:
        torTools.getConn().init(torctlConn)
        log.log(log.NOTICE, "Reconnected to Tor's control port")
        cli.popups.showMsg("Tor reconnected", 1)
    else: isKeystrokeConsumed = False
    
    return isKeystrokeConsumed
  
  def draw(self, width, height):
    self.valsLock.acquire()
    isWide = width + 1 >= MIN_DUAL_COL_WIDTH
    
    # space available for content
    if isWide:
      leftWidth = max(width / 2, 77)
      rightWidth = width - leftWidth
    else: leftWidth = rightWidth = width
    
    # Line 1 / Line 1 Left (system and tor version information)
    sysNameLabel = "arm - %s" % self.vals["sys/hostname"]
    contentSpace = min(leftWidth, 40)
    
    if len(sysNameLabel) + 10 <= contentSpace:
      sysTypeLabel = "%s %s" % (self.vals["sys/os"], self.vals["sys/version"])
      sysTypeLabel = uiTools.cropStr(sysTypeLabel, contentSpace - len(sysNameLabel) - 3, 4)
      self.addstr(0, 0, "%s (%s)" % (sysNameLabel, sysTypeLabel))
    else:
      self.addstr(0, 0, uiTools.cropStr(sysNameLabel, contentSpace))
    
    contentSpace = leftWidth - 43
    if 7 + len(self.vals["tor/version"]) + len(self.vals["tor/versionStatus"]) <= contentSpace:
      if self.vals["tor/version"] != "Unknown":
        versionColor = VERSION_STATUS_COLORS[self.vals["tor/versionStatus"]] if \
            self.vals["tor/versionStatus"] in VERSION_STATUS_COLORS else "white"
        labelPrefix = "Tor %s (" % self.vals["tor/version"]
        self.addstr(0, 43, labelPrefix)
        self.addstr(0, 43 + len(labelPrefix), self.vals["tor/versionStatus"], uiTools.getColor(versionColor))
        self.addstr(0, 43 + len(labelPrefix) + len(self.vals["tor/versionStatus"]), ")")
    elif 11 <= contentSpace:
      self.addstr(0, 43, uiTools.cropStr("Tor %s" % self.vals["tor/version"], contentSpace, 4))
    
    # Line 2 / Line 2 Left (tor ip/port information)
    x, includeControlPort = 0, True
    if self.vals["tor/orPort"]:
      myAddress = "Unknown"
      if self.vals["tor/orListenAddr"]: myAddress = self.vals["tor/orListenAddr"]
      elif self.vals["tor/address"]: myAddress = self.vals["tor/address"]
      
      # acting as a relay (we can assume certain parameters are set
      dirPortLabel = ", Dir Port: %s" % self.vals["tor/dirPort"] if self.vals["tor/dirPort"] != "0" else ""
      for label in (self.vals["tor/nickname"], " - " + myAddress, ":" + self.vals["tor/orPort"], dirPortLabel):
        if x + len(label) <= leftWidth:
          self.addstr(1, x, label)
          x += len(label)
        else: break
    else:
      # non-relay (client only)
      if self._isTorConnected:
        self.addstr(1, x, "Relaying Disabled", uiTools.getColor("cyan"))
        x += 17
      else:
        statusTime = torTools.getConn().getStatus()[1]
        
        if statusTime:
          statusTimeLabel = time.strftime("%H:%M %m/%d/%Y, ", time.localtime(statusTime))
        else: statusTimeLabel = "" # never connected to tor
        
        self.addstr(1, x, "Tor Disconnected", curses.A_BOLD | uiTools.getColor("red"))
        self.addstr(1, x + 16, " (%spress r to reconnect)" % statusTimeLabel)
        x += 39 + len(statusTimeLabel)
        includeControlPort = False
    
    if includeControlPort:
      if self.vals["tor/controlPort"] == "0":
        # connected via a control socket
        self.addstr(1, x, ", Control Socket: %s" % self.vals["tor/socketPath"])
      else:
        if self.vals["tor/isAuthPassword"]: authType = "password"
        elif self.vals["tor/isAuthCookie"]: authType = "cookie"
        else: authType = "open"
        
        if x + 19 + len(self.vals["tor/controlPort"]) + len(authType) <= leftWidth:
          authColor = "red" if authType == "open" else "green"
          self.addstr(1, x, ", Control Port (")
          self.addstr(1, x + 16, authType, uiTools.getColor(authColor))
          self.addstr(1, x + 16 + len(authType), "): %s" % self.vals["tor/controlPort"])
        elif x + 16 + len(self.vals["tor/controlPort"]) <= leftWidth:
          self.addstr(1, 0, ", Control Port: %s" % self.vals["tor/controlPort"])
    
    # Line 3 / Line 1 Right (system usage info)
    y, x = (0, leftWidth) if isWide else (2, 0)
    if self.vals["stat/rss"] != "0": memoryLabel = uiTools.getSizeLabel(int(self.vals["stat/rss"]))
    else: memoryLabel = "0"
    
    uptimeLabel = ""
    if self.vals["tor/startTime"]:
      if self.isPaused() or not self._isTorConnected:
        # freeze the uptime when paused or the tor process is stopped
        uptimeLabel = uiTools.getShortTimeLabel(self.getPauseTime() - self.vals["tor/startTime"])
      else:
        uptimeLabel = uiTools.getShortTimeLabel(time.time() - self.vals["tor/startTime"])
    
    sysFields = ((0, "cpu: %s%% tor, %s%% arm" % (self.vals["stat/%torCpu"], self.vals["stat/%armCpu"])),
                 (27, "mem: %s (%s%%)" % (memoryLabel, self.vals["stat/%mem"])),
                 (47, "pid: %s" % (self.vals["tor/pid"] if self._isTorConnected else "")),
                 (59, "uptime: %s" % uptimeLabel))
    
    for (start, label) in sysFields:
      if start + len(label) <= rightWidth: self.addstr(y, x + start, label)
      else: break
    
    if self.vals["tor/orPort"]:
      # Line 4 / Line 2 Right (fingerprint, and possibly file descriptor usage)
      y, x = (1, leftWidth) if isWide else (3, 0)
      
      fingerprintLabel = uiTools.cropStr("fingerprint: %s" % self.vals["tor/fingerprint"], width)
      self.addstr(y, x, fingerprintLabel)
      
      # if there's room and we're able to retrieve both the file descriptor
      # usage and limit then it might be presented
      if width - x - 59 >= 20 and self.vals["tor/fdUsed"] and self.vals["tor/fdLimit"]:
        # display file descriptor usage if we're either configured to do so or
        # running out
        
        fdPercent = 100 * self.vals["tor/fdUsed"] / self.vals["tor/fdLimit"]
        
        if fdPercent >= 60 or self._config["features.showFdUsage"]:
          fdPercentLabel, fdPercentFormat = "%i%%" % fdPercent, curses.A_NORMAL
          if fdPercent >= 95:
            fdPercentFormat = curses.A_BOLD | uiTools.getColor("red")
          elif fdPercent >= 90:
            fdPercentFormat = uiTools.getColor("red")
          elif fdPercent >= 60:
            fdPercentFormat = uiTools.getColor("yellow")
          
          estimateChar = "?" if self.vals["tor/isFdLimitEstimate"] else ""
          baseLabel = "file desc: %i / %i%s (" % (self.vals["tor/fdUsed"], self.vals["tor/fdLimit"], estimateChar)
          
          self.addstr(y, x + 59, baseLabel)
          self.addstr(y, x + 59 + len(baseLabel), fdPercentLabel, fdPercentFormat)
          self.addstr(y, x + 59 + len(baseLabel) + len(fdPercentLabel), ")")
      
      # Line 5 / Line 3 Left (flags)
      if self._isTorConnected:
        y, x = (2 if isWide else 4, 0)
        self.addstr(y, x, "flags: ")
        x += 7
        
        if len(self.vals["tor/flags"]) > 0:
          for i in range(len(self.vals["tor/flags"])):
            flag = self.vals["tor/flags"][i]
            flagColor = FLAG_COLORS[flag] if flag in FLAG_COLORS.keys() else "white"
            
            self.addstr(y, x, flag, curses.A_BOLD | uiTools.getColor(flagColor))
            x += len(flag)
            
            if i < len(self.vals["tor/flags"]) - 1:
              self.addstr(y, x, ", ")
              x += 2
        else:
          self.addstr(y, x, "none", curses.A_BOLD | uiTools.getColor("cyan"))
      else:
        y = 2 if isWide else 4
        statusTime = torTools.getConn().getStatus()[1]
        statusTimeLabel = time.strftime("%H:%M %m/%d/%Y", time.localtime(statusTime))
        self.addstr(y, 0, "Tor Disconnected", curses.A_BOLD | uiTools.getColor("red"))
        self.addstr(y, 16, " (%s) - press r to reconnect" % statusTimeLabel)
      
      # Undisplayed / Line 3 Right (exit policy)
      if isWide:
        exitPolicy = self.vals["tor/exitPolicy"]
        
        # adds note when default exit policy is appended
        if exitPolicy == "": exitPolicy = "<default>"
        elif not exitPolicy.endswith((" *:*", " *")): exitPolicy += ", <default>"
        
        self.addstr(2, leftWidth, "exit policy: ")
        x = leftWidth + 13
        
        # color codes accepts to be green, rejects to be red, and default marker to be cyan
        isSimple = len(exitPolicy) > rightWidth - 13
        policies = exitPolicy.split(", ")
        for i in range(len(policies)):
          policy = policies[i].strip()
          policyLabel = policy.replace("accept", "").replace("reject", "").strip() if isSimple else policy
          
          policyColor = "white"
          if policy.startswith("accept"): policyColor = "green"
          elif policy.startswith("reject"): policyColor = "red"
          elif policy.startswith("<default>"): policyColor = "cyan"
          
          self.addstr(2, x, policyLabel, curses.A_BOLD | uiTools.getColor(policyColor))
          x += len(policyLabel)
          
          if i < len(policies) - 1:
            self.addstr(2, x, ", ")
            x += 2
    else:
      # (Client only) Undisplayed / Line 2 Right (new identity option)
      if isWide:
        conn = torTools.getConn()
        newnymWait = conn.getNewnymWait()
        
        msg = "press 'n' for a new identity"
        if newnymWait > 0:
          pluralLabel = "s" if newnymWait > 1 else ""
          msg = "building circuits, available again in %i second%s" % (newnymWait, pluralLabel)
        
        self.addstr(1, leftWidth, msg)
    
    self.valsLock.release()
  
  def getPauseTime(self):
    """
    Provides the time Tor stopped if it isn't running. Otherwise this is the
    time we were last paused.
    """
    
    if self._haltTime: return self._haltTime
    else: return panel.Panel.getPauseTime(self)
  
  def run(self):
    """
    Keeps stats updated, checking for new information at a set rate.
    """
    
    lastDraw = time.time() - 1
    while not self._halt:
      currentTime = time.time()
      
      if self.isPaused() or currentTime - lastDraw < 1 or not self._isTorConnected:
        self._cond.acquire()
        if not self._halt: self._cond.wait(0.2)
        self._cond.release()
      else:
        # Update the volatile attributes (cpu, memory, flags, etc) if we have
        # a new resource usage sampling (the most dynamic stat) or its been
        # twenty seconds since last fetched (so we still refresh occasionally
        # when resource fetches fail).
        # 
        # Otherwise, just redraw the panel to change the uptime field.
        
        isChanged = False
        if self.vals["tor/pid"]:
          resourceTracker = sysTools.getResourceTracker(self.vals["tor/pid"])
          isChanged = self._lastResourceFetch != resourceTracker.getRunCount()
        
        if isChanged or currentTime - self._lastUpdate >= 20:
          self._update()
        
        self.redraw(True)
        lastDraw += 1
  
  def stop(self):
    """
    Halts further resolutions and terminates the thread.
    """
    
    self._cond.acquire()
    self._halt = True
    self._cond.notifyAll()
    self._cond.release()
  
  def resetListener(self, _, eventType):
    """
    Updates static parameters on tor reload (sighup) events.
    
    Arguments:
      conn      - tor controller
      eventType - type of event detected
    """
    
    if eventType in (torTools.State.INIT, torTools.State.RESET):
      initialHeight = self.getHeight()
      self._isTorConnected = True
      self._haltTime = None
      self._update(True)
      
      if self.getHeight() != initialHeight:
        # We're toggling between being a relay and client, causing the height
        # of this panel to change. Redraw all content so we don't get
        # overlapping content.
        cli.controller.getController().redraw()
      else:
        # just need to redraw ourselves
        self.redraw(True)
    elif eventType == torTools.State.CLOSED:
      self._isTorConnected = False
      self._haltTime = time.time()
      self._update()
      self.redraw(True)
  
  def _update(self, setStatic=False):
    """
    Updates stats in the vals mapping. By default this just revises volatile
    attributes.
    
    Arguments:
      setStatic - resets all parameters, including relatively static values
    """
    
    self.valsLock.acquire()
    conn = torTools.getConn()
    
    if setStatic:
      # version is truncated to first part, for instance:
      # 0.2.2.13-alpha (git-feb8c1b5f67f2c6f) -> 0.2.2.13-alpha
      self.vals["tor/version"] = conn.getInfo("version", "Unknown").split()[0]
      self.vals["tor/versionStatus"] = conn.getInfo("status/version/current", "Unknown")
      self.vals["tor/nickname"] = conn.getOption("Nickname", "")
      self.vals["tor/orPort"] = conn.getOption("ORPort", "0")
      self.vals["tor/dirPort"] = conn.getOption("DirPort", "0")
      self.vals["tor/controlPort"] = conn.getOption("ControlPort", "0")
      self.vals["tor/socketPath"] = conn.getOption("ControlSocket", "")
      self.vals["tor/isAuthPassword"] = conn.getOption("HashedControlPassword") != None
      self.vals["tor/isAuthCookie"] = conn.getOption("CookieAuthentication") == "1"
      
      # orport is reported as zero if unset
      if self.vals["tor/orPort"] == "0": self.vals["tor/orPort"] = ""
      
      # overwrite address if ORListenAddress is set (and possibly orPort too)
      self.vals["tor/orListenAddr"] = ""
      listenAddr = conn.getOption("ORListenAddress")
      if listenAddr:
        if ":" in listenAddr:
          # both ip and port overwritten
          self.vals["tor/orListenAddr"] = listenAddr[:listenAddr.find(":")]
          self.vals["tor/orPort"] = listenAddr[listenAddr.find(":") + 1:]
        else:
          self.vals["tor/orListenAddr"] = listenAddr
      
      # fetch exit policy (might span over multiple lines)
      policyEntries = []
      for exitPolicy in conn.getOption("ExitPolicy", [], True):
        policyEntries += [policy.strip() for policy in exitPolicy.split(",")]
      self.vals["tor/exitPolicy"] = ", ".join(policyEntries)
      
      # file descriptor limit for the process, if this can't be determined
      # then the limit is None
      fdLimit, fdIsEstimate = conn.getMyFileDescriptorLimit()
      self.vals["tor/fdLimit"] = fdLimit
      self.vals["tor/isFdLimitEstimate"] = fdIsEstimate
      
      # system information
      unameVals = os.uname()
      self.vals["sys/hostname"] = unameVals[1]
      self.vals["sys/os"] = unameVals[0]
      self.vals["sys/version"] = unameVals[2]
      
      pid = conn.getMyPid()
      self.vals["tor/pid"] = pid if pid else ""
      
      startTime = conn.getStartTime()
      self.vals["tor/startTime"] = startTime if startTime else ""
      
      # reverts volatile parameters to defaults
      self.vals["tor/fingerprint"] = "Unknown"
      self.vals["tor/flags"] = []
      self.vals["tor/fdUsed"] = 0
      self.vals["stat/%torCpu"] = "0"
      self.vals["stat/%armCpu"] = "0"
      self.vals["stat/rss"] = "0"
      self.vals["stat/%mem"] = "0"
    
    # sets volatile parameters
    # TODO: This can change, being reported by STATUS_SERVER -> EXTERNAL_ADDRESS
    # events. Introduce caching via torTools?
    self.vals["tor/address"] = conn.getInfo("address", "")
    
    self.vals["tor/fingerprint"] = conn.getInfo("fingerprint", self.vals["tor/fingerprint"])
    self.vals["tor/flags"] = conn.getMyFlags(self.vals["tor/flags"])
    
    # Updates file descriptor usage and logs if the usage is high. If we don't
    # have a known limit or it's obviously faulty (being lower than our
    # current usage) then omit file descriptor functionality.
    if self.vals["tor/fdLimit"]:
      fdUsed = conn.getMyFileDescriptorUsage()
      if fdUsed and fdUsed <= self.vals["tor/fdLimit"]: self.vals["tor/fdUsed"] = fdUsed
      else: self.vals["tor/fdUsed"] = 0
    
    if self.vals["tor/fdUsed"] and self.vals["tor/fdLimit"]:
      fdPercent = 100 * self.vals["tor/fdUsed"] / self.vals["tor/fdLimit"]
      estimatedLabel = " estimated" if self.vals["tor/isFdLimitEstimate"] else ""
      msg = "Tor's%s file descriptor usage is at %i%%." % (estimatedLabel, fdPercent)
      
      if fdPercent >= 90 and not self._isFdNinetyPercentWarned:
        self._isFdSixtyPercentWarned, self._isFdNinetyPercentWarned = True, True
        msg += " If you run out Tor will be unable to continue functioning."
        log.log(self._config["log.fdUsageNinetyPercent"], msg)
      elif fdPercent >= 60 and not self._isFdSixtyPercentWarned:
        self._isFdSixtyPercentWarned = True
        log.log(self._config["log.fdUsageSixtyPercent"], msg)
    
    # ps or proc derived resource usage stats
    if self.vals["tor/pid"]:
      resourceTracker = sysTools.getResourceTracker(self.vals["tor/pid"])
      
      if resourceTracker.lastQueryFailed():
        self.vals["stat/%torCpu"] = "0"
        self.vals["stat/rss"] = "0"
        self.vals["stat/%mem"] = "0"
      else:
        cpuUsage, _, memUsage, memUsagePercent = resourceTracker.getResourceUsage()
        self._lastResourceFetch = resourceTracker.getRunCount()
        self.vals["stat/%torCpu"] = "%0.1f" % (100 * cpuUsage)
        self.vals["stat/rss"] = str(memUsage)
        self.vals["stat/%mem"] = "%0.1f" % (100 * memUsagePercent)
    
    # determines the cpu time for the arm process (including user and system
    # time of both the primary and child processes)
    
    totalArmCpuTime, currentTime = sum(os.times()[:3]), time.time()
    armCpuDelta = totalArmCpuTime - self._armCpuSampling[0]
    armTimeDelta = currentTime - self._armCpuSampling[1]
    pythonCpuTime = armCpuDelta / armTimeDelta
    sysCallCpuTime = sysTools.getSysCpuUsage()
    self.vals["stat/%armCpu"] = "%0.1f" % (100 * (pythonCpuTime + sysCallCpuTime))
    self._armCpuSampling = (totalArmCpuTime, currentTime)
    
    self._lastUpdate = currentTime
    self.valsLock.release()

