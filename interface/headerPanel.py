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
import threading

from util import conf, log, panel, sysTools, torTools, uiTools

# seconds between querying information
DEFAULT_UPDATE_RATE = 5
UPDATE_RATE_CFG = "updateRate.header"

# minimum width for which panel attempts to double up contents (two columns to
# better use screen real estate)
MIN_DUAL_COL_WIDTH = 141

FLAG_COLORS = {"Authority": "white",  "BadExit": "red",     "BadDirectory": "red",    "Exit": "cyan",
               "Fast": "yellow",      "Guard": "green",     "HSDir": "magenta",       "Named": "blue",
               "Stable": "blue",      "Running": "yellow",  "Unnamed": "magenta",     "Valid": "green",
               "V2Dir": "cyan",       "V3Dir": "white"}

VERSION_STATUS_COLORS = {"new": "blue", "new in series": "blue", "obsolete": "red", "recommended": "green",  
                         "old": "red",  "unrecommended": "red",  "unknown": "cyan"}

class HeaderPanel(panel.Panel, threading.Thread):
  """
  Top area contenting tor settings and system information. Stats are stored in
  the vals mapping, keys including:
    tor/ version, versionStatus, nickname, orPort, dirPort, controlPort,
         exitPolicy, isAuthPassword (bool), isAuthCookie (bool)
         *address, *fingerprint, *flags
    sys/ hostname, os, version
    ps/  *%cpu, *rss, *%mem, pid, *etime
  
  * volatile parameter that'll be reset on each update
  """
  
  def __init__(self, stdscr):
    panel.Panel.__init__(self, stdscr, 0)
    threading.Thread.__init__(self)
    self.setDaemon(True)
    
    # seconds between querying updates
    try:
      self._updateRate = int(conf.getConfig("arm").get(UPDATE_RATE_CFG, DEFAULT_UPDATE_RATE))
    except ValueError:
      # value wasn't an integer
      log.log(log.WARN, "Config: %s is expected to be an integer (defaulting to %i)" % (UPDATE_RATE_CFG, DEFAULT_UPDATE_RATE))
      self._updateRate = DEFAULT_UPDATE_RATE
    
    self._isTorConnected = True
    self._lastUpdate = -1       # time the content was last revised
    self._isLastDrawWide = False
    self._isChanged = False     # new stats to be drawn if true
    self._isPaused = False      # prevents updates if true
    self._halt = False          # terminates thread if true
    self._cond = threading.Condition()  # used for pausing the thread
    
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
    return 4 if isWide else 6
  
  def draw(self, subwindow, width, height):
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
      versionColor = VERSION_STATUS_COLORS[self.vals["tor/versionStatus"]] if \
          self.vals["tor/versionStatus"] in VERSION_STATUS_COLORS else "white"
      versionStatusMsg = "<%s>%s</%s>" % (versionColor, self.vals["tor/versionStatus"], versionColor)
      self.addfstr(0, 43, "Tor %s (%s)" % (self.vals["tor/version"], versionStatusMsg))
    elif 11 <= contentSpace:
      self.addstr(0, 43, uiTools.cropStr("Tor %s" % self.vals["tor/version"], contentSpace, 4))
    
    # Line 2 / Line 2 Left (tor ip/port information)
    entry = ""
    dirPortLabel = ", Dir Port: %s" % self.vals["tor/dirPort"] if self.vals["tor/dirPort"] != "0" else ""
    for label in (self.vals["tor/nickname"], " - " + self.vals["tor/address"], ":" + self.vals["tor/orPort"], dirPortLabel):
      if len(entry) + len(label) <= leftWidth: entry += label
      else: break
    
    if self.vals["tor/isAuthPassword"]: authType = "password"
    elif self.vals["tor/isAuthCookie"]: authType = "cookie"
    else: authType = "open"
    
    if len(entry) + 19 + len(self.vals["tor/controlPort"]) + len(authType) <= leftWidth:
      authColor = "red" if authType == "open" else "green"
      authLabel = "<%s>%s</%s>" % (authColor, authType, authColor)
      self.addfstr(1, 0, "%s, Control Port (%s): %s" % (entry, authLabel, self.vals["tor/controlPort"]))
    elif len(entry) + 16 + len(self.vals["tor/controlPort"]) <= leftWidth:
      self.addstr(1, 0, "%s, Control Port: %s" % (entry, self.vals["tor/controlPort"]))
    else: self.addstr(1, 0, entry)
    
    # Line 3 / Line 1 Right (system usage info)
    y, x = (0, leftWidth) if isWide else (2, 0)
    if self.vals["ps/rss"] != "0": memoryLabel = uiTools.getSizeLabel(int(self.vals["ps/rss"]) * 1024)
    else: memoryLabel = "0"
    
    sysFields = ((0, "cpu: %s%%" % self.vals["ps/%cpu"]),
                 (13, "mem: %s (%s%%)" % (memoryLabel, self.vals["ps/%mem"])),
                 (34, "pid: %s" % (self.vals["ps/pid"] if self._isTorConnected else "")),
                 (47, "uptime: %s" % self.vals["ps/etime"]))
    
    for (start, label) in sysFields:
      if start + len(label) <= rightWidth: self.addstr(y, x + start, label)
      else: break
    
    # Line 4 / Line 2 Right (fingerprint)
    y, x = (1, leftWidth) if isWide else (3, 0)
    self.addstr(y, x, "fingerprint: %s" % self.vals["tor/fingerprint"])
    
    # Line 5 / Line 3 Left (flags)
    if self._isTorConnected:
      flagLine = "flags: "
      for flag in self.vals["tor/flags"]:
        flagColor = FLAG_COLORS[flag] if flag in FLAG_COLORS.keys() else "white"
        flagLine += "<b><%s>%s</%s></b>, " % (flagColor, flag, flagColor)
      
      if len(self.vals["tor/flags"]) > 0: flagLine = flagLine[:-2]
      else: flagLine += "<b><cyan>none</cyan></b>"
      
      self.addfstr(2 if isWide else 4, 0, flagLine)
    else:
      statusTime = torTools.getConn().getStatus()[1]
      statusTimeLabel = time.strftime("%H:%M %m/%d/%Y", time.localtime(statusTime))
      self.addfstr(2 if isWide else 4, 0, "<b><red>Tor Disconnected</red></b> (%s)" % statusTimeLabel)
    
    # Undisplayed / Line 3 Right (exit policy)
    if isWide:
      exitPolicy = self.vals["tor/exitPolicy"]
      
      # adds note when default exit policy is appended
      if exitPolicy == None: exitPolicy = "<default>"
      elif not exitPolicy.endswith((" *:*", " *")): exitPolicy += ", <default>"
      
      # color codes accepts to be green, rejects to be red, and default marker to be cyan
      isSimple = len(exitPolicy) > rightWidth - 13
      policies = exitPolicy.split(", ")
      for i in range(len(policies)):
        policy = policies[i].strip()
        displayedPolicy = policy.replace("accept", "").replace("reject", "").strip() if isSimple else policy
        if policy.startswith("accept"): policy = "<green><b>%s</b></green>" % displayedPolicy
        elif policy.startswith("reject"): policy = "<red><b>%s</b></red>" % displayedPolicy
        elif policy.startswith("<default>"): policy = "<cyan><b>%s</b></cyan>" % displayedPolicy
        policies[i] = policy
      
      self.addfstr(2, leftWidth, "exit policy: %s" % ", ".join(policies))
    
    self._isLastDrawWide = isWide
    self._isChanged = False
    self.valsLock.release()
  
  def redraw(self, forceRedraw=False, block=False):
    # determines if the content needs to be redrawn or not
    isWide = self.getParent().getmaxyx()[1] >= MIN_DUAL_COL_WIDTH
    panel.Panel.redraw(self, forceRedraw or self._isChanged or isWide != self._isLastDrawWide, block)
  
  def setPaused(self, isPause):
    """
    If true, prevents updates from being presented.
    """
    
    self._isPaused = isPause
  
  def run(self):
    """
    Keeps stats updated, querying new information at a set rate.
    """
    
    while not self._halt:
      timeSinceReset = time.time() - self._lastUpdate
      
      if self._isPaused or timeSinceReset < self._updateRate or not self._isTorConnected:
        sleepTime = max(0.5, self._updateRate - timeSinceReset)
        self._cond.acquire()
        if not self._halt: self._cond.wait(sleepTime)
        self._cond.release()
      else:
        self._update()
        self.redraw()
  
  def stop(self):
    """
    Halts further resolutions and terminates the thread.
    """
    
    self._cond.acquire()
    self._halt = True
    self._cond.notifyAll()
    self._cond.release()
  
  def resetListener(self, conn, eventType):
    """
    Updates static parameters on tor reload (sighup) events.
    
    Arguments:
      conn      - tor controller
      eventType - type of event detected
    """
    
    if eventType == torTools.TOR_INIT:
      self._isTorConnected = True
      self._update(True)
      self.redraw()
    elif eventType == torTools.TOR_CLOSED:
      self._isTorConnected = False
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
      self.vals["tor/orPort"] = conn.getOption("ORPort", "")
      self.vals["tor/dirPort"] = conn.getOption("DirPort", "0")
      self.vals["tor/controlPort"] = conn.getOption("ControlPort", "")
      self.vals["tor/isAuthPassword"] = conn.getOption("HashedControlPassword") != None
      self.vals["tor/isAuthCookie"] = conn.getOption("CookieAuthentication") == "1"
      
      # fetch exit policy (might span over multiple lines)
      policyEntries = []
      for exitPolicy in conn.getOption("ExitPolicy", [], True):
        policyEntries += [policy.strip() for policy in exitPolicy[1].split(",")]
      self.vals["tor/exitPolicy"] = ", ".join(policyEntries)
      
      # system information
      unameVals = os.uname()
      self.vals["sys/hostname"] = unameVals[1]
      self.vals["sys/os"] = unameVals[0]
      self.vals["sys/version"] = unameVals[2]
      
      pid = conn.getPid()
      self.vals["ps/pid"] = pid if pid else ""
      
      # reverts volatile parameters to defaults
      self.vals["tor/address"] = "Unknown"
      self.vals["tor/fingerprint"] = "Unknown"
      self.vals["tor/flags"] = []
      self.vals["ps/%cpu"] = "0"
      self.vals["ps/rss"] = "0"
      self.vals["ps/%mem"] = "0"
      self.vals["ps/etime"] = ""
    
    # sets volatile parameters
    volatile = {}
    volatile["tor/address"] = conn.getInfo("address", self.vals["tor/address"])
    volatile["tor/fingerprint"] = conn.getInfo("fingerprint", self.vals["tor/fingerprint"])
    
    # overwrite address if ORListenAddress is set (and possibly orPort too)
    listenAddr = conn.getOption("ORListenAddress")
    if listenAddr:
      if ":" in listenAddr:
        # both ip and port overwritten
        volatile["address"] = listenAddr[:listenAddr.find(":")]
        volatile["orPort"] = listenAddr[listenAddr.find(":") + 1:]
      else:
        volatile["address"] = listenAddr
    
    # sets flags
    if self.vals["tor/fingerprint"] != "Unknown":
      # network status contains a couple of lines, looking like:
      # r caerSidi p1aag7VwarGxqctS7/fS0y5FU+s 9On1TRGCEpljszPpJR1hKqlzaY8 2010-05-26 09:26:06 76.104.132.98 9001 0
      # s Fast HSDir Named Running Stable Valid
      nsResults = conn.getInfo("ns/id/%s" % self.vals["tor/fingerprint"], "").split("\n")
      if len(nsResults) >= 2: volatile["tor/flags"] = nsResults[1][2:].split()
    
    # ps derived stats
    psParams = ["%cpu", "rss", "%mem", "etime"]
    if self.vals["ps/pid"]:
      # if call fails then everything except etime are zeroed out (most likely
      # tor's no longer running)
      volatile["ps/%cpu"] = "0"
      volatile["ps/rss"] = "0"
      volatile["ps/%mem"] = "0"
      
      # the ps call formats results as:
      # %CPU   RSS %MEM     ELAPSED
      # 0.3 14096  1.3       29:51
      psCall = sysTools.call("ps -p %s -o %s" % (self.vals["ps/pid"], ",".join(psParams)), self._updateRate, True)
      
      if psCall and len(psCall) >= 2:
        stats = psCall[1].strip().split()
        
        if len(stats) == len(psParams):
          for i in range(len(psParams)):
            volatile["ps/" + psParams[i]] = stats[i]
    
    # checks if any changes have been made and merges volatile into vals
    self._isChanged |= setStatic
    for key, val in volatile.items():
      self._isChanged |= self.vals[key] != val
      self.vals[key] = val
    
    self._lastUpdate = time.time()
    self.valsLock.release()

