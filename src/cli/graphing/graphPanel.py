"""
Flexible panel for presenting bar graphs for a variety of stats. This panel is
just concerned with the rendering of information, which is actually collected
and stored by implementations of the GraphStats interface. Panels are made up
of a title, followed by headers and graphs for two sets of stats. For
instance...

Bandwidth (cap: 5 MB, burst: 10 MB):
Downloaded (0.0 B/sec):           Uploaded (0.0 B/sec):
  34                                30
                            *                                 *
                    **  *   *                          *      **
      *   *  *      ** **   **          ***  **       ** **   **
     *********      ******  ******     *********      ******  ******
   0 ************ ****************   0 ************ ****************
         25s  50   1m   1.6  2.0           25s  50   1m   1.6  2.0
"""

import copy
import curses
from TorCtl import TorCtl

import cli.popups
import cli.controller

from util import enum, panel, torTools, uiTools

# time intervals at which graphs can be updated
UPDATE_INTERVALS = [("each second", 1), ("5 seconds", 5),   ("30 seconds", 30),
                    ("minutely", 60),   ("15 minute", 900), ("30 minute", 1800),
                    ("hourly", 3600),   ("daily", 86400)]

DEFAULT_CONTENT_HEIGHT = 4 # space needed for labeling above and below the graph
DEFAULT_COLOR_PRIMARY, DEFAULT_COLOR_SECONDARY = "green", "cyan"
MIN_GRAPH_HEIGHT = 1

# enums for graph bounds:
#   Bounds.GLOBAL_MAX - global maximum (highest value ever seen)
#   Bounds.LOCAL_MAX - local maximum (highest value currently on the graph)
#   Bounds.TIGHT - local maximum and minimum
Bounds = enum.Enum("GLOBAL_MAX", "LOCAL_MAX", "TIGHT")

WIDE_LABELING_GRAPH_COL = 50  # minimum graph columns to use wide spacing for x-axis labels

# used for setting defaults when initializing GraphStats and GraphPanel instances
CONFIG = {"features.graph.height": 7,
          "features.graph.interval": 0,
          "features.graph.bound": 1,
          "features.graph.maxWidth": 150,
          "features.graph.showIntermediateBounds": True}

def loadConfig(config):
  config.update(CONFIG, {
    "features.graph.height": MIN_GRAPH_HEIGHT,
    "features.graph.maxWidth": 1,
    "features.graph.interval": (0, len(UPDATE_INTERVALS) - 1),
    "features.graph.bound": (0, 2)})

