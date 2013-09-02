"""
Helper functions for working with the underlying system.
"""

import os
import time
import threading

from stem.util import conf, log, proc, str_tools, system

RESOURCE_TRACKERS = {}  # mapping of pids to their resource tracker instances

# Runtimes for system calls, used to estimate cpu usage. Entries are tuples of
# the form:
# (time called, runtime)
RUNTIMES = []
SAMPLING_PERIOD = 5 # time of the sampling period

CONFIG = conf.config_dict("arm", {
  "queries.resourceUsage.rate": 5,
})

# TODO: This was a bit of a hack, and one that won't work now that we lack our
# call() method to populate RUNTIMES.

def getSysCpuUsage():
  """
  Provides an estimate of the cpu usage for system calls made through this
  module, based on a sampling period of five seconds. The os.times() function,
  unfortunately, doesn't seem to take popen calls into account. This returns a
  float representing the percentage used.
  """
  
  currentTime = time.time()
  
  # removes any runtimes outside of our sampling period
  while RUNTIMES and currentTime - RUNTIMES[0][0] > SAMPLING_PERIOD:
    RUNTIMES.pop(0)
  
  runtimeSum = sum([entry[1] for entry in RUNTIMES])
  return runtimeSum / SAMPLING_PERIOD

def getFileErrorMsg(exc):
  """
  Strips off the error number prefix for file related IOError messages. For
  instance, instead of saying:
  [Errno 2] No such file or directory
  
  this would return:
  no such file or directory
  
  Arguments:
    exc - file related IOError exception
  """
  
  excStr = str(exc)
  if excStr.startswith("[Errno ") and "] " in excStr:
    excStr = excStr[excStr.find("] ") + 2:].strip()
    excStr = excStr[0].lower() + excStr[1:]
  
  return excStr

def getResourceTracker(pid, noSpawn = False):
  """
  Provides a running singleton ResourceTracker instance for the given pid.
  
  Arguments:
    pid     - pid of the process being tracked
    noSpawn - returns None rather than generating a singleton instance if True
  """
  
  if pid in RESOURCE_TRACKERS:
    tracker = RESOURCE_TRACKERS[pid]
    if tracker.isAlive(): return tracker
    else: del RESOURCE_TRACKERS[pid]
  
  if noSpawn: return None
  tracker = ResourceTracker(pid, CONFIG["queries.resourceUsage.rate"])
  RESOURCE_TRACKERS[pid] = tracker
  tracker.start()
  return tracker

