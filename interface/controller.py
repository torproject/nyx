#!/usr/bin/env python
# controller.py -- arm interface (curses monitor for relay status)
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

"""
Curses (terminal) interface for the arm relay status monitor.
"""

import re
import os
import math
import time
import curses
import socket
from threading import RLock
from TorCtl import TorCtl
from TorCtl import TorUtil

import headerPanel
import graphPanel
import logPanel
import connPanel
import confPanel
import descriptorPopup
import fileDescriptorPopup

import util
import connResolver
import bandwidthMonitor
import cpuMemMonitor
import connCountMonitor

CONFIRM_QUIT = True
DISABLE_CONNECTIONS_PAGE = False
REFRESH_RATE = 5        # seconds between redrawing screen
cursesLock = RLock()    # global curses lock (curses isn't thread safe and
                        # concurrency bugs produce especially sinister glitches)
MAX_REGEX_FILTERS = 5   # maximum number of previous regex filters that'll be remembered

# enums for message in control label
CTL_HELP, CTL_PAUSED = range(2)

# panel order per page
PAGE_S = ["header", "control", "popup"] # sticky (ie, always available) page
PAGES = [
  ["graph", "log"],
  ["conn"],
  ["torrc"]]
PAUSEABLE = ["header", "graph", "log", "conn"]

# events needed for panels other than the event log
REQ_EVENTS = ["BW", "NEWDESC", "NEWCONSENSUS", "CIRC"]

class ControlPanel(util.Panel):
  """ Draws single line label for interface controls. """
  
  def __init__(self, lock, resolver):
    util.Panel.__init__(self, lock, 1)
    self.msgText = CTL_HELP           # message text to be displyed
    self.msgAttr = curses.A_NORMAL    # formatting attributes
    self.page = 1                     # page number currently being displayed
    self.resolver = resolver          # dns resolution thread
    self.resolvingCounter = -1         # count of resolver when starting (-1 if we aren't working on a batch)
  
  def setMsg(self, msgText, msgAttr=curses.A_NORMAL):
    """
    Sets the message and display attributes. If msgType matches CTL_HELP or
    CTL_PAUSED then uses the default message for those statuses.
    """
    
    self.msgText = msgText
    self.msgAttr = msgAttr
  
  def redraw(self):
    if self.win:
      self.clear()
      
      msgText = self.msgText
      msgAttr = self.msgAttr
      barTab = 2                # space between msgText and progress bar
      barWidthMax = 40          # max width to progress bar
      barWidth = -1             # space between "[ ]" in progress bar (not visible if -1)
      barProgress = 0           # cells to fill
      
      if msgText == CTL_HELP:
        msgAttr = curses.A_NORMAL
        
        if self.resolvingCounter != -1:
          if self.resolver.unresolvedQueue.empty() or self.resolver.isPaused:
            # done resolving dns batch
            self.resolvingCounter = -1
            curses.halfdelay(REFRESH_RATE * 10) # revert to normal refresh rate
          else:
            batchSize = self.resolver.totalResolves - self.resolvingCounter
            entryCount = batchSize - self.resolver.unresolvedQueue.qsize()
            if batchSize > 0: progress = 100 * entryCount / batchSize
            else: progress = 0
            
            additive = "or l " if self.page == 2 else ""
            batchSizeDigits = int(math.log10(batchSize)) + 1
            entryCountLabel = ("%%%ii" % batchSizeDigits) % entryCount
            #msgText = "Resolving hostnames (%i / %i, %i%%) - press esc %sto cancel" % (entryCount, batchSize, progress, additive)
            msgText = "Resolving hostnames (press esc %sto cancel) - %s / %i, %2i%%" % (additive, entryCountLabel, batchSize, progress)
            
            barWidth = min(barWidthMax, self.maxX - len(msgText) - 3 - barTab)
            barProgress = barWidth * entryCount / batchSize
        
        if self.resolvingCounter == -1:
          currentPage = self.page
          pageCount = len(PAGES)
          
          if DISABLE_CONNECTIONS_PAGE:
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
        self.addstr(0, xLoc + 1, " " * barProgress, curses.A_STANDOUT | util.getColor("red"))
        self.addstr(0, xLoc + barWidth + 1, "]", curses.A_BOLD)
      
      self.refresh()

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
    if not popup.lock.acquire(False): return -1
    try:
      curses.cbreak() # wait indefinitely for key presses (no timeout)
      
      # uses smaller dimentions more fitting for small content
      popup.height = len(options) + 2
      
      newWidth = max([len(label) for label in options]) + 9
      popup.recreate(stdscr, popup.startY, newWidth)
      
      key = 0
      while key not in (curses.KEY_ENTER, 10, ord(' ')):
        popup.clear()
        popup.win.box()
        popup.addstr(0, 0, title, util.LABEL_ATTR)
        
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
      popup.recreate(stdscr, popup.startY, 80)
      
      curses.halfdelay(REFRESH_RATE * 10) # reset normal pausing behavior
    finally:
      cursesLock.release()
  
  return selection