class GraphStats(TorCtl.PostEventListener):
  """
  Module that's expected to update dynamically and provide attributes to be
  graphed. Up to two graphs (a 'primary' and 'secondary') can be displayed at a
  time and timescale parameters use the labels defined in UPDATE_INTERVALS.
  """
  
  def __init__(self):
    """
    Initializes parameters needed to present a graph.
    """
    
    TorCtl.PostEventListener.__init__(self)
    
    # panel to be redrawn when updated (set when added to GraphPanel)
    self._graphPanel = None
    self.isSelected = False
    self.isPauseBuffer = False
    
    # tracked stats
    self.tick = 0                                 # number of processed events
    self.lastPrimary, self.lastSecondary = 0, 0   # most recent registered stats
    self.primaryTotal, self.secondaryTotal = 0, 0 # sum of all stats seen
    
    # timescale dependent stats
    self.maxCol = CONFIG["features.graph.maxWidth"]
    self.maxPrimary, self.maxSecondary = {}, {}
    self.primaryCounts, self.secondaryCounts = {}, {}
    
    for i in range(len(UPDATE_INTERVALS)):
      # recent rates for graph
      self.maxPrimary[i] = 0
      self.maxSecondary[i] = 0
      
      # historic stats for graph, first is accumulator
      # iterative insert needed to avoid making shallow copies (nasty, nasty gotcha)
      self.primaryCounts[i] = (self.maxCol + 1) * [0]
      self.secondaryCounts[i] = (self.maxCol + 1) * [0]
    
    # tracks BW events
    torTools.getConn().addEventListener(self)
  
  def clone(self, newCopy=None):
    """
    Provides a deep copy of this instance.
    
    Arguments:
      newCopy - base instance to build copy off of
    """
    
    if not newCopy: newCopy = GraphStats()
    newCopy.tick = self.tick
    newCopy.lastPrimary = self.lastPrimary
    newCopy.lastSecondary = self.lastSecondary
    newCopy.primaryTotal = self.primaryTotal
    newCopy.secondaryTotal = self.secondaryTotal
    newCopy.maxPrimary = dict(self.maxPrimary)
    newCopy.maxSecondary = dict(self.maxSecondary)
    newCopy.primaryCounts = copy.deepcopy(self.primaryCounts)
    newCopy.secondaryCounts = copy.deepcopy(self.secondaryCounts)
    newCopy.isPauseBuffer = True
    return newCopy
  
  def eventTick(self):
    """
    Called when it's time to process another event. All graphs use tor BW
    events to keep in sync with each other (this happens once a second).
    """
    
    pass
  
  def isNextTickRedraw(self):
    """
    Provides true if the following tick (call to _processEvent) will result in
    being redrawn.
    """
    
    if self._graphPanel and self.isSelected and not self._graphPanel.isPaused():
      # use the minimum of the current refresh rate and the panel's
      updateRate = UPDATE_INTERVALS[self._graphPanel.updateInterval][1]
      return (self.tick + 1) % min(updateRate, self.getRefreshRate()) == 0
    else: return False
  
  def getTitle(self, width):
    """
    Provides top label.
    """
    
    return ""
  
  def getHeaderLabel(self, width, isPrimary):
    """
    Provides labeling presented at the top of the graph.
    """
    
    return ""
  
  def getColor(self, isPrimary):
    """
    Provides the color to be used for the graph and stats.
    """
    
    return DEFAULT_COLOR_PRIMARY if isPrimary else DEFAULT_COLOR_SECONDARY
  
  def getContentHeight(self):
    """
    Provides the height content should take up (not including the graph).
    """
    
    return DEFAULT_CONTENT_HEIGHT
  
  def getRefreshRate(self):
    """
    Provides the number of ticks between when the stats have new values to be
    redrawn.
    """
    
    return 1
  
  def isVisible(self):
    """
    True if the stat has content to present, false if it should be hidden.
    """
    
    return True
  
  def draw(self, panel, width, height):
    """
    Allows for any custom drawing monitor wishes to append.
    """
    
    pass
  
  def bandwidth_event(self, event):
    if not self.isPauseBuffer: self.eventTick()
  
  def _processEvent(self, primary, secondary):
    """
    Includes new stats in graphs and notifies associated GraphPanel of changes.
    """
    
    isRedraw = self.isNextTickRedraw()
    
    self.lastPrimary, self.lastSecondary = primary, secondary
    self.primaryTotal += primary
    self.secondaryTotal += secondary
    
    # updates for all time intervals
    self.tick += 1
    for i in range(len(UPDATE_INTERVALS)):
      lable, timescale = UPDATE_INTERVALS[i]
      
      self.primaryCounts[i][0] += primary
      self.secondaryCounts[i][0] += secondary
      
      if self.tick % timescale == 0:
        self.maxPrimary[i] = max(self.maxPrimary[i], self.primaryCounts[i][0] / timescale)
        self.primaryCounts[i][0] /= timescale
        self.primaryCounts[i].insert(0, 0)
        del self.primaryCounts[i][self.maxCol + 1:]
        
        self.maxSecondary[i] = max(self.maxSecondary[i], self.secondaryCounts[i][0] / timescale)
        self.secondaryCounts[i][0] /= timescale
        self.secondaryCounts[i].insert(0, 0)
        del self.secondaryCounts[i][self.maxCol + 1:]
    
    if isRedraw and self._graphPanel: self._graphPanel.redraw(True)

