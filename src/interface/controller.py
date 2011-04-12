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

import headerPanel
import graphing.graphPanel
import logPanel
import connPanel
import configPanel
import torrcPanel
import descriptorPopup
import fileDescriptorPopup

import interface.connections.connPanel
import interface.connections.connEntry
import interface.connections.entries
from util import conf, log, connections, hostnames, panel, sysTools, torConfig, torTools, uiTools
import graphing.bandwidthStats
import graphing.connStats
import graphing.resourceStats

CONFIRM_QUIT = True
REFRESH_RATE = 5        # seconds between redrawing screen
MAX_REGEX_FILTERS = 5   # maximum number of previous regex filters that'll be remembered

# enums for message in control label
CTL_HELP, CTL_PAUSED = range(2)

# panel order per page
PAGE_S = ["header", "control", "popup"] # sticky (ie, always available) page
PAGES = [
  ["graph", "log"],
  ["conn"],
  ["conn2"],
  ["config"],
  ["torrc"]]

PAUSEABLE = ["header", "graph", "log", "conn", "conn2"]

CONFIG = {"log.torrc.readFailed": log.WARN,
          "features.graph.type": 1,
          "features.config.prepopulateEditValues": True,
          "features.connection.oldPanel": False,
          "features.connection.newPanel": True,
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
        
        if not CONFIG["features.connection.newPanel"]:
          if currentPage >= 3: currentPage -= 1
          pageCount -= 1
        
        if self.isBlindMode or not CONFIG["features.connection.oldPanel"]:
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
  
  for key in PAUSEABLE: panels[key].setPaused(overwrite or monitorIsPaused or (key not in PAGES[currentPage] and key not in PAGE_S))

def showMenu(stdscr, popup, title, options, initialSelection):
  """
  Provides menu with options laid out in a single column. User can cancel
  selection with the escape key, in which case this proives -1. Otherwise this
  returns the index of the selection. If initialSelection is -1 then the first
  option is used and the carrot indicating past selection is ommitted.
  """
  
  selection = initialSelection if initialSelection != -1 else 0
  
  if popup.win:
    if not panel.CURSES_LOCK.acquire(False): return -1
    try:
      # TODO: should pause interface (to avoid event accumilation)
      curses.cbreak() # wait indefinitely for key presses (no timeout)
      
      # uses smaller dimentions more fitting for small content
      popup.height = len(options) + 2
      
      newWidth = max([len(label) for label in options]) + 9
      popup.recreate(stdscr, newWidth)
      
      key = 0
      while not uiTools.isSelectionKey(key):
        popup.clear()
        popup.win.box()
        popup.addstr(0, 0, title, curses.A_STANDOUT)
        
        for i in range(len(options)):
          label = options[i]
          format = curses.A_STANDOUT if i == selection else curses.A_NORMAL
          tab = "> " if i == initialSelection else "  "
          popup.addstr(i + 1, 2, tab)
          popup.addstr(i + 1, 4, " %s " % label, format)
        
        popup.refresh()
        key = stdscr.getch()
        if key == curses.KEY_UP: selection = max(0, selection - 1)
        elif key == curses.KEY_DOWN: selection = min(len(options) - 1, selection + 1)
        elif key == 27: selection, key = -1, curses.KEY_ENTER # esc - cancel
      
      # reverts popup dimensions and conn panel label
      popup.height = 9
      popup.recreate(stdscr, 80)
      
      curses.halfdelay(REFRESH_RATE * 10) # reset normal pausing behavior
    finally:
      panel.CURSES_LOCK.release()
  
  return selection

def showSortDialog(stdscr, panels, isPaused, page, titleLabel, options, oldSelection, optionColors):
  """
  Displays a sorting dialog of the form:
  
  Current Order: <previous selection>
  New Order: <selections made>
  
  <option 1>    <option 2>    <option 3>   Cancel
  
  Options are colored when among the "Current Order" or "New Order", but not
  when an option below them. If cancel is selected or the user presses escape
  then this returns None. Otherwise, the new ordering is provided.
  
  Arguments:
    stdscr, panels, isPaused, page - boiler plate arguments of the controller
        (should be refactored away when rewriting)
    
    titleLabel   - title displayed for the popup window
    options      - ordered listing of option labels
    oldSelection - current ordering
    optionColors - mappings of options to their color
  
  """
  
  panel.CURSES_LOCK.acquire()
  newSelections = []  # new ordering
  
  try:
    setPauseState(panels, isPaused, page, True)
    curses.cbreak() # wait indefinitely for key presses (no timeout)
    
    popup = panels["popup"]
    cursorLoc = 0       # index of highlighted option
    
    # label for the inital ordering
    formattedPrevListing = []
    for sortType in oldSelection:
      colorStr = optionColors.get(sortType, "white")
      formattedPrevListing.append("<%s>%s</%s>" % (colorStr, sortType, colorStr))
    prevOrderingLabel = "<b>Current Order: %s</b>" % ", ".join(formattedPrevListing)
    
    selectionOptions = list(options)
    selectionOptions.append("Cancel")
    
    while len(newSelections) < len(oldSelection):
      popup.clear()
      popup.win.box()
      popup.addstr(0, 0, titleLabel, curses.A_STANDOUT)
      popup.addfstr(1, 2, prevOrderingLabel)
      
      # provides new ordering
      formattedNewListing = []
      for sortType in newSelections:
        colorStr = optionColors.get(sortType, "white")
        formattedNewListing.append("<%s>%s</%s>" % (colorStr, sortType, colorStr))
      newOrderingLabel = "<b>New Order: %s</b>" % ", ".join(formattedNewListing)
      popup.addfstr(2, 2, newOrderingLabel)
      
      # presents remaining options, each row having up to four options with
      # spacing of nineteen cells
      row, col = 4, 0
      for i in range(len(selectionOptions)):
        popup.addstr(row, col * 19 + 2, selectionOptions[i], curses.A_STANDOUT if cursorLoc == i else curses.A_NORMAL)
        col += 1
        if col == 4: row, col = row + 1, 0
      
      popup.refresh()
      
      key = stdscr.getch()
      if key == curses.KEY_LEFT: cursorLoc = max(0, cursorLoc - 1)
      elif key == curses.KEY_RIGHT: cursorLoc = min(len(selectionOptions) - 1, cursorLoc + 1)
      elif key == curses.KEY_UP: cursorLoc = max(0, cursorLoc - 4)
      elif key == curses.KEY_DOWN: cursorLoc = min(len(selectionOptions) - 1, cursorLoc + 4)
      elif uiTools.isSelectionKey(key):
        # selected entry (the ord of '10' seems needed to pick up enter)
        selection = selectionOptions[cursorLoc]
        if selection == "Cancel": break
        else:
          newSelections.append(selection)
          selectionOptions.remove(selection)
          cursorLoc = min(cursorLoc, len(selectionOptions) - 1)
      elif key == 27: break # esc - cancel
      
    setPauseState(panels, isPaused, page)
    curses.halfdelay(REFRESH_RATE * 10) # reset normal pausing behavior
  finally:
    panel.CURSES_LOCK.release()
  
  if len(newSelections) == len(oldSelection):
    return newSelections
  else: return None

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
  
  # loads config for various interface components
  config = conf.getConfig("arm")
  config.update(CONFIG)
  graphing.graphPanel.loadConfig(config)
  interface.connections.connEntry.loadConfig(config)
  
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
    "header": headerPanel.HeaderPanel(stdscr, startTime),
    "popup": Popup(stdscr, 9),
    "graph": graphing.graphPanel.GraphPanel(stdscr),
    "log": logPanel.LogPanel(stdscr, loggedEvents, config)}
  
  # TODO: later it would be good to set the right 'top' values during initialization, 
  # but for now this is just necessary for the log panel (and a hack in the log...)
  
  # TODO: bug from not setting top is that the log panel might attempt to draw
  # before being positioned - the following is a quick hack til rewritten
  panels["log"].setPaused(True)
  
  if CONFIG["features.connection.oldPanel"]:
    panels["conn"] = connPanel.ConnPanel(stdscr, conn, isBlindMode)
  else:
    panels["conn"] = panel.Panel(stdscr, "blank", 0, 0, 0)
    PAUSEABLE.remove("conn")
  
  if CONFIG["features.connection.newPanel"]:
    panels["conn2"] = interface.connections.connPanel.ConnectionPanel(stdscr, config)
  else:
    panels["conn2"] = panel.Panel(stdscr, "blank", 0, 0, 0)
    PAUSEABLE.remove("conn2")
  
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
  if CONFIG["features.connection.oldPanel"]:
    conn.add_event_listener(panels["conn"])
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
  
  # tells revised panels to run as daemons
  panels["header"].start()
  panels["log"].start()
  if CONFIG["features.connection.newPanel"]:
    panels["conn2"].start()
  
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
  regexFilters = []             # previously used log regex filters
  panels["popup"].redraw(True)  # hack to make sure popup has a window instance (not entirely sure why...)
  
  # provides notice about any unused config keys
  for key in config.getUnusedKeys():
    log.log(CONFIG["log.configEntryUndefined"], "unused configuration entry: %s" % key)
  
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
        if CONFIG["features.connection.oldPanel"]:
          panels["conn"].resetOptions()
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
      
      if CONFIG["features.connection.oldPanel"]:
        panels["conn"].reset()
      
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
        if CONFIG["features.connection.newPanel"]: panels["conn2"].stop()
        panels["log"].stop()
        
        panels["header"].join()
        if CONFIG["features.connection.newPanel"]: panels["conn2"].join()
        panels["log"].join()
        
        # joins on utility daemon threads - this might take a moment since
        # the internal threadpools being joined might be sleeping
        conn = torTools.getConn()
        myPid = conn.getMyPid()
        
        resourceTracker = sysTools.getResourceTracker(myPid) if (myPid and sysTools.isTrackerAlive(myPid)) else None
        resolver = connections.getResolver("tor") if connections.isResolverAlive("tor") else None
        if resourceTracker: resourceTracker.stop()
        if resolver: resolver.stop()  # sets halt flag (returning immediately)
        hostnames.stop()              # halts and joins on hostname worker thread pool
        if resourceTracker: resourceTracker.join()
        if resolver: resolver.join()  # joins on halted resolver
        
        conn.close() # joins on TorCtl event thread
        break
    elif key == curses.KEY_LEFT or key == curses.KEY_RIGHT:
      # switch page
      if key == curses.KEY_LEFT: page = (page - 1) % len(PAGES)
      else: page = (page + 1) % len(PAGES)
      
      # skip connections listings if it's disabled
      while True:
        if page == 1 and (isBlindMode or not CONFIG["features.connection.oldPanel"]):
          if key == curses.KEY_LEFT: page = (page - 1) % len(PAGES)
          else: page = (page + 1) % len(PAGES)
        elif page == 2 and (isBlindMode or not CONFIG["features.connection.newPanel"]):
          if key == curses.KEY_LEFT: page = (page - 1) % len(PAGES)
          else: page = (page + 1) % len(PAGES)
        else: break
      
      # pauses panels that aren't visible to prevent events from accumilating
      # (otherwise they'll wait on the curses lock which might get demanding)
      setPauseState(panels, isPaused, page)
      
      # prevents panels on other pages from redrawing
      for i in range(len(PAGES)):
        isVisible = i == page
        for entry in PAGES[i]: panels[entry].setVisible(isVisible)
      
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
      # displays popup for current page's controls
      panel.CURSES_LOCK.acquire()
      try:
        setPauseState(panels, isPaused, page, True)
        
        # lists commands
        popup = panels["popup"]
        popup.clear()
        popup.win.box()
        popup.addstr(0, 0, "Page %i Commands:" % (page + 1), curses.A_STANDOUT)
        
        pageOverrideKeys = ()
        
        if page == 0:
          graphedStats = panels["graph"].currentDisplay
          if not graphedStats: graphedStats = "none"
          popup.addfstr(1, 2, "<b>up arrow</b>: scroll log up a line")
          popup.addfstr(1, 41, "<b>down arrow</b>: scroll log down a line")
          popup.addfstr(2, 2, "<b>m</b>: increase graph size")
          popup.addfstr(2, 41, "<b>n</b>: decrease graph size")
          popup.addfstr(3, 2, "<b>s</b>: graphed stats (<b>%s</b>)" % graphedStats)
          popup.addfstr(3, 41, "<b>i</b>: graph update interval (<b>%s</b>)" % graphing.graphPanel.UPDATE_INTERVALS[panels["graph"].updateInterval][0])
          popup.addfstr(4, 2, "<b>b</b>: graph bounds (<b>%s</b>)" % panels["graph"].bounds.lower())
          popup.addfstr(4, 41, "<b>d</b>: file descriptors")
          popup.addfstr(5, 2, "<b>e</b>: change logged events")
          
          regexLabel = "enabled" if panels["log"].regexFilter else "disabled"
          popup.addfstr(5, 41, "<b>f</b>: log regex filter (<b>%s</b>)" % regexLabel)
          
          hiddenEntryLabel = "visible" if panels["log"].showDuplicates else "hidden"
          popup.addfstr(6, 2, "<b>u</b>: duplicate log entries (<b>%s</b>)" % hiddenEntryLabel)
          popup.addfstr(6, 41, "<b>c</b>: clear event log")
          popup.addfstr(7, 41, "<b>a</b>: save snapshot of the log")
          
          pageOverrideKeys = (ord('m'), ord('n'), ord('s'), ord('i'), ord('d'), ord('e'), ord('r'), ord('f'), ord('x'))
        if page == 1:
          popup.addfstr(1, 2, "<b>up arrow</b>: scroll up a line")
          popup.addfstr(1, 41, "<b>down arrow</b>: scroll down a line")
          popup.addfstr(2, 2, "<b>page up</b>: scroll up a page")
          popup.addfstr(2, 41, "<b>page down</b>: scroll down a page")
          popup.addfstr(3, 2, "<b>enter</b>: connection details")
          popup.addfstr(3, 41, "<b>d</b>: raw consensus descriptor")
          
          listingType = connPanel.LIST_LABEL[panels["conn"].listingType].lower()
          popup.addfstr(4, 2, "<b>l</b>: listed identity (<b>%s</b>)" % listingType)
          
          resolverUtil = connections.getResolver("tor").overwriteResolver
          if resolverUtil == None: resolverUtil = "auto"
          popup.addfstr(4, 41, "<b>u</b>: resolving utility (<b>%s</b>)" % resolverUtil)
          
          if CONFIG["features.connection.oldPanel"]:
            allowDnsLabel = "allow" if panels["conn"].allowDNS else "disallow"
          else: allowDnsLabel = "disallow"
          popup.addfstr(5, 2, "<b>r</b>: permit DNS resolution (<b>%s</b>)" % allowDnsLabel)
          
          popup.addfstr(5, 41, "<b>s</b>: sort ordering")
          popup.addfstr(6, 2, "<b>c</b>: client circuits")
          
          #popup.addfstr(5, 41, "c: toggle cursor (<b>%s</b>)" % ("on" if panels["conn"].isCursorEnabled else "off"))
          
          pageOverrideKeys = (ord('d'), ord('l'), ord('s'), ord('c'))
        elif page == 2:
          popup.addfstr(1, 2, "<b>up arrow</b>: scroll up a line")
          popup.addfstr(1, 41, "<b>down arrow</b>: scroll down a line")
          popup.addfstr(2, 2, "<b>page up</b>: scroll up a page")
          popup.addfstr(2, 41, "<b>page down</b>: scroll down a page")
          
          popup.addfstr(3, 2, "<b>enter</b>: edit configuration option")
          popup.addfstr(3, 41, "<b>d</b>: raw consensus descriptor")
          
          listingType = panels["conn2"]._listingType.lower()
          popup.addfstr(4, 2, "<b>l</b>: listed identity (<b>%s</b>)" % listingType)
          
          popup.addfstr(4, 41, "<b>s</b>: sort ordering")
          
          resolverUtil = connections.getResolver("tor").overwriteResolver
          if resolverUtil == None: resolverUtil = "auto"
          popup.addfstr(5, 2, "<b>u</b>: resolving utility (<b>%s</b>)" % resolverUtil)
          
          pageOverrideKeys = (ord('d'), ord('l'), ord('s'), ord('u'))
        elif page == 3:
          popup.addfstr(1, 2, "<b>up arrow</b>: scroll up a line")
          popup.addfstr(1, 41, "<b>down arrow</b>: scroll down a line")
          popup.addfstr(2, 2, "<b>page up</b>: scroll up a page")
          popup.addfstr(2, 41, "<b>page down</b>: scroll down a page")
          
          strippingLabel = "on" if panels["torrc"].stripComments else "off"
          popup.addfstr(3, 2, "<b>s</b>: comment stripping (<b>%s</b>)" % strippingLabel)
          
          lineNumLabel = "on" if panels["torrc"].showLineNum else "off"
          popup.addfstr(3, 41, "<b>n</b>: line numbering (<b>%s</b>)" % lineNumLabel)
          
          popup.addfstr(4, 2, "<b>r</b>: reload torrc")
          popup.addfstr(4, 41, "<b>x</b>: reset tor (issue sighup)")
        elif page == 4:
          popup.addfstr(1, 2, "<b>up arrow</b>: scroll up a line")
          popup.addfstr(1, 41, "<b>down arrow</b>: scroll down a line")
          popup.addfstr(2, 2, "<b>page up</b>: scroll up a page")
          popup.addfstr(2, 41, "<b>page down</b>: scroll down a page")
          popup.addfstr(3, 2, "<b>enter</b>: connection details")
        
        popup.addstr(7, 2, "Press any key...")
        popup.refresh()
        
        # waits for user to hit a key, if it belongs to a command then executes it
        curses.cbreak()
        helpExitKey = stdscr.getch()
        if helpExitKey in pageOverrideKeys: overrideKey = helpExitKey
        curses.halfdelay(REFRESH_RATE * 10)
        
        setPauseState(panels, isPaused, page)
        selectiveRefresh(panels, page)
      finally:
        panel.CURSES_LOCK.release()
    elif page == 0 and (key == ord('s') or key == ord('S')):
      # provides menu to pick stats to be graphed
      #options = ["None"] + [label for label in panels["graph"].stats.keys()]
      options = ["None"]
      
      # appends stats labels with first letters of each word capitalized
      initialSelection, i = -1, 1
      if not panels["graph"].currentDisplay: initialSelection = 0
      graphLabels = panels["graph"].stats.keys()
      graphLabels.sort()
      for label in graphLabels:
        if label == panels["graph"].currentDisplay: initialSelection = i
        words = label.split()
        options.append(" ".join(word[0].upper() + word[1:] for word in words))
        i += 1
      
      # hides top label of the graph panel and pauses panels
      if panels["graph"].currentDisplay:
        panels["graph"].showLabel = False
        panels["graph"].redraw(True)
      setPauseState(panels, isPaused, page, True)
      
      selection = showMenu(stdscr, panels["popup"], "Graphed Stats:", options, initialSelection)
      
      # reverts changes made for popup
      panels["graph"].showLabel = True
      setPauseState(panels, isPaused, page)
      
      # applies new setting
      if selection != -1 and selection != initialSelection:
        if selection == 0: panels["graph"].setStats(None)
        else: panels["graph"].setStats(options[selection].lower())
      
      selectiveRefresh(panels, page)
      
      # TODO: this shouldn't be necessary with the above refresh, but doesn't seem responsive otherwise...
      panels["graph"].redraw(True)
    elif page == 0 and (key == ord('i') or key == ord('I')):
      # provides menu to pick graph panel update interval
      options = [label for (label, intervalTime) in graphing.graphPanel.UPDATE_INTERVALS]
      
      initialSelection = panels["graph"].updateInterval
      
      #initialSelection = -1
      #for i in range(len(options)):
      #  if options[i] == panels["graph"].updateInterval: initialSelection = i
      
      # hides top label of the graph panel and pauses panels
      if panels["graph"].currentDisplay:
        panels["graph"].showLabel = False
        panels["graph"].redraw(True)
      setPauseState(panels, isPaused, page, True)
      
      selection = showMenu(stdscr, panels["popup"], "Update Interval:", options, initialSelection)
      
      # reverts changes made for popup
      panels["graph"].showLabel = True
      setPauseState(panels, isPaused, page)
      
      # applies new setting
      if selection != -1: panels["graph"].updateInterval = selection
      
      selectiveRefresh(panels, page)
    elif page == 0 and (key == ord('b') or key == ord('B')):
      # uses the next boundary type for graph
      panels["graph"].bounds = graphing.graphPanel.Bounds.next(panels["graph"].bounds)
      
      selectiveRefresh(panels, page)
    elif page == 0 and key in (ord('d'), ord('D')):
      # provides popup with file descriptors
      panel.CURSES_LOCK.acquire()
      try:
        setPauseState(panels, isPaused, page, True)
        curses.cbreak() # wait indefinitely for key presses (no timeout)
        
        fileDescriptorPopup.showFileDescriptorPopup(panels["popup"], stdscr, torPid)
        
        setPauseState(panels, isPaused, page)
        curses.halfdelay(REFRESH_RATE * 10) # reset normal pausing behavior
      finally:
        panel.CURSES_LOCK.release()
      
      panels["graph"].redraw(True)
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
    elif page == 0 and (key == ord('f') or key == ord('F')):
      # provides menu to pick previous regular expression filters or to add a new one
      # for syntax see: http://docs.python.org/library/re.html#regular-expression-syntax
      options = ["None"] + regexFilters + ["New..."]
      initialSelection = 0 if not panels["log"].regexFilter else 1
      
      # hides top label of the graph panel and pauses panels
      if panels["graph"].currentDisplay:
        panels["graph"].showLabel = False
        panels["graph"].redraw(True)
      setPauseState(panels, isPaused, page, True)
      
      selection = showMenu(stdscr, panels["popup"], "Log Filter:", options, initialSelection)
      
      # applies new setting
      if selection == 0:
        panels["log"].setFilter(None)
      elif selection == len(options) - 1:
        # selected 'New...' option - prompt user to input regular expression
        panel.CURSES_LOCK.acquire()
        try:
          # provides prompt
          panels["control"].setMsg("Regular expression: ")
          panels["control"].redraw(True)
          
          # gets user input (this blocks monitor updates)
          regexInput = panels["control"].getstr(0, 20)
          
          if regexInput:
            try:
              panels["log"].setFilter(re.compile(regexInput))
              if regexInput in regexFilters: regexFilters.remove(regexInput)
              regexFilters = [regexInput] + regexFilters
            except re.error, exc:
              panels["control"].setMsg("Unable to compile expression: %s" % str(exc), curses.A_STANDOUT)
              panels["control"].redraw(True)
              time.sleep(2)
          panels["control"].setMsg(CTL_PAUSED if isPaused else CTL_HELP)
        finally:
          panel.CURSES_LOCK.release()
      elif selection != -1:
        try:
          panels["log"].setFilter(re.compile(regexFilters[selection - 1]))
          
          # move selection to top
          regexFilters = [regexFilters[selection - 1]] + regexFilters
          del regexFilters[selection]
        except re.error, exc:
          # shouldn't happen since we've already checked validity
          log.log(log.WARN, "Invalid regular expression ('%s': %s) - removing from listing" % (regexFilters[selection - 1], str(exc)))
          del regexFilters[selection - 1]
      
      if len(regexFilters) > MAX_REGEX_FILTERS: del regexFilters[MAX_REGEX_FILTERS:]
      
      # reverts changes made for popup
      panels["graph"].showLabel = True
      setPauseState(panels, isPaused, page)
      panels["graph"].redraw(True)
    elif page == 0 and key in (ord('n'), ord('N'), ord('m'), ord('M')):
      # Unfortunately modifier keys don't work with the up/down arrows (sending
      # multiple keycodes. The only exception to this is shift + left/right,
      # but for now just gonna use standard characters.
      
      if key in (ord('n'), ord('N')):
        panels["graph"].setGraphHeight(panels["graph"].graphHeight - 1)
      else:
        # don't grow the graph if it's already consuming the whole display
        # (plus an extra line for the graph/log gap)
        maxHeight = panels["graph"].parent.getmaxyx()[0] - panels["graph"].top
        currentHeight = panels["graph"].getHeight()
        
        if currentHeight < maxHeight + 1:
          panels["graph"].setGraphHeight(panels["graph"].graphHeight + 1)
    elif page == 0 and (key == ord('c') or key == ord('C')):
      # provides prompt to confirm that arm should clear the log
      panel.CURSES_LOCK.acquire()
      try:
        setPauseState(panels, isPaused, page, True)
        
        # provides prompt
        panels["control"].setMsg("This will clear the log. Are you sure (c again to confirm)?", curses.A_BOLD)
        panels["control"].redraw(True)
        
        curses.cbreak()
        confirmationKey = stdscr.getch()
        if confirmationKey in (ord('c'), ord('C')): panels["log"].clear()
        
        # reverts display settings
        curses.halfdelay(REFRESH_RATE * 10)
        panels["control"].setMsg(CTL_PAUSED if isPaused else CTL_HELP)
        setPauseState(panels, isPaused, page)
      finally:
        panel.CURSES_LOCK.release()
    elif CONFIG["features.connection.oldPanel"] and key == 27 and panels["conn"].listingType == connPanel.LIST_HOSTNAME and panels["control"].resolvingCounter != -1:
      # canceling hostname resolution (esc on any page)
      panels["conn"].listingType = connPanel.LIST_IP
      panels["control"].resolvingCounter = -1
      hostnames.setPaused(True)
      panels["conn"].sortConnections()
    elif page == 1 and panels["conn"].isCursorEnabled and uiTools.isSelectionKey(key):
      # TODO: deprecated when migrated to the new connection panel, thought as
      # well keep around until there's a counterpart for hostname fetching
      
      # provides details on selected connection
      panel.CURSES_LOCK.acquire()
      try:
        setPauseState(panels, isPaused, page, True)
        popup = panels["popup"]
        
        # reconfigures connection panel to accomidate details dialog
        panels["conn"].showLabel = False
        panels["conn"].showingDetails = True
        panels["conn"].redraw(True)
        
        hostnames.setPaused(not panels["conn"].allowDNS)
        relayLookupCache = {} # temporary cache of entry -> (ns data, desc data)
        
        curses.cbreak() # wait indefinitely for key presses (no timeout)
        key = 0
        
        while not uiTools.isSelectionKey(key):
          popup.clear()
          popup.win.box()
          popup.addstr(0, 0, "Connection Details:", curses.A_STANDOUT)
          
          selection = panels["conn"].cursorSelection
          if not selection or not panels["conn"].connections: break
          selectionColor = connPanel.TYPE_COLORS[selection[connPanel.CONN_TYPE]]
          format = uiTools.getColor(selectionColor) | curses.A_BOLD
          
          selectedIp = selection[connPanel.CONN_F_IP]
          selectedPort = selection[connPanel.CONN_F_PORT]
          selectedIsPrivate = selection[connPanel.CONN_PRIVATE]
          
          addrLabel = "address: %s:%s" % (selectedIp, selectedPort)
          
          if selection[connPanel.CONN_TYPE] == "family" and int(selection[connPanel.CONN_L_PORT]) > 65535:
            # unresolved family entry - unknown ip/port
            addrLabel = "address: unknown"
          
          if selectedIsPrivate: hostname = None
          else:
            try: hostname = hostnames.resolve(selectedIp)
            except ValueError: hostname = "unknown" # hostname couldn't be resolved
          
          if hostname == None:
            if hostnames.isPaused() or selectedIsPrivate: hostname = "DNS resolution disallowed"
            else:
              # if hostname is still being resolved refresh panel every half-second until it's completed
              curses.halfdelay(5)
              hostname = "resolving..."
          elif len(hostname) > 73 - len(addrLabel):
            # hostname too long - truncate
            hostname = "%s..." % hostname[:70 - len(addrLabel)]
          
          if selectedIsPrivate:
            popup.addstr(1, 2, "address: <scrubbed> (unknown)", format)
            popup.addstr(2, 2, "locale: ??", format)
            popup.addstr(3, 2, "No consensus data found", format)
          else:
            popup.addstr(1, 2, "%s (%s)" % (addrLabel, hostname), format)
            
            locale = selection[connPanel.CONN_COUNTRY]
            popup.addstr(2, 2, "locale: %s" % locale, format)
            
            # provides consensus data for selection (needs fingerprint to get anywhere...)
            fingerprint = panels["conn"].getFingerprint(selectedIp, selectedPort)
            
            if fingerprint == "UNKNOWN":
              if selectedIp not in panels["conn"].fingerprintMappings.keys():
                # no consensus entry for this ip address
                popup.addstr(3, 2, "No consensus data found", format)
              else:
                # couldn't resolve due to multiple matches - list them all
                popup.addstr(3, 2, "Muliple matches, possible fingerprints are:", format)
                matchings = panels["conn"].fingerprintMappings[selectedIp]
                
                line = 4
                for (matchPort, matchFingerprint, matchNickname) in matchings:
                  popup.addstr(line, 2, "%i. or port: %-5s fingerprint: %s" % (line - 3, matchPort, matchFingerprint), format)
                  line += 1
                  
                  if line == 7 and len(matchings) > 4:
                    popup.addstr(8, 2, "... %i more" % len(matchings) - 3, format)
                    break
            else:
              # fingerprint found - retrieve related data
              lookupErrored = False
              if selection in relayLookupCache.keys(): nsEntry, descEntry = relayLookupCache[selection]
              else:
                try:
                  nsCall = conn.get_network_status("id/%s" % fingerprint)
                  if len(nsCall) == 0: raise TorCtl.ErrorReply() # no results provided
                except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed):
                  # ns lookup fails or provides empty results - can happen with
                  # localhost lookups if relay's having problems (orport not
                  # reachable) and this will be empty if network consensus
                  # couldn't be fetched
                  lookupErrored = True
                
                if not lookupErrored and nsCall:
                  if len(nsCall) > 1:
                    # multiple records for fingerprint (shouldn't happen)
                    log.log(log.WARN, "Multiple consensus entries for fingerprint: %s" % fingerprint)
                  
                  nsEntry = nsCall[0]
                  
                  try:
                    descLookupCmd = "desc/id/%s" % fingerprint
                    descEntry = TorCtl.Router.build_from_desc(conn.get_info(descLookupCmd)[descLookupCmd].split("\n"), nsEntry)
                    relayLookupCache[selection] = (nsEntry, descEntry)
                  except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed): lookupErrored = True # desc lookup failed
              
              if lookupErrored:
                popup.addstr(3, 2, "Unable to retrieve consensus data", format)
              else:
                popup.addstr(2, 15, "fingerprint: %s" % fingerprint, format)
                
                nickname = panels["conn"].getNickname(selectedIp, selectedPort)
                dirPortLabel = "dirport: %i" % nsEntry.dirport if nsEntry.dirport else ""
                popup.addstr(3, 2, "nickname: %-25s orport: %-10i %s" % (nickname, nsEntry.orport, dirPortLabel), format)
                
                popup.addstr(4, 2, "published: %-24s os: %-14s version: %s" % (descEntry.published, descEntry.os, descEntry.version), format)
                popup.addstr(5, 2, "flags: %s" % ", ".join(nsEntry.flags), format)
                
                exitLine = ", ".join([str(k) for k in descEntry.exitpolicy])
                if len(exitLine) > 63: exitLine = "%s..." % exitLine[:60]
                popup.addstr(6, 2, "exit policy: %s" % exitLine, format)
                
                if descEntry.contact:
                  # clears up some common obscuring
                  contactAddr = descEntry.contact
                  obscuring = [(" at ", "@"), (" AT ", "@"), ("AT", "@"), (" dot ", "."), (" DOT ", ".")]
                  for match, replace in obscuring: contactAddr = contactAddr.replace(match, replace)
                  if len(contactAddr) > 67: contactAddr = "%s..." % contactAddr[:64]
                  popup.addstr(7, 2, "contact: %s" % contactAddr, format)
            
          popup.refresh()
          key = stdscr.getch()
          
          if key == curses.KEY_RIGHT: key = curses.KEY_DOWN
          elif key == curses.KEY_LEFT: key = curses.KEY_UP
          
          if key in (curses.KEY_DOWN, curses.KEY_UP, curses.KEY_PPAGE, curses.KEY_NPAGE):
            panels["conn"].handleKey(key)
          elif key in (ord('d'), ord('D')):
            descriptorPopup.showDescriptorPopup(panels["popup"], stdscr, panels["conn"])
            panels["conn"].redraw(True)
        
        panels["conn"].showLabel = True
        panels["conn"].showingDetails = False
        hostnames.setPaused(not panels["conn"].allowDNS and panels["conn"].listingType == connPanel.LIST_HOSTNAME)
        setPauseState(panels, isPaused, page)
        curses.halfdelay(REFRESH_RATE * 10) # reset normal pausing behavior
      finally:
        panel.CURSES_LOCK.release()
    elif page == 1 and panels["conn"].isCursorEnabled and key in (ord('d'), ord('D')):
      # presents popup for raw consensus data
      panel.CURSES_LOCK.acquire()
      try:
        setPauseState(panels, isPaused, page, True)
        curses.cbreak() # wait indefinitely for key presses (no timeout)
        panels["conn"].showLabel = False
        panels["conn"].redraw(True)
        
        descriptorPopup.showDescriptorPopup(panels["popup"], stdscr, panels["conn"])
        
        setPauseState(panels, isPaused, page)
        curses.halfdelay(REFRESH_RATE * 10) # reset normal pausing behavior
        panels["conn"].showLabel = True
      finally:
        panel.CURSES_LOCK.release()
    elif page == 1 and (key == ord('l') or key == ord('L')):
      # provides menu to pick identification info listed for connections
      optionTypes = [connPanel.LIST_IP, connPanel.LIST_HOSTNAME, connPanel.LIST_FINGERPRINT, connPanel.LIST_NICKNAME]
      options = [connPanel.LIST_LABEL[sortType] for sortType in optionTypes]
      initialSelection = panels["conn"].listingType   # enums correspond to index
      
      # hides top label of conn panel and pauses panels
      panels["conn"].showLabel = False
      panels["conn"].redraw(True)
      setPauseState(panels, isPaused, page, True)
      
      selection = showMenu(stdscr, panels["popup"], "List By:", options, initialSelection)
      
      # reverts changes made for popup
      panels["conn"].showLabel = True
      setPauseState(panels, isPaused, page)
      
      # applies new setting
      if selection != -1 and optionTypes[selection] != panels["conn"].listingType:
        panels["conn"].listingType = optionTypes[selection]
        
        if panels["conn"].listingType == connPanel.LIST_HOSTNAME:
          curses.halfdelay(10) # refreshes display every second until done resolving
          panels["control"].resolvingCounter = hostnames.getRequestCount() - hostnames.getPendingCount()
          
          hostnames.setPaused(not panels["conn"].allowDNS)
          for connEntry in panels["conn"].connections:
            try: hostnames.resolve(connEntry[connPanel.CONN_F_IP])
            except ValueError: pass
        else:
          panels["control"].resolvingCounter = -1
          hostnames.setPaused(True)
        
        panels["conn"].sortConnections()
    elif page in (1, 2) and (key == ord('u') or key == ord('U')):
      # provides menu to pick identification resolving utility
      options = ["auto"] + connections.Resolver.values()
      
      currentOverwrite = connections.getResolver("tor").overwriteResolver # enums correspond to indices
      if currentOverwrite == None: initialSelection = 0
      else: initialSelection = options.index(currentOverwrite)
      
      # hides top label of conn panel and pauses panels
      panels["conn"].showLabel = False
      panels["conn"].redraw(True)
      setPauseState(panels, isPaused, page, True)
      
      selection = showMenu(stdscr, panels["popup"], "Resolver Util:", options, initialSelection)
      selectedOption = options[selection] if selection != "auto" else None
      
      # reverts changes made for popup
      panels["conn"].showLabel = True
      setPauseState(panels, isPaused, page)
      
      # applies new setting
      if selection != -1 and selectedOption != connections.getResolver("tor").overwriteResolver:
        connections.getResolver("tor").overwriteResolver = selectedOption
    elif page == 1 and (key == ord('s') or key == ord('S')):
      # set ordering for connection listing
      titleLabel = "Connection Ordering:"
      options = [connPanel.getSortLabel(i) for i in range(9)]
      oldSelection = [connPanel.getSortLabel(entry) for entry in panels["conn"].sortOrdering]
      optionColors = dict([connPanel.getSortLabel(i, True) for i in range(9)])
      results = showSortDialog(stdscr, panels, isPaused, page, titleLabel, options, oldSelection, optionColors)
      
      if results:
        # converts labels back to enums
        resultEnums = [connPanel.getSortType(entry) for entry in results]
        panels["conn"].sortOrdering = resultEnums
        panels["conn"].sortConnections()
      
      # TODO: not necessary until the connection panel rewrite
      #panels["conn"].redraw(True)
    elif page == 1 and (key == ord('c') or key == ord('C')):
      # displays popup with client circuits
      clientCircuits = None
      try:
        clientCircuits = conn.get_info("circuit-status")["circuit-status"].split("\n")
      except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed): pass
      
      maxEntryLength = 0
      if clientCircuits:
        for clientEntry in clientCircuits: maxEntryLength = max(len(clientEntry), maxEntryLength)
      
      panel.CURSES_LOCK.acquire()
      try:
        setPauseState(panels, isPaused, page, True)
        
        # makes sure there's room for the longest entry
        popup = panels["popup"]
        if clientCircuits and maxEntryLength + 4 > popup.getPreferredSize()[1]:
          popup.height = max(popup.height, len(clientCircuits) + 3)
          popup.recreate(stdscr, maxEntryLength + 4)
        
        # lists commands
        popup.clear()
        popup.win.box()
        popup.addstr(0, 0, "Client Circuits:", curses.A_STANDOUT)
        
        if clientCircuits == None:
          popup.addstr(1, 2, "Unable to retireve current circuits")
        elif len(clientCircuits) == 1 and clientCircuits[0] == "":
          popup.addstr(1, 2, "No active client circuits")
        else:
          line = 1
          for clientEntry in clientCircuits:
            popup.addstr(line, 2, clientEntry)
            line += 1
            
        popup.addstr(popup.height - 2, 2, "Press any key...")
        popup.refresh()
        
        curses.cbreak()
        stdscr.getch()
        curses.halfdelay(REFRESH_RATE * 10)
        
        # reverts popup dimensions
        popup.height = 9
        popup.recreate(stdscr, 80)
        
        setPauseState(panels, isPaused, page)
      finally:
        panel.CURSES_LOCK.release()
    elif page == 2 and key in (ord('d'), ord('D')):
      # presents popup for raw consensus data
      panel.CURSES_LOCK.acquire()
      try:
        setPauseState(panels, isPaused, page, True)
        curses.cbreak() # wait indefinitely for key presses (no timeout)
        panelTitle = panels["conn2"]._title
        panels["conn2"]._title = ""
        panels["conn2"].redraw(True)
        
        descriptorPopup.showDescriptorPopup(panels["popup"], stdscr, panels["conn2"], True)
        
        panels["conn2"]._title = panelTitle
        setPauseState(panels, isPaused, page)
        curses.halfdelay(REFRESH_RATE * 10) # reset normal pausing behavior
      finally:
        panel.CURSES_LOCK.release()
    elif page == 2 and (key == ord('l') or key == ord('L')):
      # provides a menu to pick the primary information we list connections by
      options = interface.connections.entries.ListingType.values()
      
      # dropping the HOSTNAME listing type until we support displaying that content
      options.remove(interface.connections.entries.ListingType.HOSTNAME)
      
      initialSelection = options.index(panels["conn2"]._listingType)
      
      # hides top label of connection panel and pauses the display
      panelTitle = panels["conn2"]._title
      panels["conn2"]._title = ""
      panels["conn2"].redraw(True)
      setPauseState(panels, isPaused, page, True)
      
      selection = showMenu(stdscr, panels["popup"], "List By:", options, initialSelection)
      
      # reverts changes made for popup
      panels["conn2"]._title = panelTitle
      setPauseState(panels, isPaused, page)
      
      # applies new setting
      if selection != -1 and options[selection] != panels["conn2"]._listingType:
        panels["conn2"].setListingType(options[selection])
        panels["conn2"].redraw(True)
    elif page == 2 and (key == ord('s') or key == ord('S')):
      # set ordering for connection options
      titleLabel = "Connection Ordering:"
      options = interface.connections.entries.SortAttr.values()
      oldSelection = panels["conn2"]._sortOrdering
      optionColors = dict([(attr, interface.connections.entries.SORT_COLORS[attr]) for attr in options])
      results = showSortDialog(stdscr, panels, isPaused, page, titleLabel, options, oldSelection, optionColors)
      
      if results:
        panels["conn2"].setSortOrder(results)
      
      panels["conn2"].redraw(True)
    elif page == 3 and (key == ord('c') or key == ord('C')) and False:
      # TODO: disabled for now (probably gonna be going with separate pages
      # rather than popup menu)
      # provides menu to pick config being displayed
      #options = [confPanel.CONFIG_LABELS[confType] for confType in range(4)]
      options = []
      initialSelection = panels["torrc"].configType
      
      # hides top label of the graph panel and pauses panels
      panels["torrc"].showLabel = False
      panels["torrc"].redraw(True)
      setPauseState(panels, isPaused, page, True)
      
      selection = showMenu(stdscr, panels["popup"], "Configuration:", options, initialSelection)
      
      # reverts changes made for popup
      panels["torrc"].showLabel = True
      setPauseState(panels, isPaused, page)
      
      # applies new setting
      if selection != -1: panels["torrc"].setConfigType(selection)
      
      selectiveRefresh(panels, page)
    elif page == 3 and (key == ord('w') or key == ord('W')):
      # display a popup for saving the current configuration
      panel.CURSES_LOCK.acquire()
      try:
        configLines = torConfig.getCustomOptions(True)
        
        # lists event types
        popup = panels["popup"]
        popup.height = len(configLines) + 3
        popup.recreate(stdscr)
        displayHeight, displayWidth = panels["popup"].getPreferredSize()
        
        # displayed options (truncating the labels if there's limited room)
        if displayWidth >= 30: selectionOptions = ("Save", "Save As...", "Cancel")
        else: selectionOptions = ("Save", "Save As", "X")
        
        # checks if we can show options beside the last line of visible content
        lastIndex = min(displayHeight - 3, len(configLines) - 1)
        isOptionLineSeparate = displayWidth < (30 + len(configLines[lastIndex]))
        
        # if we're showing all the content and have room to display selection
        # options besides the text then shrink the popup by a row
        if not isOptionLineSeparate and displayHeight == len(configLines) + 3:
          popup.height -= 1
          popup.recreate(stdscr)
        
        key, selection = 0, 2
        while not uiTools.isSelectionKey(key):
          # if the popup has been resized then recreate it (needed for the
          # proper border height)
          newHeight, newWidth = panels["popup"].getPreferredSize()
          if (displayHeight, displayWidth) != (newHeight, newWidth):
            displayHeight, displayWidth = newHeight, newWidth
            popup.recreate(stdscr)
          
          # if there isn't room to display the popup then cancel it
          if displayHeight <= 2:
            selection = 2
            break
          
          popup.clear()
          popup.win.box()
          popup.addstr(0, 0, "Configuration being saved:", curses.A_STANDOUT)
          
          visibleConfigLines = displayHeight - 3 if isOptionLineSeparate else displayHeight - 2
          for i in range(visibleConfigLines):
            line = uiTools.cropStr(configLines[i], displayWidth - 2)
            
            if " " in line:
              option, arg = line.split(" ", 1)
              popup.addstr(i + 1, 1, option, curses.A_BOLD | uiTools.getColor("green"))
              popup.addstr(i + 1, len(option) + 2, arg, curses.A_BOLD | uiTools.getColor("cyan"))
            else:
              popup.addstr(i + 1, 1, line, curses.A_BOLD | uiTools.getColor("green"))
          
          # draws 'T' between the lower left and the covered panel's scroll bar
          if displayWidth > 1: popup.win.addch(displayHeight - 1, 1, curses.ACS_TTEE)
          
          # draws selection options (drawn right to left)
          drawX = displayWidth - 1
          for i in range(len(selectionOptions) - 1, -1, -1):
            optionLabel = selectionOptions[i]
            drawX -= (len(optionLabel) + 2)
            
            # if we've run out of room then drop the option (this will only
            # occure on tiny displays)
            if drawX < 1: break
            
            selectionFormat = curses.A_STANDOUT if i == selection else curses.A_NORMAL
            popup.addstr(displayHeight - 2, drawX, "[")
            popup.addstr(displayHeight - 2, drawX + 1, optionLabel, selectionFormat | curses.A_BOLD)
            popup.addstr(displayHeight - 2, drawX + len(optionLabel) + 1, "]")
            
            drawX -= 1 # space gap between the options
          
          popup.refresh()
          
          key = stdscr.getch()
          if key == curses.KEY_LEFT: selection = max(0, selection - 1)
          elif key == curses.KEY_RIGHT: selection = min(len(selectionOptions) - 1, selection + 1)
        
        if selection in (0, 1):
          loadedTorrc = torConfig.getTorrc()
          try: configLocation = loadedTorrc.getConfigLocation()
          except IOError: configLocation = ""
          
          if selection == 1:
            # prompts user for a configuration location
            promptMsg = "Save to (esc to cancel): "
            panels["control"].setMsg(promptMsg)
            panels["control"].redraw(True)
            configLocation = panels["control"].getstr(0, len(promptMsg), configLocation)
            if configLocation: configLocation = os.path.abspath(configLocation)
          
          if configLocation:
            try:
              # make dir if the path doesn't already exist
              baseDir = os.path.dirname(configLocation)
              if not os.path.exists(baseDir): os.makedirs(baseDir)
              
              # saves the configuration to the file
              configFile = open(configLocation, "w")
              configFile.write("\n".join(configLines))
              configFile.close()
              
              # reloads the cached torrc if overwriting it
              if configLocation == loadedTorrc.getConfigLocation():
                try:
                  loadedTorrc.load()
                  panels["torrc"]._lastContentHeightArgs = None
                except IOError: pass
              
              msg = "Saved configuration to %s" % configLocation
            except (IOError, OSError), exc:
              msg = "Unable to save configuration (%s)" % sysTools.getFileErrorMsg(exc)
            
            panels["control"].setMsg(msg, curses.A_STANDOUT)
            panels["control"].redraw(True)
            time.sleep(2)
          
          panels["control"].setMsg(CTL_PAUSED if isPaused else CTL_HELP)
        
        # reverts popup dimensions
        popup.height = 9
        popup.recreate(stdscr, 80)
      finally:
        panel.CURSES_LOCK.release()
      
      panels["config"].redraw(True)
    elif page == 3 and (key == ord('s') or key == ord('S')):
      # set ordering for config options
      titleLabel = "Config Option Ordering:"
      options = [configPanel.FIELD_ATTR[field][0] for field in configPanel.Field.values()]
      oldSelection = [configPanel.FIELD_ATTR[field][0] for field in panels["config"].sortOrdering]
      optionColors = dict([configPanel.FIELD_ATTR[field] for field in configPanel.Field.values()])
      results = showSortDialog(stdscr, panels, isPaused, page, titleLabel, options, oldSelection, optionColors)
      
      if results:
        # converts labels back to enums
        resultEnums = []
        
        for label in results:
          for entryEnum in configPanel.FIELD_ATTR:
            if label == configPanel.FIELD_ATTR[entryEnum][0]:
              resultEnums.append(entryEnum)
              break
        
        panels["config"].setSortOrder(resultEnums)
      
      panels["config"].redraw(True)
    elif page == 3 and uiTools.isSelectionKey(key):
      # let the user edit the configuration value, unchanged if left blank
      panel.CURSES_LOCK.acquire()
      try:
        setPauseState(panels, isPaused, page, True)
        
        # provides prompt
        selection = panels["config"].getSelection()
        configOption = selection.get(configPanel.Field.OPTION)
        titleMsg = "%s Value (esc to cancel): " % configOption
        panels["control"].setMsg(titleMsg)
        panels["control"].redraw(True)
        
        displayWidth = panels["control"].getPreferredSize()[1]
        initialValue = selection.get(configPanel.Field.VALUE)
        
        # initial input for the text field
        initialText = ""
        if CONFIG["features.config.prepopulateEditValues"] and initialValue != "<none>":
          initialText = initialValue
        
        newConfigValue = panels["control"].getstr(0, len(titleMsg), initialText)
        
        # it would be nice to quit on esc, but looks like this might not be possible...
        if newConfigValue != None and newConfigValue != initialValue:
          conn = torTools.getConn()
          
          # if the value's a boolean then allow for 'true' and 'false' inputs
          if selection.get(configPanel.Field.TYPE) == "Boolean":
            if newConfigValue.lower() == "true": newConfigValue = "1"
            elif newConfigValue.lower() == "false": newConfigValue = "0"
          
          try:
            if selection.get(configPanel.Field.TYPE) == "LineList":
              newConfigValue = newConfigValue.split(",")
            
            conn.setOption(configOption, newConfigValue)
            
            # resets the isDefault flag
            customOptions = torConfig.getCustomOptions()
            selection.fields[configPanel.Field.IS_DEFAULT] = not configOption in customOptions
            
            panels["config"].redraw(True)
          except Exception, exc:
            errorMsg = "%s (press any key)" % exc
            panels["control"].setMsg(uiTools.cropStr(errorMsg, displayWidth), curses.A_STANDOUT)
            panels["control"].redraw(True)
            
            curses.cbreak() # wait indefinitely for key presses (no timeout)
            stdscr.getch()
            curses.halfdelay(REFRESH_RATE * 10)
        
        panels["control"].setMsg(CTL_PAUSED if isPaused else CTL_HELP)
        setPauseState(panels, isPaused, page)
      finally:
        panel.CURSES_LOCK.release()
    elif page == 4 and key == ord('r') or key == ord('R'):
      # reloads torrc, providing a notice if successful or not
      loadedTorrc = torConfig.getTorrc()
      loadedTorrc.getLock().acquire()
      
      try:
        loadedTorrc.load()
        isSuccessful = True
      except IOError:
        isSuccessful = False
      
      loadedTorrc.getLock().release()
      
      #isSuccessful = panels["torrc"].loadConfig(logErrors = False)
      #confTypeLabel = confPanel.CONFIG_LABELS[panels["torrc"].configType]
      resetMsg = "torrc reloaded" if isSuccessful else "failed to reload torrc"
      if isSuccessful:
        panels["torrc"]._lastContentHeightArgs = None
        panels["torrc"].redraw(True)
      
      panels["control"].setMsg(resetMsg, curses.A_STANDOUT)
      panels["control"].redraw(True)
      time.sleep(1)
      
      panels["control"].setMsg(CTL_PAUSED if isPaused else CTL_HELP)
    elif page == 0:
      panels["log"].handleKey(key)
    elif page == 1:
      panels["conn"].handleKey(key)
    elif page == 2:
      panels["conn2"].handleKey(key)
    elif page == 3:
      panels["config"].handleKey(key)
    elif page == 4:
      panels["torrc"].handleKey(key)

def startTorMonitor(startTime, loggedEvents, isBlindMode):
  try:
    curses.wrapper(drawTorMonitor, startTime, loggedEvents, isBlindMode)
  except KeyboardInterrupt:
    pass # skip printing stack trace in case of keyboard interrupt

