#!/usr/bin/env python
# controller.py -- arm interface (curses monitor for relay status)
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

"""
Curses (terminal) interface for the arm relay status monitor.
"""

import os
import re
import math
import time
import curses
import curses.textpad
import socket
from TorCtl import TorCtl

import popups
import headerPanel
import graphing.graphPanel
import logPanel
import configPanel
import torrcPanel
import descriptorPopup

import cli.connections.connPanel
import cli.connections.connEntry
import cli.connections.entries
from util import conf, log, connections, hostnames, panel, sysTools, torConfig, torTools, uiTools
import graphing.bandwidthStats
import graphing.connStats
import graphing.resourceStats

# TODO: controller should be its own object that can be refreshed - until that
# emulating via a 'refresh' flag
REFRESH_FLAG = False

def refresh():
  global REFRESH_FLAG
  REFRESH_FLAG = True

# new panel params and accessors (this is part of the new controller apis)
PANELS = {}
STDSCR = None
IS_PAUSED = False

def getScreen():
  return STDSCR

def getPage():
  """
  Provides the number belonging to this page. Page numbers start at one.
  """
  
  return PAGE + 1

def getPanel(name):
  """
  Provides the panel with the given identifier.
  
  Arguments:
    name - name of the panel to be fetched
  """
  
  return PANELS[name]

def getPanels(page = None):
  """
  Provides all panels or all panels from a given page.
  
  Arguments:
    page - page number of the panels to be fetched, all panels if undefined
  """
  
  panelSet = []
  if page == None:
    # fetches all panel names
    panelSet = list(PAGE_S)
    for pagePanels in PAGES:
      panelSet += pagePanels
  else: panelSet = PAGES[page - 1]
  
  return [getPanel(name) for name in panelSet]

CONFIRM_QUIT = True
REFRESH_RATE = 5        # seconds between redrawing screen

# enums for message in control label
CTL_HELP, CTL_PAUSED = range(2)

# panel order per page
PAGE_S = ["header", "control", "popup"] # sticky (ie, always available) page
PAGES = [
  ["graph", "log"],
  ["conn"],
  ["config"],
  ["torrc"]]

CONFIG = {"log.torrc.readFailed": log.WARN,
          "features.graph.type": 1,
          "queries.refreshRate.rate": 5,
          "log.torEventTypeUnrecognized": log.NOTICE,
          "features.graph.bw.prepopulate": True,
          "log.startTime": log.INFO,
          "log.refreshRate": log.DEBUG,
          "log.highCpuUsage": log.WARN,
          "log.configEntryUndefined": log.NOTICE,
          "log.torrc.validation.torStateDiffers": log.WARN,
          "log.torrc.validation.unnecessaryTorrcEntries": log.NOTICE}

class ControlPanel(panel.Panel):
  """ Draws single line label for interface controls. """
  
  def __init__(self, stdscr, isBlindMode):
    panel.Panel.__init__(self, stdscr, "control", 0, 1)
    self.msgText = CTL_HELP           # message text to be displyed
    self.msgAttr = curses.A_NORMAL    # formatting attributes
    self.page = 1                     # page number currently being displayed
    self.resolvingCounter = -1        # count of resolver when starting (-1 if we aren't working on a batch)
    self.isBlindMode = isBlindMode
  
  def setMsg(self, msgText, msgAttr=curses.A_NORMAL):
    """
    Sets the message and display attributes. If msgType matches CTL_HELP or
    CTL_PAUSED then uses the default message for those statuses.
    """
    
    self.msgText = msgText
    self.msgAttr = msgAttr
  
  def revertMsg(self):
    self.setMsg(CTL_PAUSED if IS_PAUSED else CTL_HELP)
  
  def draw(self, width, height):
    msgText = self.msgText
    msgAttr = self.msgAttr
    barTab = 2                # space between msgText and progress bar
    barWidthMax = 40          # max width to progress bar
    barWidth = -1             # space between "[ ]" in progress bar (not visible if -1)
    barProgress = 0           # cells to fill
    
    if msgText == CTL_HELP:
      msgAttr = curses.A_NORMAL
      
      if self.resolvingCounter != -1:
        if hostnames.isPaused() or not hostnames.isResolving():
          # done resolving dns batch
          self.resolvingCounter = -1
          curses.halfdelay(REFRESH_RATE * 10) # revert to normal refresh rate
        else:
          batchSize = hostnames.getRequestCount() - self.resolvingCounter
          entryCount = batchSize - hostnames.getPendingCount()
          if batchSize > 0: progress = 100 * entryCount / batchSize
          else: progress = 0
          
          additive = "or l " if self.page == 2 else ""
          batchSizeDigits = int(math.log10(batchSize)) + 1
          entryCountLabel = ("%%%ii" % batchSizeDigits) % entryCount
          #msgText = "Resolving hostnames (%i / %i, %i%%) - press esc %sto cancel" % (entryCount, batchSize, progress, additive)
          msgText = "Resolving hostnames (press esc %sto cancel) - %s / %i, %2i%%" % (additive, entryCountLabel, batchSize, progress)
          
          barWidth = min(barWidthMax, width - len(msgText) - 3 - barTab)
          barProgress = barWidth * entryCount / batchSize
      
      if self.resolvingCounter == -1:
        currentPage = self.page
        pageCount = len(PAGES)
        
        if self.isBlindMode:
          if currentPage >= 2: currentPage -= 1
          pageCount -= 1
        
        msgText = "page %i / %i - q: quit, p: pause, h: page help" % (currentPage, pageCount)
    elif msgText == CTL_PAUSED:
      msgText = "Paused"
      msgAttr = curses.A_STANDOUT
    
    self.addstr(0, 0, msgText, msgAttr)
    if barWidth > -1:
      xLoc = len(msgText) + barTab
      self.addstr(0, xLoc, "[", curses.A_BOLD)
      self.addstr(0, xLoc + 1, " " * barProgress, curses.A_STANDOUT | uiTools.getColor("red"))
      self.addstr(0, xLoc + barWidth + 1, "]", curses.A_BOLD)