class GraphPanel(panel.Panel):
  """
  Panel displaying a graph, drawing statistics from custom GraphStats
  implementations.
  """
  
  def __init__(self, stdscr):
    panel.Panel.__init__(self, stdscr, "graph", 0)
    self.updateInterval = CONFIG["features.graph.interval"]
    self.bounds = Bounds.values()[CONFIG["features.graph.bound"]]
    self.graphHeight = CONFIG["features.graph.height"]
    self.currentDisplay = None    # label of the stats currently being displayed
    self.stats = {}               # available stats (mappings of label -> instance)
    self.setPauseAttr("stats")
  
  def getUpdateInterval(self):
    """
    Provides the rate that we update the graph at.
    """
    
    return self.updateInterval
  
  def setUpdateInterval(self, updateInterval):
    """
    Sets the rate that we update the graph at.
    
    Arguments:
      updateInterval - update time enum
    """
    
    self.updateInterval = updateInterval
  
  def getBoundsType(self):
    """
    Provides the type of graph bounds used.
    """
    
    return self.bounds
  
  def setBoundsType(self, boundsType):
    """
    Sets the type of graph boundaries we use.
    
    Arguments:
      boundsType - graph bounds enum
    """
    
    self.bounds = boundsType
  
  def getHeight(self):
    """
    Provides the height requested by the currently displayed GraphStats (zero
    if hidden).
    """
    
    if self.currentDisplay and self.stats[self.currentDisplay].isVisible():
      return self.stats[self.currentDisplay].getContentHeight() + self.graphHeight
    else: return 0
  
  def setGraphHeight(self, newGraphHeight):
    """
    Sets the preferred height used for the graph (restricted to the
    MIN_GRAPH_HEIGHT minimum).
    
    Arguments:
      newGraphHeight - new height for the graph
    """
    
    self.graphHeight = max(MIN_GRAPH_HEIGHT, newGraphHeight)
  
  def resizeGraph(self):
    """
    Prompts for user input to resize the graph panel. Options include...
      down arrow - grow graph
      up arrow - shrink graph
      enter / space - set size
    """
    
    control = cli.controller.getController()
    
    panel.CURSES_LOCK.acquire()
    try:
      while True:
        msg = "press the down/up to resize the graph, and enter when done"
        control.setMsg(msg, curses.A_BOLD, True)
        curses.cbreak()
        key = control.getScreen().getch()
        
        if key == curses.KEY_DOWN:
          # don't grow the graph if it's already consuming the whole display
          # (plus an extra line for the graph/log gap)
          maxHeight = self.parent.getmaxyx()[0] - self.top
          currentHeight = self.getHeight()
          
          if currentHeight < maxHeight + 1:
            self.setGraphHeight(self.graphHeight + 1)
        elif key == curses.KEY_UP:
          self.setGraphHeight(self.graphHeight - 1)
        elif uiTools.isSelectionKey(key): break
        
        control.redraw()
    finally:
      control.setMsg()
      panel.CURSES_LOCK.release()
  
  def handleKey(self, key):
    isKeystrokeConsumed = True
    if key == ord('r') or key == ord('R'):
      self.resizeGraph()
    elif key == ord('b') or key == ord('B'):
      # uses the next boundary type
      self.bounds = Bounds.next(self.bounds)
      self.redraw(True)
    elif key == ord('s') or key == ord('S'):
      # provides a menu to pick the graphed stats
      availableStats = self.stats.keys()
      availableStats.sort()
      
      # uses sorted, camel cased labels for the options
      options = ["None"]
      for label in availableStats:
        words = label.split()
        options.append(" ".join(word[0].upper() + word[1:] for word in words))
      
      if self.currentDisplay:
        initialSelection = availableStats.index(self.currentDisplay) + 1
      else: initialSelection = 0
      
      selection = cli.popups.showMenu("Graphed Stats:", options, initialSelection)
      
      # applies new setting
      if selection == 0: self.setStats(None)
      elif selection != -1: self.setStats(availableStats[selection - 1])
    elif key == ord('i') or key == ord('I'):
      # provides menu to pick graph panel update interval
      options = [label for (label, _) in UPDATE_INTERVALS]
      selection = cli.popups.showMenu("Update Interval:", options, self.updateInterval)
      if selection != -1: self.updateInterval = selection
    else: isKeystrokeConsumed = False
    
    return isKeystrokeConsumed
  
  def getHelp(self):
    if self.currentDisplay: graphedStats = self.currentDisplay
    else: graphedStats = "none"
    
    options = []
    options.append(("r", "resize graph", None))
    options.append(("s", "graphed stats", graphedStats))
    options.append(("b", "graph bounds", self.bounds.lower()))
    options.append(("i", "graph update interval", UPDATE_INTERVALS[self.updateInterval][0]))
    return options
  
  def draw(self, width, height):
    """ Redraws graph panel """
    
    if self.currentDisplay:
      param = self.getAttr("stats")[self.currentDisplay]
      graphCol = min((width - 10) / 2, param.maxCol)
      
      primaryColor = uiTools.getColor(param.getColor(True))
      secondaryColor = uiTools.getColor(param.getColor(False))
      
      if self.isTitleVisible(): self.addstr(0, 0, param.getTitle(width), curses.A_STANDOUT)
      
      # top labels
      left, right = param.getHeaderLabel(width / 2, True), param.getHeaderLabel(width / 2, False)
      if left: self.addstr(1, 0, left, curses.A_BOLD | primaryColor)
      if right: self.addstr(1, graphCol + 5, right, curses.A_BOLD | secondaryColor)
      
      # determines max/min value on the graph
      if self.bounds == Bounds.GLOBAL_MAX:
        primaryMaxBound = int(param.maxPrimary[self.updateInterval])
        secondaryMaxBound = int(param.maxSecondary[self.updateInterval])
      else:
        # both Bounds.LOCAL_MAX and Bounds.TIGHT use local maxima
        if graphCol < 2:
          # nothing being displayed
          primaryMaxBound, secondaryMaxBound = 0, 0
        else:
          primaryMaxBound = int(max(param.primaryCounts[self.updateInterval][1:graphCol + 1]))
          secondaryMaxBound = int(max(param.secondaryCounts[self.updateInterval][1:graphCol + 1]))
      
      primaryMinBound = secondaryMinBound = 0
      if self.bounds == Bounds.TIGHT:
        primaryMinBound = int(min(param.primaryCounts[self.updateInterval][1:graphCol + 1]))
        secondaryMinBound = int(min(param.secondaryCounts[self.updateInterval][1:graphCol + 1]))
        
        # if the max = min (ie, all values are the same) then use zero lower
        # bound so a graph is still displayed
        if primaryMinBound == primaryMaxBound: primaryMinBound = 0
        if secondaryMinBound == secondaryMaxBound: secondaryMinBound = 0
      
      # displays upper and lower bounds
      self.addstr(2, 0, "%4i" % primaryMaxBound, primaryColor)
      self.addstr(self.graphHeight + 1, 0, "%4i" % primaryMinBound, primaryColor)
      
      self.addstr(2, graphCol + 5, "%4i" % secondaryMaxBound, secondaryColor)
      self.addstr(self.graphHeight + 1, graphCol + 5, "%4i" % secondaryMinBound, secondaryColor)
      
      # displays intermediate bounds on every other row
      if CONFIG["features.graph.showIntermediateBounds"]:
        ticks = (self.graphHeight - 3) / 2
        for i in range(ticks):
          row = self.graphHeight - (2 * i) - 3
          if self.graphHeight % 2 == 0 and i >= (ticks / 2): row -= 1
          
          if primaryMinBound != primaryMaxBound:
            primaryVal = (primaryMaxBound - primaryMinBound) * (self.graphHeight - row - 1) / (self.graphHeight - 1)
            if not primaryVal in (primaryMinBound, primaryMaxBound): self.addstr(row + 2, 0, "%4i" % primaryVal, primaryColor)
          
          if secondaryMinBound != secondaryMaxBound:
            secondaryVal = (secondaryMaxBound - secondaryMinBound) * (self.graphHeight - row - 1) / (self.graphHeight - 1)
            if not secondaryVal in (secondaryMinBound, secondaryMaxBound): self.addstr(row + 2, graphCol + 5, "%4i" % secondaryVal, secondaryColor)
      
      # creates bar graph (both primary and secondary)
      for col in range(graphCol):
        colCount = int(param.primaryCounts[self.updateInterval][col + 1]) - primaryMinBound
        colHeight = min(self.graphHeight, self.graphHeight * colCount / (max(1, primaryMaxBound) - primaryMinBound))
        for row in range(colHeight): self.addstr(self.graphHeight + 1 - row, col + 5, " ", curses.A_STANDOUT | primaryColor)
        
        colCount = int(param.secondaryCounts[self.updateInterval][col + 1]) - secondaryMinBound
        colHeight = min(self.graphHeight, self.graphHeight * colCount / (max(1, secondaryMaxBound) - secondaryMinBound))
        for row in range(colHeight): self.addstr(self.graphHeight + 1 - row, col + graphCol + 10, " ", curses.A_STANDOUT | secondaryColor)
      
      # bottom labeling of x-axis
      intervalSec = 1 # seconds per labeling
      for i in range(len(UPDATE_INTERVALS)):
        if i == self.updateInterval: intervalSec = UPDATE_INTERVALS[i][1]
      
      intervalSpacing = 10 if graphCol >= WIDE_LABELING_GRAPH_COL else 5
      unitsLabel, decimalPrecision = None, 0
      for i in range((graphCol - 4) / intervalSpacing):
        loc = (i + 1) * intervalSpacing
        timeLabel = uiTools.getTimeLabel(loc * intervalSec, decimalPrecision)
        
        if not unitsLabel: unitsLabel = timeLabel[-1]
        elif unitsLabel != timeLabel[-1]:
          # upped scale so also up precision of future measurements
          unitsLabel = timeLabel[-1]
          decimalPrecision += 1
        else:
          # if constrained on space then strips labeling since already provided
          timeLabel = timeLabel[:-1]
        
        self.addstr(self.graphHeight + 2, 4 + loc, timeLabel, primaryColor)
        self.addstr(self.graphHeight + 2, graphCol + 10 + loc, timeLabel, secondaryColor)
        
      param.draw(self, width, height) # allows current stats to modify the display
  
  def addStats(self, label, stats):
    """
    Makes GraphStats instance available in the panel.
    """
    
    stats._graphPanel = self
    self.stats[label] = stats
  
  def getStats(self):
    """
    Provides the currently selected stats label.
    """
    
    return self.currentDisplay
  
  def setStats(self, label):
    """
    Sets the currently displayed stats instance, hiding panel if None.
    """
    
    if label != self.currentDisplay:
      if self.currentDisplay: self.stats[self.currentDisplay].isSelected = False
      
      if not label:
        self.currentDisplay = None
      elif label in self.stats.keys():
        self.currentDisplay = label
        self.stats[self.currentDisplay].isSelected = True
      else: raise ValueError("Unrecognized stats label: %s" % label)
  
  def copyAttr(self, attr):
    if attr == "stats":
      # uses custom clone method to copy GraphStats instances
      return dict([(key, self.stats[key].clone()) for key in self.stats])
    else: return panel.Panel.copyAttr(self, attr)