def setEventListening(loggedEvents, conn, logListener):
  """
  Tries to set events being listened for, displaying error for any event
  types that aren't supported (possibly due to version issues). This returns 
  a list of event types that were successfully set.
  """
  eventsSet = False
  
  # adds events used for panels to function if not already included
  connEvents = loggedEvents.union(set(REQ_EVENTS))
  
  # removes UNKNOWN since not an actual event type
  connEvents.discard("UNKNOWN")
  
  while not eventsSet:
    try:
      conn.set_events(connEvents)
      eventsSet = True
    except TorCtl.ErrorReply, exc:
      msg = str(exc)
      if "Unrecognized event" in msg:
        # figure out type of event we failed to listen for
        start = msg.find("event \"") + 7
        end = msg.rfind("\"")
        eventType = msg[start:end]
        
        # removes and notes problem
        connEvents.discard(eventType)
        if eventType in loggedEvents: loggedEvents.remove(eventType)
        
        if eventType in REQ_EVENTS:
          if eventType == "BW": msg = "(bandwidth graph won't function)"
          elif eventType in ("NEWDESC", "NEWCONSENSUS"): msg = "(connections listing can't register consensus changes)"
          else: msg = ""
          logListener.monitor_event("ERR", "Unsupported event type: %s %s" % (eventType, msg))
        else: logListener.monitor_event("WARN", "Unsupported event type: %s" % eventType)
    except TorCtl.TorCtlClosed:
      return []
  
  loggedEvents = list(loggedEvents)
  loggedEvents.sort() # alphabetizes
  return loggedEvents