class Popup(panel.Panel):
  """
  Temporarily providing old panel methods until permanent workaround for popup
  can be derrived (this passive drawing method is horrible - I'll need to
  provide a version using the more active repaint design later in the
  revision).
  """
  
  def __init__(self, stdscr, height):
    panel.Panel.__init__(self, stdscr, "popup", 0, height)
  
  def setPaused(self, isPause):
    panel.Panel.setPaused(self, isPause, True)
  
  # The following methods are to emulate old panel functionality (this was the
  # only implementations to use these methods and will require a complete
  # rewrite when refactoring gets here)
  def clear(self):
    if self.win:
      self.isDisplaced = self.top > self.win.getparyx()[0]
      if not self.isDisplaced: self.win.erase()
  
  def refresh(self):
    if self.win and not self.isDisplaced: self.win.refresh()
  
  def recreate(self, stdscr, newWidth=-1, newTop=None):
    self.setParent(stdscr)
    self.setWidth(newWidth)
    if newTop != None: self.setTop(newTop)
    
    newHeight, newWidth = self.getPreferredSize()
    if newHeight > 0:
      self.win = self.parent.subwin(newHeight, newWidth, self.top, 0)
    elif self.win == None:
      # don't want to leave the window as none (in very edge cases could cause
      # problems) - rather, create a displaced instance
      self.win = self.parent.subwin(1, newWidth, 0, 0)
    
    self.maxY, self.maxX = self.win.getmaxyx()

def addstr_wrap(panel, y, x, text, formatting, startX = 0, endX = -1, maxY = -1):
  """
  Writes text with word wrapping, returning the ending y/x coordinate.
  y: starting write line
  x: column offset from startX
  text / formatting: content to be written
  startX / endX: column bounds in which text may be written
  """
  
  # moved out of panel (trying not to polute new code!)
  # TODO: unpleaseantly complex usage - replace with something else when
  # rewriting confPanel and descriptorPopup (the only places this is used)
  if not text: return (y, x)          # nothing to write
  if endX == -1: endX = panel.maxX     # defaults to writing to end of panel
  if maxY == -1: maxY = panel.maxY + 1 # defaults to writing to bottom of panel
  lineWidth = endX - startX           # room for text
  while True:
    if len(text) > lineWidth - x - 1:
      chunkSize = text.rfind(" ", 0, lineWidth - x)
      writeText = text[:chunkSize]
      text = text[chunkSize:].strip()
      
      panel.addstr(y, x + startX, writeText, formatting)
      y, x = y + 1, 0
      if y >= maxY: return (y, x)
    else:
      panel.addstr(y, x + startX, text, formatting)
      return (y, x + len(text))

class sighupListener(TorCtl.PostEventListener):
  """
  Listens for reload signal (hup), which is produced by:
  pkill -sighup tor
  causing the torrc and internal state to be reset.
  """
  
  def __init__(self):
    TorCtl.PostEventListener.__init__(self)
    self.isReset = False
  
  def msg_event(self, event):
    self.isReset |= event.level == "NOTICE" and event.msg.startswith("Received reload signal (hup)")

