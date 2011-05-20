"""
Main interface loop for arm, periodically redrawing the screen and issuing
user input to the proper panels.
"""

import time
import curses
import threading

import cli.popups
import cli.headerPanel
import cli.logPanel
import cli.configPanel
import cli.torrcPanel
import cli.graphing.graphPanel
import cli.graphing.bandwidthStats
import cli.graphing.connStats
import cli.graphing.resourceStats
import cli.connections.connPanel

from util import connections, conf, enum, log, panel, sysTools, torConfig, torTools

ARM_CONTROLLER = None

CONFIG = {"startup.events": "N3",
          "startup.blindModeEnabled": False,
          "features.redrawRate": 5,
          "features.confirmQuit": True,
          "features.graph.type": 1,
          "features.graph.bw.prepopulate": True,
          "log.startTime": log.INFO,
          "log.torEventTypeUnrecognized": log.NOTICE,
          "log.configEntryUndefined": log.NOTICE,
          "log.unknownTorPid": log.WARN}

GraphStat = enum.Enum("BANDWIDTH", "CONNECTIONS", "SYSTEM_RESOURCES")

# maps 'features.graph.type' config values to the initial types
GRAPH_INIT_STATS = {1: GraphStat.BANDWIDTH, 2: GraphStat.CONNECTIONS, 3: GraphStat.SYSTEM_RESOURCES}

def getController():
  """
  Provides the arm controller instance.
  """
  
  return ARM_CONTROLLER

def initController(stdscr, startTime):
  """
  Spawns the controller, and related panels for it.
  
  Arguments:
    stdscr - curses window
  """
  
  global ARM_CONTROLLER
  config = conf.getConfig("arm")
  
  # initializes the panels
  stickyPanels = [cli.headerPanel.HeaderPanel(stdscr, startTime, config),
                  LabelPanel(stdscr)]
  pagePanels = []
  
  # first page: graph and log
  expandedEvents = cli.logPanel.expandEvents(CONFIG["startup.events"])
  pagePanels.append([cli.graphing.graphPanel.GraphPanel(stdscr),
                     cli.logPanel.LogPanel(stdscr, expandedEvents, config)])
  
  # second page: connections
  if not CONFIG["startup.blindModeEnabled"]:
    pagePanels.append([cli.connections.connPanel.ConnectionPanel(stdscr, config)])
  
  # third page: config
  pagePanels.append([cli.configPanel.ConfigPanel(stdscr, cli.configPanel.State.TOR, config)])
  
  # fourth page: torrc
  pagePanels.append([cli.torrcPanel.TorrcPanel(stdscr, cli.torrcPanel.Config.TORRC, config)])
  
  # initializes the controller
  ARM_CONTROLLER = Controller(stdscr, stickyPanels, pagePanels)
  
  # additional configuration for the graph panel
  graphPanel = ARM_CONTROLLER.getPanel("graph")
  
  # statistical monitors for graph
  bwStats = cli.graphing.bandwidthStats.BandwidthStats(config)
  graphPanel.addStats(GraphStat.BANDWIDTH, bwStats)
  graphPanel.addStats(GraphStat.SYSTEM_RESOURCES, cli.graphing.resourceStats.ResourceStats())
  if not CONFIG["startup.blindModeEnabled"]:
    graphPanel.addStats(GraphStat.CONNECTIONS, cli.graphing.connStats.ConnStats())
  
  # sets graph based on config parameter
  try:
    initialStats = GRAPH_INIT_STATS.get(CONFIG["features.graph.type"])
    graphPanel.setStats(initialStats)
  except ValueError: pass # invalid stats, maybe connections when in blind mode
  
  # prepopulates bandwidth values from state file
  if CONFIG["features.graph.bw.prepopulate"]:
    isSuccessful = bwStats.prepopulateFromState()
    if isSuccessful: graphPanel.updateInterval = 4

class LabelPanel(panel.Panel):
  """
  Panel that just displays a single line of text.
  """
  
  def __init__(self, stdscr):
    panel.Panel.__init__(self, stdscr, "msg", 0, 1)
    self.msgText = ""
    self.msgAttr = curses.A_NORMAL
  
  def setMessage(self, msg, attr = None):
    """
    Sets the message being displayed by the panel.
    
    Arguments:
      msg  - string to be displayed
      attr - attribute for the label, normal text if undefined
    """
    
    if attr == None: attr = curses.A_NORMAL
    self.msgText = msg
    self.msgAttr = attr
  
  def draw(self, width, height):
    self.addstr(0, 0, self.msgText, self.msgAttr)

