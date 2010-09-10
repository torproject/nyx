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

from util import panel, uiTools

# time intervals at which graphs can be updated
UPDATE_INTERVALS = [("each second", 1), ("5 seconds", 5),   ("30 seconds", 30),
                    ("minutely", 60),   ("15 minute", 900), ("30 minute", 1800),
                    ("hourly", 3600),   ("daily", 86400)]

DEFAULT_CONTENT_HEIGHT = 4 # space needed for labeling above and below the graph
DEFAULT_COLOR_PRIMARY, DEFAULT_COLOR_SECONDARY = "green", "cyan"
MIN_GRAPH_HEIGHT = 1

# enums for graph bounds:
#   BOUNDS_GLOBAL_MAX - global maximum (highest value ever seen)
#   BOUNDS_LOCAL_MAX - local maximum (highest value currently on the graph)
#   BOUNDS_TIGHT - local maximum and minimum
BOUNDS_GLOBAL_MAX, BOUNDS_LOCAL_MAX, BOUNDS_TIGHT = range(3)
BOUND_LABELS = {BOUNDS_GLOBAL_MAX: "global max", BOUNDS_LOCAL_MAX: "local max", BOUNDS_TIGHT: "tight"}

WIDE_LABELING_GRAPH_COL = 50  # minimum graph columns to use wide spacing for x-axis labels

# used for setting defaults when initializing GraphStats and GraphPanel instances
CONFIG = {"features.graph.height": 7, "features.graph.interval": 0, "features.graph.bound": 1, "features.graph.maxWidth": 150, "features.graph.showIntermediateBounds": True, "features.graph.frequentRefresh": True}

def loadConfig(config):
  config.update(CONFIG)
  CONFIG["features.graph.height"] = max(MIN_GRAPH_HEIGHT, CONFIG["features.graph.height"])
  CONFIG["features.graph.maxWidth"] = max(1, CONFIG["features.graph.maxWidth"])
  CONFIG["features.graph.interval"] = min(len(UPDATE_INTERVALS) - 1, max(0, CONFIG["features.graph.interval"]))
  CONFIG["features.graph.bound"] = min(2, max(0, CONFIG["features.graph.bound"]))

class GraphStats(TorCtl.PostEventListener):
  """
  Module that's expected to update dynamically and provide attributes to be
  graphed. Up to two graphs (a 'primary' and 'secondary') can be displayed at a
  time and timescale parameters use the labels defined in UPDATE_INTERVALS.
  """
  
  def __init__(self, isPauseBuffer=False):
    """
    Initializes parameters needed to present a graph.
    """
    
    TorCtl.PostEventListener.__init__(self)
    
    # panel to be redrawn when updated (set when added to GraphPanel)
    self._graphPanel = None
    
    # mirror instance used to track updates when paused
    self.isPaused, self.isPauseBuffer = False, isPauseBuffer
    if isPauseBuffer: self._pauseBuffer = None
    else: self._pauseBuffer = GraphStats(True)
    
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
    
    if self._graphPanel and not self.isPauseBuffer and not self.isPaused:
      if CONFIG["features.graph.frequentRefresh"]: return True
      else:
        updateRate = UPDATE_INTERVALS[self._graphPanel.updateInterval][1]
        if (self.tick + 1) % updateRate == 0: return True
    
    return False
  
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
  
  def setPaused(self, isPause):
    """
    If true, prevents bandwidth updates from being presented. This is a no-op
    if a pause buffer.
    """
    
    if isPause == self.isPaused or self.isPauseBuffer: return
    self.isPaused = isPause
    
    if self.isPaused: active, inactive = self._pauseBuffer, self
    else: active, inactive = self, self._pauseBuffer
    self._parameterSwap(active, inactive)
  
  def bandwidth_event(self, event):
    self.eventTick()
  
  def _parameterSwap(self, active, inactive):
    """
    Either overwrites parameters of pauseBuffer or with the current values or
    vice versa. This is a helper method for setPaused and should be overwritten
    to append with additional parameters that need to be preserved when paused.
    """
    
    # The pause buffer is constructed as a GraphStats instance which will
    # become problematic if this is overridden by any implementations (which
    # currently isn't the case). If this happens then the pause buffer will
    # need to be of the requester's type (not quite sure how to do this
    # gracefully...).
    
    active.tick = inactive.tick
    active.lastPrimary = inactive.lastPrimary
    active.lastSecondary = inactive.lastSecondary
    active.primaryTotal = inactive.primaryTotal
    active.secondaryTotal = inactive.secondaryTotal
    active.maxPrimary = dict(inactive.maxPrimary)
    active.maxSecondary = dict(inactive.maxSecondary)
    active.primaryCounts = copy.deepcopy(inactive.primaryCounts)
    active.secondaryCounts = copy.deepcopy(inactive.secondaryCounts)
  
  def _processEvent(self, primary, secondary):
    """
    Includes new stats in graphs and notifies associated GraphPanel of changes.
    """
    
    if self.isPaused: self._pauseBuffer._processEvent(primary, secondary)
    else:
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
      
      if isRedraw: self._graphPanel.redraw(True)