def setPauseState(panels, monitorIsPaused, currentPage, overwrite=False):
  """
  Resets the isPaused state of panels. If overwrite is True then this pauses
  reguardless of the monitor is paused or not.
  """
  
  allPanels = list(PAGE_S)
  for pagePanels in PAGES:
    allPanels += pagePanels
  
  for key in allPanels: panels[key].setPaused(overwrite or monitorIsPaused or (key not in PAGES[currentPage] and key not in PAGE_S))

def setEventListening(selectedEvents, isBlindMode):
  # creates a local copy, note that a suspected python bug causes *very*
  # puzzling results otherwise when trying to discard entries (silently
  # returning out of this function!)
  events = set(selectedEvents)
  isLoggingUnknown = "UNKNOWN" in events
  
  # removes special types only used in arm (UNKNOWN, TORCTL, ARM_DEBUG, etc)
  toDiscard = []
  for eventType in events:
    if eventType not in logPanel.TOR_EVENT_TYPES.values(): toDiscard += [eventType]
  
  for eventType in list(toDiscard): events.discard(eventType)
  
  # adds events unrecognized by arm if we're listening to the 'UNKNOWN' type
  if isLoggingUnknown:
    events.update(set(logPanel.getMissingEventTypes()))
  
  setEvents = torTools.getConn().setControllerEvents(list(events))
  
  # temporary hack for providing user selected events minus those that failed
  # (wouldn't be a problem if I wasn't storing tor and non-tor events together...)
  returnVal = list(selectedEvents.difference(torTools.FAILED_EVENTS))
  returnVal.sort() # alphabetizes
  return returnVal

def connResetListener(conn, eventType):
  """
  Pauses connection resolution when tor's shut down, and resumes if started
  again.
  """
  
  if connections.isResolverAlive("tor"):
    resolver = connections.getResolver("tor")
    resolver.setPaused(eventType == torTools.State.CLOSED)

def selectiveRefresh(panels, page):
  """
  This forces a redraw of content on the currently active page (should be done
  after changing pages, popups, or anything else that overwrites panels).
  """
  
  for panelKey in PAGES[page]:
    panels[panelKey].redraw(True)