class Controller:
  """
  Tracks the global state of the interface
  """
  
  def __init__(self, stdscr, stickyPanels, pagePanels):
    """
    Creates a new controller instance. Panel lists are ordered as they appear,
    top to bottom on the page.
    
    Arguments:
      stdscr       - curses window
      stickyPanels - panels shown at the top of each page
      pagePanels   - list of pages, each being a list of the panels on it
    """
    
    self._screen = stdscr
    self._stickyPanels = stickyPanels
    self._pagePanels = pagePanels
    self._page = 0
    self._isPaused = False
    self._forceRedraw = False
    self.setMsg() # initializes our control message
  
  def getScreen(self):
    """
    Provides our curses window.
    """
    
    return self._screen
  
  def getPage(self):
    """
    Provides the number belonging to this page. Page numbers start at zero.
    """
    
    return self._page
  
  def nextPage(self):
    """
    Increments the page number.
    """
    
    self._page = (self._page + 1) % len(self._pagePanels)
    self._forceRedraw = True
    self.setMsg()
  
  def prevPage(self):
    """
    Decrements the page number.
    """
    
    self._page = (self._page - 1) % len(self._pagePanels)
    self._forceRedraw = True
    self.setMsg()
  
  def isPaused(self):
    """
    True if the interface is paused, false otherwise.
    """
    
    return self._isPaused
  
  def setPaused(self, isPause):
    """
    Sets the interface to be paused or unpaused.
    """
    
    if isPause != self._isPaused:
      self._isPaused = isPause
      self._forceRedraw = True
      self.setMsg()
      
      for panelImpl in self.getAllPanels():
        panelImpl.setPaused(isPause)
  
  def getPanel(self, name):
    """
    Provides the panel with the given identifier. This returns None if no such
    panel exists.
    
    Arguments:
      name - name of the panel to be fetched
    """
    
    for panelImpl in self.getAllPanels():
      if panelImpl.getName() == name:
        return panelImpl
    
    return None
  
  def getStickyPanels(self):
    """
    Provides the panels visibile at the top of every page.
    """
    
    return list(self._stickyPanels)
  
  def getDisplayPanels(self, includeSticky = True):
    """
    Provides all panels belonging to the current page and sticky content above
    it. This is ordered they way they are presented (top to bottom) on the
    page.
    
    Arguments:
      includeSticky - includes sticky panels in the results if true
    """
    
    if includeSticky:
      return self._stickyPanels + self._pagePanels[self._page]
    else:
      return list(self._pagePanels[self._page])
  
  def getDaemonPanels(self):
    """
    Provides thread panels.
    """
    
    threadPanels = []
    for panelImpl in self.getAllPanels():
      if isinstance(panelImpl, threading.Thread):
        threadPanels.append(panelImpl)
    
    return threadPanels
  
  def getAllPanels(self):
    """
    Provides all panels in the interface.
    """
    
    allPanels = list(self._stickyPanels)
    
    for page in self._pagePanels:
      allPanels += list(page)
    
    return allPanels
  
  def requestRedraw(self):
    """
    Requests that all content is redrawn when the interface is next rendered.
    """
    
    self._forceRedraw = True
  
  def isRedrawRequested(self, clearFlag = False):
    """
    True if a full redraw has been requested, false otherwise.
    
    Arguments:
      clearFlag - request clears the flag if true
    """
    
    returnValue = self._forceRedraw
    if clearFlag: self._forceRedraw = False
    return returnValue
  
  def setMsg(self, msg = None, attr = None, redraw = False):
    """
    Sets the message displayed in the interfaces control panel. This uses our
    default prompt if no arguments are provided.
    
    Arguments:
      msg    - string to be displayed
      attr   - attribute for the label, normal text if undefined
      redraw - redraws right away if true, otherwise redraws when display
               content is next normally drawn
    """
    
    if msg == None:
      msg = ""
      
      if attr == None:
        if not self._isPaused:
          msg = "page %i / %i - q: quit, p: pause, h: page help" % (self._page + 1, len(self._pagePanels))
          attr = curses.A_NORMAL
        else:
          msg = "Paused"
          attr = curses.A_STANDOUT
    
    controlPanel = self.getPanel("msg")
    controlPanel.setMessage(msg, attr)
    
    if redraw: controlPanel.redraw(True)
    else: self._forceRedraw = True