class GraphPanel(panel.Panel):
  """
  Panel displaying a graph, drawing statistics from custom GraphStats
  implementations.
  """
  
  def __init__(self, stdscr):
    panel.Panel.__init__(self, stdscr, "graph", 0)
    self.updateInterval = CONFIG["features.graph.interval"]
    self.bounds = CONFIG["features.graph.bound"]
    self.graphHeight = CONFIG["features.graph.height"]
    self.currentDisplay = None    # label of the stats currently being displayed
    self.stats = {}               # available stats (mappings of label -> instance)
    self.showLabel = True         # shows top label if true, hides otherwise
    self.isPaused = False
  
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
  
  def draw(self, subwindow, width, height):
    """ Redraws graph panel """
    
    if self.currentDisplay:
      param = self.stats[self.currentDisplay]
      graphCol = min((width - 10) / 2, param.maxCol)
      
      primaryColor = uiTools.getColor(param.getColor(True))
      secondaryColor = uiTools.getColor(param.getColor(False))
      
      if self.showLabel: self.addstr(0, 0, param.getTitle(width), curses.A_STANDOUT)
      
      # top labels
      left, right = param.getHeaderLabel(width / 2, True), param.getHeaderLabel(width / 2, False)
      if left: self.addstr(1, 0, left, curses.A_BOLD | primaryColor)
      if right: self.addstr(1, graphCol + 5, right, curses.A_BOLD | secondaryColor)
      
      # determines max/min value on the graph
      if self.bounds == BOUNDS_GLOBAL_MAX:
        primaryMaxBound = int(param.maxPrimary[self.updateInterval])
        secondaryMaxBound = int(param.maxSecondary[self.updateInterval])
      else:
        # both BOUNDS_LOCAL_MAX and BOUNDS_TIGHT use local maxima
        if graphCol < 2:
          # nothing being displayed
          primaryMaxBound, secondaryMaxBound = 0, 0
        else:
          primaryMaxBound = int(max(param.primaryCounts[self.updateInterval][1:graphCol + 1]))
          secondaryMaxBound = int(max(param.secondaryCounts[self.updateInterval][1:graphCol + 1]))
      
      primaryMinBound = secondaryMinBound = 0
      if self.bounds == BOUNDS_TIGHT:
        primaryMinBound = min(param.primaryCounts[self.updateInterval][1:graphCol + 1])
        secondaryMinBound = min(param.secondaryCounts[self.updateInterval][1:graphCol + 1])
        
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
            primaryVal = (primaryMaxBound - primaryMinBound) / (self.graphHeight - 1) * (self.graphHeight - row - 1)
            self.addstr(row + 2, 0, "%4i" % primaryVal, primaryColor)
          
          if secondaryMinBound != secondaryMaxBound:
            secondaryVal = (secondaryMaxBound - secondaryMinBound) / (self.graphHeight - 1) * (self.graphHeight - row - 1)
            self.addstr(row + 2, graphCol + 5, "%4i" % secondaryVal, secondaryColor)
      
      # creates bar graph (both primary and secondary)
      for col in range(graphCol):
        colCount = param.primaryCounts[self.updateInterval][col + 1] - primaryMinBound
        colHeight = min(self.graphHeight, self.graphHeight * colCount / (max(1, primaryMaxBound) - primaryMinBound))
        for row in range(colHeight): self.addstr(self.graphHeight + 1 - row, col + 5, " ", curses.A_STANDOUT | primaryColor)
        
        colCount = param.secondaryCounts[self.updateInterval][col + 1] - secondaryMinBound
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
    stats.isPaused = True
    self.stats[label] = stats
  
  def setStats(self, label):
    """
    Sets the currently displayed stats instance, hiding panel if None.
    """
    
    if label != self.currentDisplay:
      if self.currentDisplay: self.stats[self.currentDisplay].setPaused(True)
      
      if not label:
        self.currentDisplay = None
      elif label in self.stats.keys():
        self.currentDisplay = label
        self.stats[label].setPaused(self.isPaused)
      else: raise ValueError("Unrecognized stats label: %s" % label)
  
  def setPaused(self, isPause):
    """
    If true, prevents bandwidth updates from being presented.
    """
    
    if isPause == self.isPaused: return
    self.isPaused = isPause
    if self.currentDisplay: self.stats[self.currentDisplay].setPaused(self.isPaused)

