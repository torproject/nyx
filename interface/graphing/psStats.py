"""
Tracks configured ps stats. If non-numeric then this fails, providing a blank
graph. By default this provides the cpu and memory usage of the tor process.
"""

import graphPanel
from util import log, sysTools, torTools, uiTools

# number of subsequent failed queries before giving up
FAILURE_THRESHOLD = 5

# attempts to use cached results from the header panel's ps calls
HEADER_PS_PARAM = ["%cpu", "rss", "%mem", "etime"]

DEFAULT_CONFIG = {"features.graph.ps.primaryStat": "%cpu", "features.graph.ps.secondaryStat": "rss", "features.graph.ps.cachedOnly": True, "log.graph.ps.invalidStat": log.WARN, "log.graph.ps.abandon": log.WARN}

class PsStats(graphPanel.GraphStats):
  """
  Tracks ps stats, defaulting to system resource usage (cpu and memory usage).
  """
  
  def __init__(self, config=None):
    graphPanel.GraphStats.__init__(self)
    self.failedCount = 0      # number of subsequent failed queries
    
    self._config = dict(DEFAULT_CONFIG)
    if config: config.update(self._config)
    
    self.queryPid = torTools.getConn().getMyPid()
    self.queryParam = [self._config["features.graph.ps.primaryStat"], self._config["features.graph.ps.secondaryStat"]]
    
    # If we're getting the same stats as the header panel then issues identical
    # queries to make use of cached results. If not, then disable cache usage.
    if self.queryParam[0] in HEADER_PS_PARAM and self.queryParam[1] in HEADER_PS_PARAM:
      self.queryParam = list(HEADER_PS_PARAM)
    else: self._config["features.graph.ps.cachedOnly"] = False
    
    # strips any empty entries
    while "" in self.queryParam: self.queryParam.remove("")
    
    self.cacheTime = 3600 if self._config["features.graph.ps.cachedOnly"] else 1
  
  def getTitle(self, width):
    return "System Resources:"
  
  def getHeaderLabel(self, width, isPrimary):
    avg = (self.primaryTotal if isPrimary else self.secondaryTotal) / max(1, self.tick)
    lastAmount = self.lastPrimary if isPrimary else self.lastSecondary
    
    if isPrimary: statName = self._config["features.graph.ps.primaryStat"]
    else: statName = self._config["features.graph.ps.secondaryStat"]
    
    # provides nice labels for failures and common stats
    if not statName or self.failedCount >= FAILURE_THRESHOLD or not statName in self.queryParam:
      return ""
    elif statName == "%cpu":
      return "CPU (%s%%, avg: %0.1f%%):" % (lastAmount, avg)
    elif statName in ("rss", "size"):
      # memory sizes are converted from MB to B before generating labels
      statLabel = "Memory" if statName == "rss" else "Size"
      usageLabel = uiTools.getSizeLabel(lastAmount * 1048576, 1)
      avgLabel = uiTools.getSizeLabel(avg * 1048576, 1)
      return "%s (%s, avg: %s):" % (statLabel, usageLabel, avgLabel)
    else:
      # generic label (first letter of stat name is capitalized)
      statLabel = statName[0].upper() + statName[1:]
      return "%s (%s, avg: %s):" % (statLabel, lastAmount, avg)
  
  def getPreferredHeight(self):
    # hides graph if there's nothing to display (provides default otherwise)
    # provides default height unless there's nothing to 
    if self.queryPid and self.queryParam and self.failedCount < FAILURE_THRESHOLD:
      return graphPanel.DEFAULT_HEIGHT
    else: return 0
  
  def eventTick(self):
    """
    Processes a ps event.
    """
    
    psResults = {} # mapping of stat names to their results
    if self.queryPid and self.queryParam and self.failedCount < FAILURE_THRESHOLD:
      queryCmd = "ps -p %s -o %s" % (self.queryPid, ",".join(self.queryParam))
      psCall = sysTools.call(queryCmd, self.cacheTime, True)
      
      if psCall and len(psCall) == 2:
        # ps provided results (first line is headers, second is stats)
        stats = psCall[1].strip().split()
        
        if len(self.queryParam) == len(stats):
          # we have a result to match each stat - constructs mapping
          psResults = dict([(self.queryParam[i], stats[i]) for i in range(len(stats))])
          self.failedCount = 0 # had a successful call - reset failure count
      
      if not psResults:
        # ps call failed, if we fail too many times sequentially then abandon
        # listing (probably due to invalid ps parameters)
        self.failedCount += 1
        
        if self.failedCount == FAILURE_THRESHOLD:
          msg = "failed several attempts to query '%s', abandoning ps graph" % queryCmd
          log.log(self._config["log.graph.ps.abandon"], msg)
    
    # if something fails (no pid, ps call failed, etc) then uses last results
    primary, secondary = self.lastPrimary, self.lastSecondary
    
    for isPrimary in (True, False):
      if isPrimary: statName = self._config["features.graph.ps.primaryStat"]
      else: statName = self._config["features.graph.ps.secondaryStat"]
      
      if statName in psResults:
        try:
          result = float(psResults[statName])
          
          # The 'rss' and 'size' parameters provide memory usage in KB. This is
          # scaled up to MB so the graph's y-high is a reasonable value.
          if statName in ("rss", "size"): result /= 1024.0
          
          if isPrimary: primary = result
          else: secondary = result
        except ValueError:
          if self.queryParam != HEADER_PS_PARAM:
            # custom stat provides non-numeric results - give a warning and stop querying it
            msg = "unable to use non-numeric ps stat '%s' for graphing" % statName
            log.log(self._config["log.graph.ps.invalidStat"], msg)
            self.queryParam.remove(statName)
    
    self._processEvent(primary, secondary)