def drawTorMonitor(stdscr, startTime, loggedEvents, isBlindMode):
  """
  Starts arm interface reflecting information on provided control port.
  
  stdscr - curses window
  conn - active Tor control port connection
  loggedEvents - types of events to be logged (plus an optional "UNKNOWN" for
    otherwise unrecognized events)
  """
  
  global PANELS, STDSCR, REFRESH_FLAG, PAGE, IS_PAUSED
  STDSCR = stdscr
  
  # loads config for various interface components
  config = conf.getConfig("arm")
  config.update(CONFIG)
  graphing.graphPanel.loadConfig(config)
  cli.connections.connEntry.loadConfig(config)
  
  # adds events needed for arm functionality to the torTools REQ_EVENTS mapping
  # (they're then included with any setControllerEvents call, and log a more
  # helpful error if unavailable)
  torTools.REQ_EVENTS["BW"] = "bandwidth graph won't function"
  
  if not isBlindMode:
    torTools.REQ_EVENTS["CIRC"] = "may cause issues in identifying client connections"
  
  # pauses/unpauses connection resolution according to if tor's connected or not
  torTools.getConn().addStatusListener(connResetListener)
  
  # TODO: incrementally drop this requirement until everything's using the singleton
  conn = torTools.getConn().getTorCtl()
  
  curses.halfdelay(REFRESH_RATE * 10)   # uses getch call as timer for REFRESH_RATE seconds
  try: curses.use_default_colors()      # allows things like semi-transparent backgrounds (call can fail with ERR)
  except curses.error: pass
  
  # attempts to make the cursor invisible (not supported in all terminals)
  try: curses.curs_set(0)
  except curses.error: pass
  
  # attempts to determine tor's current pid (left as None if unresolveable, logging an error later)
  torPid = torTools.getConn().getMyPid()
  
  #try:
  #  confLocation = conn.get_info("config-file")["config-file"]
  #  if confLocation[0] != "/":
  #    # relative path - attempt to add process pwd
  #    try:
  #      results = sysTools.call("pwdx %s" % torPid)
  #      if len(results) == 1 and len(results[0].split()) == 2: confLocation = "%s/%s" % (results[0].split()[1], confLocation)
  #    except IOError: pass # pwdx call failed
  #except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed):
  #  confLocation = ""
  
  # loads the torrc and provides warnings in case of validation errors
  loadedTorrc = torConfig.getTorrc()
  loadedTorrc.getLock().acquire()
  
  try:
    loadedTorrc.load()
  except IOError, exc:
    msg = "Unable to load torrc (%s)" % sysTools.getFileErrorMsg(exc)
    log.log(CONFIG["log.torrc.readFailed"], msg)
  
  if loadedTorrc.isLoaded():
    corrections = loadedTorrc.getCorrections()
    duplicateOptions, defaultOptions, mismatchLines, missingOptions = [], [], [], []
    
    for lineNum, issue, msg in corrections:
      if issue == torConfig.ValidationError.DUPLICATE:
        duplicateOptions.append("%s (line %i)" % (msg, lineNum + 1))
      elif issue == torConfig.ValidationError.IS_DEFAULT:
        defaultOptions.append("%s (line %i)" % (msg, lineNum + 1))
      elif issue == torConfig.ValidationError.MISMATCH: mismatchLines.append(lineNum + 1)
      elif issue == torConfig.ValidationError.MISSING: missingOptions.append(msg)
    
    if duplicateOptions or defaultOptions:
      msg = "Unneeded torrc entries found. They've been highlighted in blue on the torrc page."
      
      if duplicateOptions:
        if len(duplicateOptions) > 1:
          msg += "\n- entries ignored due to having duplicates: "
        else:
          msg += "\n- entry ignored due to having a duplicate: "
        
        duplicateOptions.sort()
        msg += ", ".join(duplicateOptions)
      
      if defaultOptions:
        if len(defaultOptions) > 1:
          msg += "\n- entries match their default values: "
        else:
          msg += "\n- entry matches its default value: "
        
        defaultOptions.sort()
        msg += ", ".join(defaultOptions)
      
      log.log(CONFIG["log.torrc.validation.unnecessaryTorrcEntries"], msg)
    
    if mismatchLines or missingOptions:
      msg = "The torrc differ from what tor's using. You can issue a sighup to reload the torrc values by pressing x."
      
      if mismatchLines:
        if len(mismatchLines) > 1:
          msg += "\n- torrc values differ on lines: "
        else:
          msg += "\n- torrc value differs on line: "
        
        mismatchLines.sort()
        msg += ", ".join([str(val + 1) for val in mismatchLines])
        
      if missingOptions:
        if len(missingOptions) > 1:
          msg += "\n- configuration values are missing from the torrc: "
        else:
          msg += "\n- configuration value is missing from the torrc: "
        
        missingOptions.sort()
        msg += ", ".join(missingOptions)
      
      log.log(CONFIG["log.torrc.validation.torStateDiffers"], msg)
  
  loadedTorrc.getLock().release()
  
  # minor refinements for connection resolver
  if not isBlindMode:
    if torPid:
      # use the tor pid to help narrow connection results
      torCmdName = sysTools.getProcessName(torPid, "tor")
      resolver = connections.getResolver(torCmdName, torPid, "tor")
    else:
      resolver = connections.getResolver("tor")
  
  # hack to display a better (arm specific) notice if all resolvers fail
  connections.RESOLVER_FINAL_FAILURE_MSG += " (connection related portions of the monitor won't function)"
  
  panels = {
    "header": headerPanel.HeaderPanel(stdscr, startTime, config),
    "popup": Popup(stdscr, 9),
    "graph": graphing.graphPanel.GraphPanel(stdscr),
    "log": logPanel.LogPanel(stdscr, loggedEvents, config)}
  
  # TODO: later it would be good to set the right 'top' values during initialization, 
  # but for now this is just necessary for the log panel (and a hack in the log...)
  
  # TODO: bug from not setting top is that the log panel might attempt to draw
  # before being positioned - the following is a quick hack til rewritten
  panels["log"].setPaused(True)
  
  panels["conn"] = cli.connections.connPanel.ConnectionPanel(stdscr, config)
  
  panels["control"] = ControlPanel(stdscr, isBlindMode)
  panels["config"] = configPanel.ConfigPanel(stdscr, configPanel.State.TOR, config)
  panels["torrc"] = torrcPanel.TorrcPanel(stdscr, torrcPanel.Config.TORRC, config)
  
  # provides error if pid coulnd't be determined (hopefully shouldn't happen...)
  if not torPid: log.log(log.WARN, "Unable to resolve tor pid, abandoning connection listing")
  
  # statistical monitors for graph
  panels["graph"].addStats("bandwidth", graphing.bandwidthStats.BandwidthStats(config))
  panels["graph"].addStats("system resources", graphing.resourceStats.ResourceStats())
  if not isBlindMode: panels["graph"].addStats("connections", graphing.connStats.ConnStats())
  
  # sets graph based on config parameter
  graphType = CONFIG["features.graph.type"]
  if graphType == 0: panels["graph"].setStats(None)
  elif graphType == 1: panels["graph"].setStats("bandwidth")
  elif graphType == 2 and not isBlindMode: panels["graph"].setStats("connections")
  elif graphType == 3: panels["graph"].setStats("system resources")
  
  # listeners that update bandwidth and log panels with Tor status
  sighupTracker = sighupListener()
  #conn.add_event_listener(panels["log"])
  conn.add_event_listener(panels["graph"].stats["bandwidth"])
  conn.add_event_listener(panels["graph"].stats["system resources"])
  if not isBlindMode: conn.add_event_listener(panels["graph"].stats["connections"])
  conn.add_event_listener(sighupTracker)
  
  # prepopulates bandwidth values from state file
  if CONFIG["features.graph.bw.prepopulate"]:
    isSuccessful = panels["graph"].stats["bandwidth"].prepopulateFromState()
    if isSuccessful: panels["graph"].updateInterval = 4
  
  # tells Tor to listen to the events we're interested
  loggedEvents = setEventListening(loggedEvents, isBlindMode)
  #panels["log"].loggedEvents = loggedEvents # strips any that couldn't be set
  panels["log"].setLoggedEvents(loggedEvents) # strips any that couldn't be set
  
  # directs logged TorCtl events to log panel
  #TorUtil.loglevel = "DEBUG"
  #TorUtil.logfile = panels["log"]
  #torTools.getConn().addTorCtlListener(panels["log"].tor_ctl_event)
  
  # provides a notice about any event types tor supports but arm doesn't
  missingEventTypes = logPanel.getMissingEventTypes()
  if missingEventTypes:
    pluralLabel = "s" if len(missingEventTypes) > 1 else ""
    log.log(CONFIG["log.torEventTypeUnrecognized"], "arm doesn't recognize the following event type%s: %s (log 'UNKNOWN' events to see them)" % (pluralLabel, ", ".join(missingEventTypes)))
  
  PANELS = panels
  
  # tells revised panels to run as daemons
  panels["header"].start()
  panels["log"].start()
  panels["conn"].start()
  
  # warns if tor isn't updating descriptors
  #try:
  #  if conn.get_option("FetchUselessDescriptors")[0][1] == "0" and conn.get_option("DirPort")[0][1] == "0":
  #    warning = """Descriptors won't be updated (causing some connection information to be stale) unless:
  #a. 'FetchUselessDescriptors 1' is set in your torrc
  #b. the directory service is provided ('DirPort' defined)
  #c. or tor is used as a client"""
  #    log.log(log.WARN, warning)
  #except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed): pass
  
  isUnresponsive = False    # true if it's been over ten seconds since the last BW event (probably due to Tor closing)
  isPaused = False          # if true updates are frozen
  overrideKey = None        # immediately runs with this input rather than waiting for the user if set
  page = 0
  panels["popup"].redraw(True)  # hack to make sure popup has a window instance (not entirely sure why...)
  
  PAGE = page
  
  # provides notice about any unused config keys
  for key in config.getUnusedKeys():
    log.log(CONFIG["log.configEntryUndefined"], "Unused configuration entry: %s" % key)
  
  lastPerformanceLog = 0 # ensures we don't do performance logging too frequently
  redrawStartTime = time.time()
  
  # TODO: popups need to force the panels it covers to redraw (or better, have
  # a global refresh function for after changing pages, popups, etc)
  
  initTime = time.time() - startTime
  log.log(CONFIG["log.startTime"], "arm started (initialization took %0.3f seconds)" % initTime)
  
  # attributes to give a WARN level event if arm's resource usage is too high
  isResourceWarningGiven = False
  lastResourceCheck = startTime
  
  lastSize = None
  
  # sets initial visiblity for the pages
  for entry in PAGE_S: panels[entry].setVisible(True)
  
  for i in range(len(PAGES)):
    isVisible = i == page
    for entry in PAGES[i]: panels[entry].setVisible(isVisible)
  
  # TODO: come up with a nice, clean method for other threads to immediately
  # terminate the draw loop and provide a stacktrace
  while True:
    # tried only refreshing when the screen was resized but it caused a
    # noticeable lag when resizing and didn't have an appreciable effect
    # on system usage
    
    panel.CURSES_LOCK.acquire()
    try:
      redrawStartTime = time.time()
      
      # if sighup received then reload related information
      if sighupTracker.isReset:
        #panels["header"]._updateParams(True)
        
        # other panels that use torrc data
        #if not isBlindMode: panels["graph"].stats["connections"].resetOptions(conn)
        #panels["graph"].stats["bandwidth"].resetOptions()
        
        # if bandwidth graph is being shown then height might have changed
        if panels["graph"].currentDisplay == "bandwidth":
          panels["graph"].setHeight(panels["graph"].stats["bandwidth"].getContentHeight())
        
        # TODO: should redraw the torrcPanel
        #panels["torrc"].loadConfig()
        
        # reload the torrc if it's previously been loaded
        if loadedTorrc.isLoaded():
          try:
            loadedTorrc.load()
            if page == 3: panels["torrc"].redraw(True)
          except IOError, exc:
            msg = "Unable to load torrc (%s)" % sysTools.getFileErrorMsg(exc)
            log.log(CONFIG["log.torrc.readFailed"], msg)
        
        sighupTracker.isReset = False
      
      # gives panels a chance to take advantage of the maximum bounds
      # originally this checked in the bounds changed but 'recreate' is a no-op
      # if panel properties are unchanged and checking every redraw is more
      # resilient in case of funky changes (such as resizing during popups)
      
      # hack to make sure header picks layout before using the dimensions below
      #panels["header"].getPreferredSize()
      
      startY = 0
      for panelKey in PAGE_S[:2]:
        #panels[panelKey].recreate(stdscr, -1, startY)
        panels[panelKey].setParent(stdscr)
        panels[panelKey].setWidth(-1)
        panels[panelKey].setTop(startY)
        startY += panels[panelKey].getHeight()
      
      panels["popup"].recreate(stdscr, 80, startY)
      
      for panelSet in PAGES:
        tmpStartY = startY
        
        for panelKey in panelSet:
          #panels[panelKey].recreate(stdscr, -1, tmpStartY)
          panels[panelKey].setParent(stdscr)
          panels[panelKey].setWidth(-1)
          panels[panelKey].setTop(tmpStartY)
          tmpStartY += panels[panelKey].getHeight()
      
      # provides a notice if there's been ten seconds since the last BW event
      lastHeartbeat = torTools.getConn().getHeartbeat()
      if torTools.getConn().isAlive() and "BW" in torTools.getConn().getControllerEvents() and lastHeartbeat != 0:
        if not isUnresponsive and (time.time() - lastHeartbeat) >= 10:
          isUnresponsive = True
          log.log(log.NOTICE, "Relay unresponsive (last heartbeat: %s)" % time.ctime(lastHeartbeat))
        elif isUnresponsive and (time.time() - lastHeartbeat) < 10:
          # really shouldn't happen (meant Tor froze for a bit)
          isUnresponsive = False
          log.log(log.NOTICE, "Relay resumed")
      
      # TODO: part two of hack to prevent premature drawing by log panel
      if page == 0 and not isPaused: panels["log"].setPaused(False)
      
      # I haven't the foggiest why, but doesn't work if redrawn out of order...
      for panelKey in (PAGE_S + PAGES[page]):
        # redrawing popup can result in display flicker when it should be hidden
        if panelKey != "popup":
          newSize = stdscr.getmaxyx()
          isResize = lastSize != newSize
          lastSize = newSize
          
          if panelKey in ("header", "graph", "log", "config", "torrc", "conn2"):
            # revised panel (manages its own content refreshing)
            panels[panelKey].redraw(isResize)
          else:
            panels[panelKey].redraw(True)
      
      stdscr.refresh()
      
      currentTime = time.time()
      if currentTime - lastPerformanceLog >= CONFIG["queries.refreshRate.rate"]:
        cpuTotal = sum(os.times()[:3])
        pythonCpuAvg = cpuTotal / (currentTime - startTime)
        sysCallCpuAvg = sysTools.getSysCpuUsage()
        totalCpuAvg = pythonCpuAvg + sysCallCpuAvg
        
        if sysCallCpuAvg > 0.00001:
          log.log(CONFIG["log.refreshRate"], "refresh rate: %0.3f seconds, average cpu usage: %0.3f%% (python), %0.3f%% (system calls), %0.3f%% (total)" % (currentTime - redrawStartTime, 100 * pythonCpuAvg, 100 * sysCallCpuAvg, 100 * totalCpuAvg))
        else:
          # with the proc enhancements the sysCallCpuAvg is usually zero
          log.log(CONFIG["log.refreshRate"], "refresh rate: %0.3f seconds, average cpu usage: %0.3f%%" % (currentTime - redrawStartTime, 100 * totalCpuAvg))
        
        lastPerformanceLog = currentTime
        
        # once per minute check if the sustained cpu usage is above 5%, if so
        # then give a warning (and if able, some advice for lowering it)
        # TODO: disabling this for now (scrolling causes cpu spikes for quick
        # redraws, ie this is usually triggered by user input)
        if False and not isResourceWarningGiven and currentTime > (lastResourceCheck + 60):
          if totalCpuAvg >= 0.05:
            msg = "Arm's cpu usage is high (averaging %0.3f%%)." % (100 * totalCpuAvg)
            
            if not isBlindMode:
              msg += " You could lower it by dropping the connection data (running as \"arm -b\")."
            
            log.log(CONFIG["log.highCpuUsage"], msg)
            isResourceWarningGiven = True
          
          lastResourceCheck = currentTime
    finally:
      panel.CURSES_LOCK.release()
    
    # wait for user keyboard input until timeout (unless an override was set)
    if overrideKey:
      key = overrideKey
      overrideKey = None
    else:
      key = stdscr.getch()
    
    if key == ord('q') or key == ord('Q'):
      quitConfirmed = not CONFIRM_QUIT
      
      # provides prompt to confirm that arm should exit
      if CONFIRM_QUIT:
        panel.CURSES_LOCK.acquire()
        try:
          setPauseState(panels, isPaused, page, True)
          
          # provides prompt
          panels["control"].setMsg("Are you sure (q again to confirm)?", curses.A_BOLD)
          panels["control"].redraw(True)
          
          curses.cbreak()
          confirmationKey = stdscr.getch()
          quitConfirmed = confirmationKey in (ord('q'), ord('Q'))
          curses.halfdelay(REFRESH_RATE * 10)
          
          if not quitConfirmed:
            panels["control"].setMsg(CTL_PAUSED if isPaused else CTL_HELP)
          setPauseState(panels, isPaused, page)
        finally:
          panel.CURSES_LOCK.release()
      
      if quitConfirmed:
        # quits arm
        # very occasionally stderr gets "close failed: [Errno 11] Resource temporarily unavailable"
        # this appears to be a python bug: http://bugs.python.org/issue3014
        # (haven't seen this is quite some time... mysteriously resolved?)
        
        torTools.NO_SPAWN = True # prevents further worker threads from being spawned
        
        # stops panel daemons
        panels["header"].stop()
        panels["conn"].stop()
        panels["log"].stop()
        
        panels["header"].join()
        panels["conn"].join()
        panels["log"].join()
        
        conn = torTools.getConn()
        conn.close() # joins on TorCtl event thread
        
        # joins on utility daemon threads - this might take a moment since
        # the internal threadpools being joined might be sleeping
        resourceTrackers = sysTools.RESOURCE_TRACKERS.values()
        resolver = connections.getResolver("tor") if connections.isResolverAlive("tor") else None
        for tracker in resourceTrackers: tracker.stop()
        if resolver: resolver.stop()  # sets halt flag (returning immediately)
        hostnames.stop()              # halts and joins on hostname worker thread pool
        for tracker in resourceTrackers: tracker.join()
        if resolver: resolver.join()  # joins on halted resolver
        
        break
    elif key == curses.KEY_LEFT or key == curses.KEY_RIGHT:
      # switch page
      if key == curses.KEY_LEFT: page = (page - 1) % len(PAGES)
      else: page = (page + 1) % len(PAGES)
      
      # skip connections listing if it's disabled
      if page == 1 and isBlindMode:
        if key == curses.KEY_LEFT: page = (page - 1) % len(PAGES)
        else: page = (page + 1) % len(PAGES)
      
      # pauses panels that aren't visible to prevent events from accumilating
      # (otherwise they'll wait on the curses lock which might get demanding)
      setPauseState(panels, isPaused, page)
      
      # prevents panels on other pages from redrawing
      for i in range(len(PAGES)):
        isVisible = i == page
        for entry in PAGES[i]: panels[entry].setVisible(isVisible)
      
      PAGE = page
      
      panels["control"].page = page + 1
      
      # TODO: this redraw doesn't seem necessary (redraws anyway after this
      # loop) - look into this when refactoring
      panels["control"].redraw(True)
      
      selectiveRefresh(panels, page)
    elif key == ord('p') or key == ord('P'):
      # toggles update freezing
      panel.CURSES_LOCK.acquire()
      try:
        isPaused = not isPaused
        IS_PAUSED = isPaused
        setPauseState(panels, isPaused, page)
        panels["control"].setMsg(CTL_PAUSED if isPaused else CTL_HELP)
      finally:
        panel.CURSES_LOCK.release()
      
      selectiveRefresh(panels, page)
    elif key == ord('x') or key == ord('X'):
      # provides prompt to confirm that arm should issue a sighup
      panel.CURSES_LOCK.acquire()
      try:
        setPauseState(panels, isPaused, page, True)
        
        # provides prompt
        panels["control"].setMsg("This will reset Tor's internal state. Are you sure (x again to confirm)?", curses.A_BOLD)
        panels["control"].redraw(True)
        
        curses.cbreak()
        confirmationKey = stdscr.getch()
        if confirmationKey in (ord('x'), ord('X')):
          try:
            torTools.getConn().reload()
          except IOError, exc:
            log.log(log.ERR, "Error detected when reloading tor: %s" % sysTools.getFileErrorMsg(exc))
            
            #errorMsg = " (%s)" % str(err) if str(err) else ""
            #panels["control"].setMsg("Sighup failed%s" % errorMsg, curses.A_STANDOUT)
            #panels["control"].redraw(True)
            #time.sleep(2)
        
        # reverts display settings
        curses.halfdelay(REFRESH_RATE * 10)
        panels["control"].setMsg(CTL_PAUSED if isPaused else CTL_HELP)
        setPauseState(panels, isPaused, page)
      finally:
        panel.CURSES_LOCK.release()
    elif key == ord('h') or key == ord('H'):
      overrideKey = popups.showHelpPopup()
    elif page == 0 and (key == ord('b') or key == ord('B')):
      # uses the next boundary type for graph
      panels["graph"].bounds = graphing.graphPanel.Bounds.next(panels["graph"].bounds)
      
      selectiveRefresh(panels, page)
    elif page == 0 and (key == ord('a') or key == ord('A')):
      # allow user to enter a path to take a snapshot - abandons if left blank
      panel.CURSES_LOCK.acquire()
      try:
        setPauseState(panels, isPaused, page, True)
        
        # provides prompt
        panels["control"].setMsg("Path to save log snapshot: ")
        panels["control"].redraw(True)
        
        # gets user input (this blocks monitor updates)
        pathInput = panels["control"].getstr(0, 27)
        
        if pathInput:
          try:
            panels["log"].saveSnapshot(pathInput)
            panels["control"].setMsg("Saved: %s" % pathInput, curses.A_STANDOUT)
            panels["control"].redraw(True)
            time.sleep(2)
          except IOError, exc:
            panels["control"].setMsg("Unable to save snapshot: %s" % sysTools.getFileErrorMsg(exc), curses.A_STANDOUT)
            panels["control"].redraw(True)
            time.sleep(2)
        
        panels["control"].setMsg(CTL_PAUSED if isPaused else CTL_HELP)
        setPauseState(panels, isPaused, page)
      finally:
        panel.CURSES_LOCK.release()
      
      panels["graph"].redraw(True)
    elif page == 0 and (key == ord('e') or key == ord('E')):
      # allow user to enter new types of events to log - unchanged if left blank
      panel.CURSES_LOCK.acquire()
      try:
        setPauseState(panels, isPaused, page, True)
        
        # provides prompt
        panels["control"].setMsg("Events to log: ")
        panels["control"].redraw(True)
        
        # lists event types
        popup = panels["popup"]
        popup.height = 11
        popup.recreate(stdscr, 80)
        
        popup.clear()
        popup.win.box()
        popup.addstr(0, 0, "Event Types:", curses.A_STANDOUT)
        lineNum = 1
        for line in logPanel.EVENT_LISTING.split("\n"):
          line = line[6:]
          popup.addstr(lineNum, 1, line)
          lineNum += 1
        popup.refresh()
        
        # gets user input (this blocks monitor updates)
        eventsInput = panels["control"].getstr(0, 15)
        if eventsInput: eventsInput = eventsInput.replace(' ', '') # strips spaces
        
        # it would be nice to quit on esc, but looks like this might not be possible...
        if eventsInput:
          try:
            expandedEvents = logPanel.expandEvents(eventsInput)
            loggedEvents = setEventListening(expandedEvents, isBlindMode)
            panels["log"].setLoggedEvents(loggedEvents)
          except ValueError, exc:
            panels["control"].setMsg("Invalid flags: %s" % str(exc), curses.A_STANDOUT)
            panels["control"].redraw(True)
            time.sleep(2)
        
        # reverts popup dimensions
        popup.height = 9
        popup.recreate(stdscr, 80)
        
        panels["control"].setMsg(CTL_PAUSED if isPaused else CTL_HELP)
        setPauseState(panels, isPaused, page)
      finally:
        panel.CURSES_LOCK.release()
      
      panels["graph"].redraw(True)
    else:
      for pagePanel in getPanels(page + 1):
        isKeystrokeConsumed = pagePanel.handleKey(key)
        if isKeystrokeConsumed: break
    
    if REFRESH_FLAG:
      REFRESH_FLAG = False
      selectiveRefresh(panels, page)

def startTorMonitor(startTime, loggedEvents, isBlindMode):
  try:
    curses.wrapper(drawTorMonitor, startTime, loggedEvents, isBlindMode)
  except KeyboardInterrupt:
    pass # skip printing stack trace in case of keyboard interrupt