class ResourceTracker(threading.Thread):
  """
  Periodically fetches the resource usage (cpu and memory usage) for a given
  process.
  """
  
  def __init__(self, processPid, resolveRate):
    """
    Initializes a new resolver daemon. When no longer needed it's suggested
    that this is stopped.
    
    Arguments:
      processPid  - pid of the process being tracked
      resolveRate - time between resolving resource usage, resolution is
                    disabled if zero
    """
    
    threading.Thread.__init__(self)
    self.setDaemon(True)
    
    self.processPid = processPid
    self.resolveRate = resolveRate
    
    self.cpuSampling = 0.0  # latest cpu usage sampling
    self.cpuAvg = 0.0       # total average cpu usage
    self.memUsage = 0       # last sampled memory usage in bytes
    self.memUsagePercentage = 0.0 # percentage cpu usage
    
    # resolves usage via proc results if true, ps otherwise
    self._useProc = proc.is_available()
    
    # used to get the deltas when querying cpu time
    self._lastCpuTotal = 0
    
    self.lastLookup = -1
    self._halt = False      # terminates thread if true
    self._valLock = threading.RLock()
    self._cond = threading.Condition()  # used for pausing the thread
    
    # number of successful calls we've made
    self._runCount = 0
    
    # sequential times we've failed with this method of resolution
    self._failureCount = 0
  
  def getResourceUsage(self):
    """
    Provides the last cached resource usage as a tuple of the form:
    (cpuUsage_sampling, cpuUsage_avg, memUsage_bytes, memUsage_percent)
    """
    
    self._valLock.acquire()
    results = (self.cpuSampling, self.cpuAvg, self.memUsage, self.memUsagePercentage)
    self._valLock.release()
    
    return results
  
  def getRunCount(self):
    """
    Provides the number of times we've successfully fetched the resource
    usages.
    """
    
    return self._runCount
  
  def lastQueryFailed(self):
    """
    Provides true if, since we fetched the currently cached results, we've
    failed to get new results. False otherwise.
    """
    
    return self._failureCount != 0
  
  def run(self):
    while not self._halt:
      timeSinceReset = time.time() - self.lastLookup
      
      if self.resolveRate == 0:
        self._cond.acquire()
        if not self._halt: self._cond.wait(0.2)
        self._cond.release()
        
        continue
      elif timeSinceReset < self.resolveRate:
        sleepTime = max(0.2, self.resolveRate - timeSinceReset)
        
        self._cond.acquire()
        if not self._halt: self._cond.wait(sleepTime)
        self._cond.release()
        
        continue # done waiting, try again
      
      newValues = {}
      try:
        if self._useProc:
          utime, stime, startTime = proc.get_stats(self.processPid, proc.Stat.CPU_UTIME, proc.Stat.CPU_STIME, proc.Stat.START_TIME)
          totalCpuTime = float(utime) + float(stime)
          cpuDelta = totalCpuTime - self._lastCpuTotal
          newValues["cpuSampling"] = cpuDelta / timeSinceReset
          newValues["cpuAvg"] = totalCpuTime / (time.time() - float(startTime))
          newValues["_lastCpuTotal"] = totalCpuTime
          
          memUsage = int(proc.get_memory_usage(self.processPid)[0])
          totalMemory = proc.get_physical_memory()
          newValues["memUsage"] = memUsage
          newValues["memUsagePercentage"] = float(memUsage) / totalMemory
        else:
          # the ps call formats results as:
          # 
          #     TIME     ELAPSED   RSS %MEM
          # 3-08:06:32 21-00:00:12 121844 23.5
          # 
          # or if Tor has only recently been started:
          # 
          #     TIME      ELAPSED    RSS %MEM
          #  0:04.40        37:57  18772  0.9
          
          psCall = system.call("ps -p %s -o cputime,etime,rss,%%mem" % self.processPid)
          
          isSuccessful = False
          if psCall and len(psCall) >= 2:
            stats = psCall[1].strip().split()
            
            if len(stats) == 4:
              try:
                totalCpuTime = str_tools.parse_short_time_label(stats[0])
                uptime = str_tools.parse_short_time_label(stats[1])
                cpuDelta = totalCpuTime - self._lastCpuTotal
                newValues["cpuSampling"] = cpuDelta / timeSinceReset
                newValues["cpuAvg"] = totalCpuTime / uptime
                newValues["_lastCpuTotal"] = totalCpuTime
                
                newValues["memUsage"] = int(stats[2]) * 1024 # ps size is in kb
                newValues["memUsagePercentage"] = float(stats[3]) / 100.0
                isSuccessful = True
              except ValueError, exc: pass
          
          if not isSuccessful:
            raise IOError("unrecognized output from ps: %s" % psCall)
      except IOError, exc:
        newValues = {}
        self._failureCount += 1
        
        if self._useProc:
          if self._failureCount >= 3:
            # We've failed three times resolving via proc. Warn, and fall back
            # to ps resolutions.
            log.info("Failed three attempts to get process resource usage from proc, falling back to ps (%s)" % exc)
            
            self._useProc = False
            self._failureCount = 1 # prevents lastQueryFailed() from thinking that we succeeded
          else:
            # wait a bit and try again
            log.debug("Unable to query process resource usage from proc (%s)" % exc)
            self._cond.acquire()
            if not self._halt: self._cond.wait(0.5)
            self._cond.release()
        else:
          # exponential backoff on making failed ps calls
          sleepTime = 0.01 * (2 ** self._failureCount) + self._failureCount
          log.debug("Unable to query process resource usage from ps, waiting %0.2f seconds (%s)" % (sleepTime, exc))
          self._cond.acquire()
          if not self._halt: self._cond.wait(sleepTime)
          self._cond.release()
      
      # sets the new values
      if newValues:
        # If this is the first run then the cpuSampling stat is meaningless
        # (there isn't a previous tick to sample from so it's zero at this
        # point). Setting it to the average, which is a fairer estimate.
        if self.lastLookup == -1:
          newValues["cpuSampling"] = newValues["cpuAvg"]
        
        self._valLock.acquire()
        self.cpuSampling = newValues["cpuSampling"]
        self.cpuAvg = newValues["cpuAvg"]
        self.memUsage = newValues["memUsage"]
        self.memUsagePercentage = newValues["memUsagePercentage"]
        self._lastCpuTotal = newValues["_lastCpuTotal"]
        self.lastLookup = time.time()
        self._runCount += 1
        self._failureCount = 0
        self._valLock.release()
  
  def stop(self):
    """
    Halts further resolutions and terminates the thread.
    """
    
    self._cond.acquire()
    self._halt = True
    self._cond.notifyAll()
    self._cond.release()