def shutdownDaemons():
  """
  Stops and joins on worker threads.
  """
  
  # prevents further worker threads from being spawned
  torTools.NO_SPAWN = True
  
  # stops panel daemons
  control = getController()
  for panelImpl in control.getDaemonPanels(): panelImpl.stop()
  for panelImpl in control.getDaemonPanels(): panelImpl.join()
  
  # joins on TorCtl event thread
  torTools.getConn().close()
  
  # joins on utility daemon threads - this might take a moment since the
  # internal threadpools being joined might be sleeping
  resourceTrackers = sysTools.RESOURCE_TRACKERS.values()
  resolver = connections.getResolver("tor") if connections.isResolverAlive("tor") else None
  for tracker in resourceTrackers: tracker.stop()
  if resolver: resolver.stop()  # sets halt flag (returning immediately)
  for tracker in resourceTrackers: tracker.join()
  if resolver: resolver.join()  # joins on halted resolver

def heartbeatCheck(isUnresponsive):
  """
  Logs if its been ten seconds since the last BW event.
  
  Arguments:
    isUnresponsive - flag for if we've indicated to be responsive or not
  """
  
  conn = torTools.getConn()
  lastHeartbeat = conn.getHeartbeat()
  if conn.isAlive() and "BW" in conn.getControllerEvents() and lastHeartbeat != 0:
    if not isUnresponsive and (time.time() - lastHeartbeat) >= 10:
      isUnresponsive = True
      log.log(log.NOTICE, "Relay unresponsive (last heartbeat: %s)" % time.ctime(lastHeartbeat))
    elif isUnresponsive and (time.time() - lastHeartbeat) < 10:
      # really shouldn't happen (meant Tor froze for a bit)
      isUnresponsive = False
      log.log(log.NOTICE, "Relay resumed")
  
  return isUnresponsive

def connResetListener(_, eventType):
  """
  Pauses connection resolution when tor's shut down, and resumes if started
  again.
  """
  
  if connections.isResolverAlive("tor"):
    resolver = connections.getResolver("tor")
    resolver.setPaused(eventType == torTools.State.CLOSED)

def startTorMonitor(startTime):
  """
  Initializes the interface and starts the main draw loop.
  
  Arguments:
    startTime - unix time for when arm was started
  """
  
  # initializes interface configs
  config = conf.getConfig("arm")
  config.update(CONFIG)
  
  cli.graphing.graphPanel.loadConfig(config)
  cli.connections.connEntry.loadConfig(config)
  
  # attempts to fetch the tor pid, warning if unsuccessful (this is needed for
  # checking its resource usage, among other things)
  conn = torTools.getConn()
  torPid = conn.getMyPid()
  
  if not torPid:
    msg = "Unable to determine Tor's pid. Some information, like its resource usage will be unavailable."
    log.log(CONFIG["log.unknownTorPid"], msg)
  
  # adds events needed for arm functionality to the torTools REQ_EVENTS
  # mapping (they're then included with any setControllerEvents call, and log
  # a more helpful error if unavailable)
  
  torTools.REQ_EVENTS["BW"] = "bandwidth graph won't function"
  
  if not CONFIG["startup.blindModeEnabled"]:
    torTools.REQ_EVENTS["CIRC"] = "may cause issues in identifying client connections"
    
    # Configures connection resoultions. This is paused/unpaused according to
    # if Tor's connected or not.
    conn.addStatusListener(connResetListener)
    
    if torPid:
      # use the tor pid to help narrow connection results
      torCmdName = sysTools.getProcessName(torPid, "tor")
      connections.getResolver(torCmdName, torPid, "tor")
    else: connections.getResolver("tor")
    
    # hack to display a better (arm specific) notice if all resolvers fail
    connections.RESOLVER_FINAL_FAILURE_MSG += " (connection related portions of the monitor won't function)"
  
  # loads the torrc and provides warnings in case of validation errors
  try:
    loadedTorrc = torConfig.getTorrc()
    loadedTorrc.load(True)
    loadedTorrc.logValidationIssues()
  except IOError: pass
  
  # provides a notice about any event types tor supports but arm doesn't
  missingEventTypes = cli.logPanel.getMissingEventTypes()
  
  if missingEventTypes:
    pluralLabel = "s" if len(missingEventTypes) > 1 else ""
    log.log(CONFIG["log.torEventTypeUnrecognized"], "arm doesn't recognize the following event type%s: %s (log 'UNKNOWN' events to see them)" % (pluralLabel, ", ".join(missingEventTypes)))
  
  try:
    curses.wrapper(drawTorMonitor, startTime)
  except KeyboardInterrupt:
    pass # skip printing stack trace in case of keyboard interrupt

