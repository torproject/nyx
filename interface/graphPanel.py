#!/usr/bin/env python
# graphPanel.py -- Graph providing a variety of statistics.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import copy
import curses

import util

MAX_GRAPH_COL = 150  # max columns of data in graph
WIDE_LABELING_GRAPH_COL = 50  # minimum graph columns to use wide spacing for x-axis labels

# enums for graph bounds:
#   BOUNDS_MAX - global maximum (highest value ever seen)
#   BOUNDS_TIGHT - local maximum (highest value currently on the graph)
BOUNDS_MAX, BOUNDS_TIGHT = range(2)
BOUND_LABELS = {BOUNDS_MAX: "max", BOUNDS_TIGHT: "tight"}

# time intervals at which graphs can be updated
DEFAULT_UPDATE_INTERVAL = "5 seconds"
UPDATE_INTERVALS = [("each second", 1),     ("5 seconds", 5),   ("30 seconds", 30),   ("minutely", 60),
                    ("half hour", 1800),    ("hourly", 3600),   ("daily", 86400)]

class GraphStats:
  """
  Module that's expected to update dynamically and provide attributes to be
  graphed. Up to two graphs (a 'primary' and 'secondary') can be displayed at a
  time and timescale parameters use the labels defined in UPDATE_INTERVALS.
  """
  
  def __init__(self):
    """
    Initializes all parameters to dummy values.
    """
    
    self.primaryColor = None    # colors used to draw stats/graphs
    self.secondaryColor = None
    self.height = None          # vertical size of content
    self.graphPanel = None      # panel where stats are drawn (set when added to GraphPanel)
    
    self.isPaused = False
    self.pauseBuffer = None     # mirror instance used to track updates when pauses - 
                                # this is a pauseBuffer instance itself if None
    
    # tracked stats
    self.tick = 0               # number of events processed
    self.lastPrimary = 0        # most recent registered stats
    self.lastSecondary = 0
    self.primaryTotal = 0       # sum of all stats seen
    self.secondaryTotal = 0
    
    # timescale dependent stats
    self.maxPrimary, self.maxSecondary = {}, {}
    self.primaryCounts, self.secondaryCounts = {}, {}
    for (label, timescale) in UPDATE_INTERVALS:
      # recent rates for graph
      self.maxPrimary[label] = 1
      self.maxSecondary[label] = 1
      
      # historic stats for graph, first is accumulator
      # iterative insert needed to avoid making shallow copies (nasty, nasty gotcha)
      self.primaryCounts[label] = (MAX_GRAPH_COL + 1) * [0]
      self.secondaryCounts[label] = (MAX_GRAPH_COL + 1) * [0]
  
  def initialize(self, primaryColor, secondaryColor, height, pauseBuffer=None):
    """
    Initializes newly constructed GraphPanel instance.
    """
    
    # used because of python's inability to have overloaded constructors
    self.primaryColor = primaryColor        # colors used to draw stats/graphs
    self.secondaryColor = secondaryColor
    self.height = height
    
    # mirror instance used to track updates when paused
    if not pauseBuffer: self.pauseBuffer = GraphStats()
    else: self.pauseBuffer = pauseBuffer
  
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
  
  def redraw(self, panel):
    """
    Allows for any custom redrawing monitor wishes to append.
    """
    
    pass
  
  def setPaused(self, isPause):
    """
    If true, prevents bandwidth updates from being presented. This is a no-op
    if a pause buffer.
    """
    
    if isPause == self.isPaused or not self.pauseBuffer: return
    self.isPaused = isPause
    
    if self.isPaused: active, inactive = self.pauseBuffer, self
    else: active, inactive = self, self.pauseBuffer
    self._parameterSwap(active, inactive)
  
  def _parameterSwap(self, active, inactive):
    """
    Either overwrites parameters of pauseBuffer or with the current values or
    vice versa. This is a helper method for setPaused and should be overwritten
    to append with additional parameters that need to be preserved when paused.
    """
    
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
    Includes new stats in graphs and notifies GraphPanel of changes.
    """
    
    if self.isPaused: self.pauseBuffer._processEvent(primary, secondary)
    else:
      self.lastPrimary, self.lastSecondary = primary, secondary
      self.primaryTotal += primary
      self.secondaryTotal += secondary
      
      # updates for all time intervals
      self.tick += 1
      for (label, timescale) in UPDATE_INTERVALS:
        self.primaryCounts[label][0] += primary
        self.secondaryCounts[label][0] += secondary
        
        if self.tick % timescale == 0:
          self.maxPrimary[label] = max(self.maxPrimary[label], self.primaryCounts[label][0] / timescale)
          self.primaryCounts[label][0] /= timescale
          self.primaryCounts[label].insert(0, 0)
          del self.primaryCounts[label][MAX_GRAPH_COL + 1:]
          
          self.maxSecondary[label] = max(self.maxSecondary[label], self.secondaryCounts[label][0] / timescale)
          self.secondaryCounts[label][0] /= timescale
          self.secondaryCounts[label].insert(0, 0)
          del self.secondaryCounts[label][MAX_GRAPH_COL + 1:]
      
      if self.graphPanel: self.graphPanel.redraw()

class GraphPanel(util.Panel):
  """
  Panel displaying a graph, drawing statistics from custom GraphStats
  implementations.
  """
  
  def __init__(self, lock):
    util.Panel.__init__(self, lock, 0) # height is overwritten with current module
    self.updateInterval = DEFAULT_UPDATE_INTERVAL
    self.isPaused = False
    self.showLabel = True         # shows top label if true, hides otherwise
    self.bounds = BOUNDS_TIGHT    # determines bounds on graph
    self.currentDisplay = None    # label of the stats currently being displayed
    self.stats = {}               # available stats (mappings of label -> instance)
  
  def redraw(self):
    """ Redraws graph panel """
    if self.win:
      if not self.lock.acquire(False): return
      try:
        self.clear()
        graphCol = min((self.maxX - 10) / 2, MAX_GRAPH_COL)
        
        if self.currentDisplay:
          param = self.stats[self.currentDisplay]
          primaryColor = util.getColor(param.primaryColor)
          secondaryColor = util.getColor(param.secondaryColor)
          
          if self.showLabel: self.addstr(0, 0, param.getTitle(self.maxX), util.LABEL_ATTR)
          
          # top labels
          left, right = param.getHeaderLabel(self.maxX / 2, True), param.getHeaderLabel(self.maxX / 2, False)
          if left: self.addstr(1, 0, left, curses.A_BOLD | primaryColor)
          if right: self.addstr(1, graphCol + 5, right, curses.A_BOLD | secondaryColor)
          
          # determines max value on the graph
          primaryBound, secondaryBound = -1, -1
          
          if self.bounds == BOUNDS_MAX:
            primaryBound = param.maxPrimary[self.updateInterval]
            secondaryBound = param.maxSecondary[self.updateInterval]
          elif self.bounds == BOUNDS_TIGHT:
            for value in param.primaryCounts[self.updateInterval][1:graphCol + 1]: primaryBound = max(value, primaryBound)
            for value in param.secondaryCounts[self.updateInterval][1:graphCol + 1]: secondaryBound = max(value, secondaryBound)
          
          # displays bound
          self.addstr(2, 0, "%4s" % str(int(primaryBound)), primaryColor)
          self.addstr(7, 0, "   0", primaryColor)
          
          self.addstr(2, graphCol + 5, "%4s" % str(int(secondaryBound)), secondaryColor)
          self.addstr(7, graphCol + 5, "   0", secondaryColor)
          
          # creates bar graph of bandwidth usage over time
          for col in range(graphCol):
            colHeight = min(5, 5 * param.primaryCounts[self.updateInterval][col + 1] / max(1, primaryBound))
            for row in range(colHeight): self.addstr(7 - row, col + 5, " ", curses.A_STANDOUT | primaryColor)
          
          for col in range(graphCol):
            colHeight = min(5, 5 * param.secondaryCounts[self.updateInterval][col + 1] / max(1, secondaryBound))
            for row in range(colHeight): self.addstr(7 - row, col + graphCol + 10, " ", curses.A_STANDOUT | secondaryColor)
          
          # bottom labeling of x-axis
          intervalSec = 1
          for (label, timescale) in UPDATE_INTERVALS:
            if label == self.updateInterval: intervalSec = timescale
          
          intervalSpacing = 10 if graphCol >= WIDE_LABELING_GRAPH_COL else 5
          unitsLabel, decimalPrecision = None, 0
          for i in range(1, (graphCol + intervalSpacing - 4) / intervalSpacing):
            loc = i * intervalSpacing
            timeLabel = util.getTimeLabel(loc * intervalSec, decimalPrecision)
            
            if not unitsLabel: unitsLabel = timeLabel[-1]
            elif unitsLabel != timeLabel[-1]:
              # upped scale so also up precision of future measurements
              unitsLabel = timeLabel[-1]
              decimalPrecision += 1
            else:
              # if constrained on space then strips labeling since already provided
              timeLabel = timeLabel[:-1]
            
            self.addstr(8, 4 + loc, timeLabel, primaryColor)
            self.addstr(8, graphCol + 10 + loc, timeLabel, secondaryColor)
            
          # allows for finishing touches by monitor
          param.redraw(self)
          
        self.refresh()
      finally:
        self.lock.release()
  
  def addStats(self, label, stats):
    """
    Makes GraphStats instance available in the panel.
    """
    
    stats.graphPanel = self
    self.stats[label] = stats
    stats.isPaused = True
  
  def setStats(self, label):
    """
    Sets the current stats instance, hiding panel if None.
    """
    
    if label != self.currentDisplay:
      if self.currentDisplay: self.stats[self.currentDisplay].setPaused(True)
      
      if not label:
        self.currentDisplay = None
        self.height = 0
      elif label in self.stats.keys():
        self.currentDisplay = label
        newStats = self.stats[label]
        self.height = newStats.height
        newStats.setPaused(self.isPaused)
      else: raise ValueError("Unrecognized stats label: %s" % label)
  
  def setPaused(self, isPause):
    """
    If true, prevents bandwidth updates from being presented.
    """
    
    if isPause == self.isPaused: return
    self.isPaused = isPause
    if self.currentDisplay: self.stats[self.currentDisplay].setPaused(self.isPaused)