def drawTorMonitor(stdscr, conn, loggedEvents):
  """
  Starts arm interface reflecting information on provided control port.
  
  stdscr - curses window
  conn - active Tor control port connection
  loggedEvents - types of events to be logged (plus an optional "UNKNOWN" for
    otherwise unrecognized events)
  """
  
  curses.use_default_colors()           # allows things like semi-transparent backgrounds
  util.initColors()                     # initalizes color pairs for colored text
  curses.halfdelay(REFRESH_RATE * 10)   # uses getch call as timer for REFRESH_RATE seconds
  
  # attempts to make the cursor invisible (not supported in all terminals)
  try: curses.curs_set(0)
  except curses.error: pass
  
  # gets pid of tor instance with control port open
  torPid = None       # None if couldn't be resolved (provides error later)
  
  pidOfCall = os.popen("pidof tor 2> /dev/null")
  try:
    # gets pid if there's only one possability
    results = pidOfCall.readlines()
    if len(results) == 1 and len(results[0].split()) == 1: torPid = results[0].strip()
  except IOError: pass # pid call failed
  pidOfCall.close()
  
  if not torPid:
    try:
      # uses netstat to identify process with open control port (might not
      # work if tor's being run as a different user due to permissions)
      netstatCall = os.popen("netstat -npl 2> /dev/null | grep 127.0.0.1:%s" % conn.get_option("ControlPort")[0][1])
      results = netstatCall.readlines()
      
      if len(results) == 1:
        results = results[0].split()[6] # process field (ex. "7184/tor")
        torPid = results[:results.find("/")]
    except (IOError, socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed): pass # netstat or control port calls failed
    netstatCall.close()
  
  if not torPid:
    try:
      # third try, use ps if there's only one possability
      psCall = os.popen("ps -o pid -C tor 2> /dev/null")
      results = psCall.readlines()
      if len(results) == 2 and len(results[0].split()) == 1: torPid = results[1].strip()
    except IOError: pass # ps call failed
    psCall.close()
  
  try:
    confLocation = conn.get_info("config-file")["config-file"]
    if confLocation[0] != "/":
      # relative path - attempt to add process pwd
      try:
        pwdxCall = os.popen("pwdx %s 2> /dev/null" % torPid)
        results = pwdxCall.readlines()
        if len(results) == 1 and len(results[0].split()) == 2: confLocation = "%s/%s" % (results[0].split()[1], confLocation)
      except IOError: pass # pwdx call failed
      pwdxCall.close()
  except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed):
    confLocation = ""
  
  panels = {
    "header": headerPanel.HeaderPanel(cursesLock, conn, torPid),
    "popup": util.Panel(cursesLock, 9),
    "graph": graphPanel.GraphPanel(cursesLock),
    "log": logPanel.LogMonitor(cursesLock, conn, loggedEvents)}
  
  # starts thread for processing netstat queries
  connResolutionThread = connResolver.ConnResolver(conn, torPid, panels["log"])
  connResolutionThread.start()
  
  panels["conn"] = connPanel.ConnPanel(cursesLock, conn, connResolutionThread, panels["log"])
  panels["control"] = ControlPanel(cursesLock, panels["conn"].resolver)
  panels["torrc"] = confPanel.ConfPanel(cursesLock, confLocation, conn, panels["log"])
  
  # prevents netstat calls by connPanel if not being used
  if DISABLE_CONNECTIONS_PAGE: panels["conn"].isDisabled = True
  
  # provides error if pid coulnd't be determined (hopefully shouldn't happen...)
  if not torPid: panels["log"].monitor_event("WARN", "Unable to resolve tor pid, abandoning connection listing")
  
  # statistical monitors for graph
  panels["graph"].addStats("bandwidth", bandwidthMonitor.BandwidthMonitor(conn))
  panels["graph"].addStats("system resources", cpuMemMonitor.CpuMemMonitor(panels["header"]))
  panels["graph"].addStats("connections", connCountMonitor.ConnCountMonitor(conn, connResolutionThread))
  panels["graph"].setStats("bandwidth")
  
  # listeners that update bandwidth and log panels with Tor status
  sighupTracker = sighupListener()
  conn.add_event_listener(panels["log"])
  conn.add_event_listener(panels["graph"].stats["bandwidth"])
  conn.add_event_listener(panels["graph"].stats["system resources"])
  conn.add_event_listener(panels["graph"].stats["connections"])
  conn.add_event_listener(panels["conn"])
  conn.add_event_listener(sighupTracker)
  
  # tells Tor to listen to the events we're interested
  loggedEvents = setEventListening(loggedEvents, conn, panels["log"])
  panels["log"].loggedEvents = loggedEvents # strips any that couldn't be set
  
  # directs logged TorCtl events to log panel
  TorUtil.loglevel = "DEBUG"
  TorUtil.logfile = panels["log"]
  
  # warns if tor isn't updating descriptors
  try:
    if conn.get_option("FetchUselessDescriptors")[0][1] == "0" and conn.get_option("DirPort")[0][1] == "0":
      warning = ["Descriptors won't be updated (causing some connection information to be stale) unless:", \
                "  a. 'FetchUselessDescriptors 1' is set in your torrc", \
                "  b. the directory service is provided ('DirPort' defined)", \
                "  c. or tor is used as a client"]
      panels["log"].monitor_event("WARN", warning)
  except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed): pass
  
  isUnresponsive = False    # true if it's been over ten seconds since the last BW event (probably due to Tor closing)
  isPaused = False          # if true updates are frozen
  page = 0
  regexFilters = []             # previously used log regex filters
  
  while True:
    # tried only refreshing when the screen was resized but it caused a
    # noticeable lag when resizing and didn't have an appreciable effect
    # on system usage
    
    cursesLock.acquire()
    try:
      # if sighup received then reload related information
      if sighupTracker.isReset:
        panels["header"]._updateParams(True)
        
        # if bandwidth graph is being shown then height might have changed
        if panels["graph"].currentDisplay == "bandwidth":
          panels["graph"].height = panels["graph"].stats["bandwidth"].height
        
        # other panels that use torrc data
        panels["conn"].resetOptions()
        panels["graph"].stats["connections"].resetOptions(conn)
        panels["graph"].stats["bandwidth"].resetOptions()
        
        panels["torrc"].reset()
        sighupTracker.isReset = False
      
      # gives panels a chance to take advantage of the maximum bounds
      # originally this checked in the bounds changed but 'recreate' is a no-op
      # if panel properties are unchanged and checking every redraw is more
      # resilient in case of funky changes (such as resizing during popups)
      startY = 0
      for panelKey in PAGE_S[:2]:
        panels[panelKey].recreate(stdscr, startY)
        startY += panels[panelKey].height
      
      panels["popup"].recreate(stdscr, startY, 80)
      
      for panelSet in PAGES:
        tmpStartY = startY
        
        for panelKey in panelSet:
          panels[panelKey].recreate(stdscr, tmpStartY)
          tmpStartY += panels[panelKey].height
      
      # if it's been at least ten seconds since the last BW event Tor's probably done
      if not isUnresponsive and not panels["log"].controlPortClosed and panels["log"].getHeartbeat() >= 10:
        isUnresponsive = True
        panels["log"].monitor_event("NOTICE", "Relay unresponsive (last heartbeat: %s)" % time.ctime(panels["log"].lastHeartbeat))
      elif not panels["log"].controlPortClosed and (isUnresponsive and panels["log"].getHeartbeat() < 10):
        # shouldn't happen unless Tor freezes for a bit - BW events happen every second...
        isUnresponsive = False
        panels["log"].monitor_event("NOTICE", "Relay resumed")
      
      panels["conn"].reset()
      
      # I haven't the foggiest why, but doesn't work if redrawn out of order...
      for panelKey in (PAGE_S + PAGES[page]): panels[panelKey].redraw()
      stdscr.refresh()
    finally:
      cursesLock.release()
    
    key = stdscr.getch()
    if key == ord('q') or key == ord('Q'):
      quitConfirmed = not CONFIRM_QUIT
      
      # provides prompt to confirm that arm should exit
      if CONFIRM_QUIT:
        cursesLock.acquire()
        try:
          setPauseState(panels, isPaused, page, True)
          
          # provides prompt
          panels["control"].setMsg("Are you sure (q again to confirm)?", curses.A_BOLD)
          panels["control"].redraw()
          
          curses.cbreak()
          confirmationKey = stdscr.getch()
          quitConfirmed = confirmationKey in (ord('q'), ord('Q'))
          curses.halfdelay(REFRESH_RATE * 10)
          
          panels["control"].setMsg(CTL_PAUSED if isPaused else CTL_HELP)
          setPauseState(panels, isPaused, page)
        finally:
          cursesLock.release()
      
      if quitConfirmed:
        # quits arm
        # very occasionally stderr gets "close failed: [Errno 11] Resource temporarily unavailable"
        # this appears to be a python bug: http://bugs.python.org/issue3014
        daemonThreads = panels["conn"].resolver.threadPool
        
        # sets halt flags for all worker daemon threads
        for worker in daemonThreads: worker.halt = True
        
        # joins on workers (prevents noisy termination)
        for worker in daemonThreads: worker.join()
        
        conn.close() # joins on TorCtl event thread
        
        break
    elif key == curses.KEY_LEFT or key == curses.KEY_RIGHT:
      # switch page
      if key == curses.KEY_LEFT: page = (page - 1) % len(PAGES)
      else: page = (page + 1) % len(PAGES)
      
      # skip connections listing if it's disabled
      if page == 1 and DISABLE_CONNECTIONS_PAGE:
        if key == curses.KEY_LEFT: page = (page - 1) % len(PAGES)
        else: page = (page + 1) % len(PAGES)
      
      # pauses panels that aren't visible to prevent events from accumilating
      # (otherwise they'll wait on the curses lock which might get demanding)
      setPauseState(panels, isPaused, page)
      
      panels["control"].page = page + 1
      panels["control"].refresh()
    elif key == ord('p') or key == ord('P'):
      # toggles update freezing
      cursesLock.acquire()
      try:
        isPaused = not isPaused
        setPauseState(panels, isPaused, page)
        panels["control"].setMsg(CTL_PAUSED if isPaused else CTL_HELP)
      finally:
        cursesLock.release()
    elif key == ord('h') or key == ord('H'):
      # displays popup for current page's controls
      cursesLock.acquire()
      try:
        setPauseState(panels, isPaused, page, True)
        
        # lists commands
        popup = panels["popup"]
        popup.clear()
        popup.win.box()
        popup.addstr(0, 0, "Page %i Commands:" % (page + 1), util.LABEL_ATTR)
        
        if page == 0:
          graphedStats = panels["graph"].currentDisplay
          if not graphedStats: graphedStats = "none"
          popup.addfstr(1, 2, "s: graphed stats (<b>%s</b>)" % graphedStats)
          popup.addfstr(1, 41, "i: graph update interval (<b>%s</b>)" % panels["graph"].updateInterval)
          popup.addfstr(2, 2, "b: graph bounds (<b>%s</b>)" % graphPanel.BOUND_LABELS[panels["graph"].bounds])
          popup.addstr(2, 41, "d: file descriptors")
          popup.addstr(3, 2, "e: change logged events")
          
          runlevelEventsLabel = "arm and tor"
          if panels["log"].runlevelTypes == logPanel.RUNLEVEL_TOR_ONLY: runlevelEventsLabel = "tor only"
          elif panels["log"].runlevelTypes == logPanel.RUNLEVEL_ARM_ONLY: runlevelEventsLabel = "arm only"
          popup.addfstr(3, 41, "r: logged runlevels (<b>%s</b>)" % runlevelEventsLabel)
          
          regexLabel = "enabled" if panels["log"].regexFilter else "disabled"
          popup.addfstr(4, 2, "f: log regex filter (<b>%s</b>)" % regexLabel)
        if page == 1:
          popup.addstr(1, 2, "up arrow: scroll up a line")
          popup.addstr(1, 41, "down arrow: scroll down a line")
          popup.addstr(2, 2, "page up: scroll up a page")
          popup.addstr(2, 41, "page down: scroll down a page")
          popup.addstr(3, 2, "enter: connection details")
          popup.addstr(3, 41, "d: raw consensus descriptor")
          
          listingType = connPanel.LIST_LABEL[panels["conn"].listingType].lower()
          popup.addfstr(4, 2, "l: listed identity (<b>%s</b>)" % listingType)
          
          allowDnsLabel = "allow" if panels["conn"].allowDNS else "disallow"
          popup.addfstr(4, 41, "r: permit DNS resolution (<b>%s</b>)" % allowDnsLabel)
          
          popup.addstr(5, 2, "s: sort ordering")
          popup.addstr(5, 41, "c: client circuits")
          #popup.addfstr(5, 41, "c: toggle cursor (<b>%s</b>)" % ("on" if panels["conn"].isCursorEnabled else "off"))
        elif page == 2:
          popup.addstr(1, 2, "up arrow: scroll up a line")
          popup.addstr(1, 41, "down arrow: scroll down a line")
          popup.addstr(2, 2, "page up: scroll up a page")
          popup.addstr(2, 41, "page down: scroll down a page")
          
          strippingLabel = "on" if panels["torrc"].stripComments else "off"
          popup.addfstr(3, 2, "s: comment stripping (<b>%s</b>)" % strippingLabel)
          
          lineNumLabel = "on" if panels["torrc"].showLineNum else "off"
          popup.addfstr(3, 41, "n: line numbering (<b>%s</b>)" % lineNumLabel)
        
        popup.addstr(7, 2, "Press any key...")
        popup.refresh()
        
        curses.cbreak()
        stdscr.getch()
        curses.halfdelay(REFRESH_RATE * 10)
        
        setPauseState(panels, isPaused, page)
      finally:
        cursesLock.release()
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
        panels["graph"].redraw()
      setPauseState(panels, isPaused, page, True)
      
      selection = showMenu(stdscr, panels["popup"], "Graphed Stats:", options, initialSelection)
      
      # reverts changes made for popup
      panels["graph"].showLabel = True
      setPauseState(panels, isPaused, page)
      
      # applies new setting
      if selection != -1 and selection != initialSelection:
        if selection == 0: panels["graph"].setStats(None)
        else: panels["graph"].setStats(options[selection].lower())
    elif page == 0 and (key == ord('i') or key == ord('I')):
      # provides menu to pick graph panel update interval
      options = [label for (label, intervalTime) in graphPanel.UPDATE_INTERVALS]
      
      initialSelection = -1
      for i in range(len(options)):
        if options[i] == panels["graph"].updateInterval: initialSelection = i
      
      # hides top label of the graph panel and pauses panels
      if panels["graph"].currentDisplay:
        panels["graph"].showLabel = False
        panels["graph"].redraw()
      setPauseState(panels, isPaused, page, True)
      
      selection = showMenu(stdscr, panels["popup"], "Update Interval:", options, initialSelection)
      
      # reverts changes made for popup
      panels["graph"].showLabel = True
      setPauseState(panels, isPaused, page)
      
      # applies new setting
      if selection != -1: panels["graph"].updateInterval = options[selection]
    elif page == 0 and (key == ord('b') or key == ord('B')):
      # uses the next boundary type for graph
      panels["graph"].bounds = (panels["graph"].bounds + 1) % 2
    elif page == 0 and key in (ord('d'), ord('D')):
      # provides popup with file descriptors
      cursesLock.acquire()
      try:
        setPauseState(panels, isPaused, page, True)
        curses.cbreak() # wait indefinitely for key presses (no timeout)
        
        fileDescriptorPopup.showFileDescriptorPopup(panels["popup"], stdscr, torPid)
        
        setPauseState(panels, isPaused, page)
        curses.halfdelay(REFRESH_RATE * 10) # reset normal pausing behavior
      finally:
        cursesLock.release()
    elif page == 0 and (key == ord('e') or key == ord('E')):
      # allow user to enter new types of events to log - unchanged if left blank
      cursesLock.acquire()
      try:
        setPauseState(panels, isPaused, page, True)
        
        # provides prompt
        panels["control"].setMsg("Events to log: ")
        panels["control"].redraw()
        
        # makes cursor and typing visible
        try: curses.curs_set(1)
        except curses.error: pass
        curses.echo()
        
        # lists event types
        popup = panels["popup"]
        popup.height = 10
        popup.recreate(stdscr, popup.startY, 80)
        
        popup.clear()
        popup.win.box()
        popup.addstr(0, 0, "Event Types:", util.LABEL_ATTR)
        lineNum = 1
        for line in logPanel.EVENT_LISTING.split("\n"):
          line = line[6:]
          popup.addstr(lineNum, 1, line)
          lineNum += 1
        popup.refresh()
        
        # gets user input (this blocks monitor updates)
        eventsInput = panels["control"].win.getstr(0, 15)
        eventsInput = eventsInput.replace(' ', '') # strips spaces
        
        # reverts visability settings
        try: curses.curs_set(0)
        except curses.error: pass
        curses.noecho()
        curses.halfdelay(REFRESH_RATE * 10) # evidenlty previous tweaks reset this...
        
        # it would be nice to quit on esc, but looks like this might not be possible...
        if eventsInput != "":
          try:
            expandedEvents = logPanel.expandEvents(eventsInput)
            loggedEvents = setEventListening(expandedEvents, conn, panels["log"])
            panels["log"].loggedEvents = loggedEvents
          except ValueError, exc:
            panels["control"].setMsg("Invalid flags: %s" % str(exc), curses.A_STANDOUT)
            panels["control"].redraw()
            time.sleep(2)
        
        # reverts popup dimensions
        popup.height = 9
        popup.recreate(stdscr, popup.startY, 80)
        
        panels["control"].setMsg(CTL_PAUSED if isPaused else CTL_HELP)
        setPauseState(panels, isPaused, page)
      finally:
        cursesLock.release()
    elif page == 0 and (key == ord('f') or key == ord('F')):
      # provides menu to pick previous regular expression filters or to add a new one
      # for syntax see: http://docs.python.org/library/re.html#regular-expression-syntax
      options = ["None"] + regexFilters + ["New..."]
      initialSelection = 0 if not panels["log"].regexFilter else 1
      
      # hides top label of the graph panel and pauses panels
      if panels["graph"].currentDisplay:
        panels["graph"].showLabel = False
        panels["graph"].redraw()
      setPauseState(panels, isPaused, page, True)
      
      selection = showMenu(stdscr, panels["popup"], "Log Filter:", options, initialSelection)
      
      # applies new setting
      if selection == 0:
        panels["log"].regexFilter = None
      elif selection == len(options) - 1:
        # selected 'New...' option - prompt user to input regular expression
        cursesLock.acquire()
        try:
          # provides prompt
          panels["control"].setMsg("Regular expression: ")
          panels["control"].redraw()
          
          # makes cursor and typing visible
          try: curses.curs_set(1)
          except curses.error: pass
          curses.echo()
          
          # gets user input (this blocks monitor updates)
          regexInput = panels["control"].win.getstr(0, 20)
          
          # reverts visability settings
          try: curses.curs_set(0)
          except curses.error: pass
          curses.noecho()
          curses.halfdelay(REFRESH_RATE * 10)
          
          if regexInput != "":
            try:
              panels["log"].regexFilter = re.compile(regexInput)
              if regexInput in regexFilters: regexFilters.remove(regexInput)
              regexFilters = [regexInput] + regexFilters
            except re.error, exc:
              panels["control"].setMsg("Unable to compile expression: %s" % str(exc), curses.A_STANDOUT)
              panels["control"].redraw()
              time.sleep(2)
          panels["control"].setMsg(CTL_PAUSED if isPaused else CTL_HELP)
        finally:
          cursesLock.release()
      elif selection != -1:
        try:
          panels["log"].regexFilter = re.compile(regexFilters[selection - 1])
          
          # move selection to top
          regexFilters = [regexFilters[selection - 1]] + regexFilters
          del regexFilters[selection]
        except re.error, exc:
          # shouldn't happen since we've already checked validity
          panels["log"].monitor_event("WARN", "Invalid regular expression ('%s': %s) - removing from listing" % (regexFilters[selection - 1], str(exc)))
          del regexFilters[selection - 1]
      
      if len(regexFilters) > MAX_REGEX_FILTERS: del regexFilters[MAX_REGEX_FILTERS:]
      
      # reverts changes made for popup
      panels["graph"].showLabel = True
      setPauseState(panels, isPaused, page)
    elif page == 0 and (key == ord('r') or key == ord('R')):
      # provides menu to pick the type of runlevel events to log
      options = ["tor only", "arm only", "arm and tor"]
      initialSelection = panels["log"].runlevelTypes
      
      # hides top label of the graph panel and pauses panels
      if panels["graph"].currentDisplay:
        panels["graph"].showLabel = False
        panels["graph"].redraw()
      setPauseState(panels, isPaused, page, True)
      
      selection = showMenu(stdscr, panels["popup"], "Logged Runlevels:", options, initialSelection)
      
      # reverts changes made for popup
      panels["graph"].showLabel = True
      setPauseState(panels, isPaused, page)
      
      # applies new setting
      if selection != -1: panels["log"].runlevelTypes = selection
    elif key == 27 and panels["conn"].listingType == connPanel.LIST_HOSTNAME and panels["control"].resolvingCounter != -1:
      # canceling hostname resolution (esc on any page)
      panels["conn"].listingType = connPanel.LIST_IP
      panels["control"].resolvingCounter = -1
      panels["conn"].resolver.setPaused(True)
      panels["conn"].sortConnections()
    elif page == 1 and panels["conn"].isCursorEnabled and key in (curses.KEY_ENTER, 10, ord(' ')):
      # provides details on selected connection
      cursesLock.acquire()
      try:
        setPauseState(panels, isPaused, page, True)
        popup = panels["popup"]
        
        # reconfigures connection panel to accomidate details dialog
        panels["conn"].showLabel = False
        panels["conn"].showingDetails = True
        panels["conn"].redraw()
        
        resolver = panels["conn"].resolver
        resolver.setPaused(not panels["conn"].allowDNS)
        relayLookupCache = {} # temporary cache of entry -> (ns data, desc data)
        
        curses.cbreak() # wait indefinitely for key presses (no timeout)
        key = 0
        
        while key not in (curses.KEY_ENTER, 10, ord(' ')):
          popup.clear()
          popup.win.box()
          popup.addstr(0, 0, "Connection Details:", util.LABEL_ATTR)
          
          selection = panels["conn"].cursorSelection
          if not selection or not panels["conn"].connections: break
          selectionColor = connPanel.TYPE_COLORS[selection[connPanel.CONN_TYPE]]
          format = util.getColor(selectionColor) | curses.A_BOLD
          
          selectedIp = selection[connPanel.CONN_F_IP]
          selectedPort = selection[connPanel.CONN_F_PORT]
          
          addrLabel = "address: %s:%s" % (selectedIp, selectedPort)
          
          if selection[connPanel.CONN_TYPE] == "family" and int(selection[connPanel.CONN_L_PORT]) > 65535:
            # unresolved family entry - unknown ip/port
            addrLabel = "address: unknown"
          
          hostname = resolver.resolve(selectedIp)
          if hostname == None:
            if resolver.isPaused: hostname = "DNS resolution disallowed"
            elif selectedIp not in resolver.resolvedCache.keys():
              # if hostname is still being resolved refresh panel every half-second until it's completed
              curses.halfdelay(5)
              hostname = "resolving..."
            else:
              # hostname couldn't be resolved
              hostname = "unknown"
          elif len(hostname) > 73 - len(addrLabel):
            # hostname too long - truncate
            hostname = "%s..." % hostname[:70 - len(addrLabel)]
          
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
              # ns lookup fails, can happen with localhost lookups if relay's having problems (orport not reachable)
              try: nsData = conn.get_network_status("id/%s" % fingerprint)
              except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed): lookupErrored = True
              
              if not lookupErrored:
                if len(nsData) > 1:
                  # multiple records for fingerprint (shouldn't happen)
                  panels["log"].monitor_event("WARN", "Multiple consensus entries for fingerprint: %s" % fingerprint)
                
                nsEntry = nsData[0]
                
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
            descriptorPopup.showDescriptorPopup(panels["popup"], stdscr, conn, panels["conn"])
            panels["conn"].redraw()
        
        panels["conn"].showLabel = True
        panels["conn"].showingDetails = False
        resolver.setPaused(not panels["conn"].allowDNS and panels["conn"].listingType == connPanel.LIST_HOSTNAME)
        setPauseState(panels, isPaused, page)
        curses.halfdelay(REFRESH_RATE * 10) # reset normal pausing behavior
      finally:
        cursesLock.release()
    elif page == 1 and panels["conn"].isCursorEnabled and key in (ord('d'), ord('D')):
      # presents popup for raw consensus data
      cursesLock.acquire()
      try:
        setPauseState(panels, isPaused, page, True)
        curses.cbreak() # wait indefinitely for key presses (no timeout)
        panels["conn"].showLabel = False
        panels["conn"].redraw()
        
        descriptorPopup.showDescriptorPopup(panels["popup"], stdscr, conn, panels["conn"])
        
        setPauseState(panels, isPaused, page)
        curses.halfdelay(REFRESH_RATE * 10) # reset normal pausing behavior
        panels["conn"].showLabel = True
      finally:
        cursesLock.release()
    elif page == 1 and (key == ord('l') or key == ord('L')):
      # provides menu to pick identification info listed for connections
      optionTypes = [connPanel.LIST_IP, connPanel.LIST_HOSTNAME, connPanel.LIST_FINGERPRINT, connPanel.LIST_NICKNAME]
      options = [connPanel.LIST_LABEL[sortType] for sortType in optionTypes]
      initialSelection = panels["conn"].listingType   # enums correspond to index
      
      # hides top label of conn panel and pauses panels
      panels["conn"].showLabel = False
      panels["conn"].redraw()
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
          panels["control"].resolvingCounter = panels["conn"].resolver.totalResolves - panels["conn"].resolver.unresolvedQueue.qsize()
          
          resolver = panels["conn"].resolver
          resolver.setPaused(not panels["conn"].allowDNS)
          for connEntry in panels["conn"].connections: resolver.resolve(connEntry[connPanel.CONN_F_IP])
        else:
          panels["control"].resolvingCounter = -1
          panels["conn"].resolver.setPaused(True)
        
        panels["conn"].sortConnections()
    elif page == 1 and (key == ord('s') or key == ord('S')):
      # set ordering for connection listing
      cursesLock.acquire()
      try:
        setPauseState(panels, isPaused, page, True)
        curses.cbreak() # wait indefinitely for key presses (no timeout)
        
        # lists event types
        popup = panels["popup"]
        selections = []     # new ordering
        cursorLoc = 0       # index of highlighted option
        
        # listing of inital ordering
        prevOrdering = "<b>Current Order: "
        for sort in panels["conn"].sortOrdering: prevOrdering += connPanel.getSortLabel(sort, True) + ", "
        prevOrdering = prevOrdering[:-2] + "</b>"
        
        # Makes listing of all options
        options = []
        for (type, label, func) in connPanel.SORT_TYPES: options.append(connPanel.getSortLabel(type))
        options.append("Cancel")
        
        while len(selections) < 3:
          popup.clear()
          popup.win.box()
          popup.addstr(0, 0, "Connection Ordering:", util.LABEL_ATTR)
          popup.addfstr(1, 2, prevOrdering)
          
          # provides new ordering
          newOrdering = "<b>New Order: "
          if selections:
            for sort in selections: newOrdering += connPanel.getSortLabel(sort, True) + ", "
            newOrdering = newOrdering[:-2] + "</b>"
          else: newOrdering += "</b>"
          popup.addfstr(2, 2, newOrdering)
          
          row, col, index = 4, 0, 0
          for option in options:
            popup.addstr(row, col * 19 + 2, option, curses.A_STANDOUT if cursorLoc == index else curses.A_NORMAL)
            col += 1
            index += 1
            if col == 4: row, col = row + 1, 0
          
          popup.refresh()
          
          key = stdscr.getch()
          if key == curses.KEY_LEFT: cursorLoc = max(0, cursorLoc - 1)
          elif key == curses.KEY_RIGHT: cursorLoc = min(len(options) - 1, cursorLoc + 1)
          elif key == curses.KEY_UP: cursorLoc = max(0, cursorLoc - 4)
          elif key == curses.KEY_DOWN: cursorLoc = min(len(options) - 1, cursorLoc + 4)
          elif key in (curses.KEY_ENTER, 10, ord(' ')):
            # selected entry (the ord of '10' seems needed to pick up enter)
            selection = options[cursorLoc]
            if selection == "Cancel": break
            else:
              selections.append(connPanel.getSortType(selection.replace("Tor ID", "Fingerprint")))
              options.remove(selection)
              cursorLoc = min(cursorLoc, len(options) - 1)
          elif key == 27: break # esc - cancel
          
        if len(selections) == 3:
          panels["conn"].sortOrdering = selections
          panels["conn"].sortConnections()
        setPauseState(panels, isPaused, page)
        curses.halfdelay(REFRESH_RATE * 10) # reset normal pausing behavior
      finally:
        cursesLock.release()
    elif page == 1 and (key == ord('c') or key == ord('C')):
      # displays popup with client circuits
      clientCircuits = None
      try:
        clientCircuits = conn.get_info("circuit-status")["circuit-status"].split("\n")
      except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed): pass
      
      maxEntryLength = 0
      if clientCircuits:
        for clientEntry in clientCircuits: maxEntryLength = max(len(clientEntry), maxEntryLength)
      
      cursesLock.acquire()
      try:
        setPauseState(panels, isPaused, page, True)
        
        # makes sure there's room for the longest entry
        popup = panels["popup"]
        popup._resetBounds()
        if clientCircuits and maxEntryLength + 4 > popup.maxX:
          popup.height = max(popup.height, len(clientCircuits) + 3)
          popup.recreate(stdscr, popup.startY, maxEntryLength + 4)
        
        # lists commands
        popup.clear()
        popup.win.box()
        popup.addstr(0, 0, "Client Circuits:", util.LABEL_ATTR)
        
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
        popup.recreate(stdscr, popup.startY, 80)
        
        setPauseState(panels, isPaused, page)
      finally:
        cursesLock.release()
    elif page == 0:
      panels["log"].handleKey(key)
    elif page == 1:
      panels["conn"].handleKey(key)
    elif page == 2:
      panels["torrc"].handleKey(key)

def startTorMonitor(conn, loggedEvents):
  curses.wrapper(drawTorMonitor, conn, loggedEvents)