def drawTorMonitor(stdscr, startTime):
  """
  Main draw loop context.
  
  Arguments:
    stdscr    - curses window
    startTime - unix time for when arm was started
  """
  
  initController(stdscr, startTime)
  control = getController()
  
  # provides notice about any unused config keys
  for key in conf.getConfig("arm").getUnusedKeys():
    log.log(CONFIG["log.configEntryUndefined"], "Unused configuration entry: %s" % key)
  
  # tells daemon panels to start
  for panelImpl in control.getDaemonPanels(): panelImpl.start()
  
  # allows for background transparency
  try: curses.use_default_colors()
  except curses.error: pass
  
  # makes the cursor invisible
  try: curses.curs_set(0)
  except curses.error: pass
  
  # logs the initialization time
  msg = "arm started (initialization took %0.3f seconds)" % (time.time() - startTime)
  log.log(CONFIG["log.startTime"], msg)
  
  # main draw loop
  overrideKey = None     # uses this rather than waiting on user input
  isUnresponsive = False # flag for heartbeat responsiveness check
  
  while True:
    displayPanels = control.getDisplayPanels()
    isUnresponsive = heartbeatCheck(isUnresponsive)
    
    # sets panel visability
    for panelImpl in control.getAllPanels():
      panelImpl.setVisible(panelImpl in displayPanels)
    
    # panel placement
    occupiedContent = 0
    for panelImpl in displayPanels:
      panelImpl.setTop(occupiedContent)
      occupiedContent += panelImpl.getHeight()
    
    # redraws visible content
    forceRedraw = control.isRedrawRequested(True)
    for panelImpl in displayPanels:
      panelImpl.redraw(forceRedraw)
    
    stdscr.refresh()
    
    # wait for user keyboard input until timeout, unless an override was set
    if overrideKey:
      key, overrideKey = overrideKey, None
    else:
      curses.halfdelay(CONFIG["features.redrawRate"] * 10)
      key = stdscr.getch()
    
    if key == curses.KEY_RIGHT:
      control.nextPage()
    elif key == curses.KEY_LEFT:
      control.prevPage()
    elif key == ord('p') or key == ord('P'):
      control.setPaused(not control.isPaused())
    elif key == ord('q') or key == ord('Q'):
      # provides prompt to confirm that arm should exit
      if CONFIG["features.confirmQuit"]:
        msg = "Are you sure (q again to confirm)?"
        confirmationKey = cli.popups.showMsg(msg, attr = curses.A_BOLD)
        quitConfirmed = confirmationKey in (ord('q'), ord('Q'))
      else: quitConfirmed = True
      
      if quitConfirmed:
        shutdownDaemons()
        break
    elif key == ord('x') or key == ord('X'):
      # provides prompt to confirm that arm should issue a sighup
      msg = "This will reset Tor's internal state. Are you sure (x again to confirm)?"
      confirmationKey = cli.popups.showMsg(msg, attr = curses.A_BOLD)
      
      if confirmationKey in (ord('x'), ord('X')):
        try: torTools.getConn().reload()
        except IOError, exc:
          log.log(log.ERR, "Error detected when reloading tor: %s" % sysTools.getFileErrorMsg(exc))
    elif key == ord('h') or key == ord('H'):
      overrideKey = cli.popups.showHelpPopup()
    else:
      for panelImpl in displayPanels:
        isKeystrokeConsumed = panelImpl.handleKey(key)
        if isKeystrokeConsumed: break

