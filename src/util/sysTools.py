"""
Helper functions for working with the underlying system.
"""

import os
import time
import threading

from util import log, procTools, uiTools

# Mapping of commands to if they're available or not. This isn't always
# reliable, failing for some special commands. For these the cache is
# prepopulated to skip lookups.
CMD_AVAILABLE_CACHE = {"ulimit": True}

# cached system call results, mapping the command issued to the (time, results) tuple
CALL_CACHE = {}
IS_FAILURES_CACHED = True           # caches both successful and failed results if true
CALL_CACHE_LOCK = threading.RLock() # governs concurrent modifications of CALL_CACHE

PROCESS_NAME_CACHE = {} # mapping of pids to their process names
PWD_CACHE = {}          # mapping of pids to their present working directory
RESOURCE_TRACKERS = {}  # mapping of pids to their resource tracker instances

# Runtimes for system calls, used to estimate cpu usage. Entries are tuples of
# the form:
# (time called, runtime)
RUNTIMES = []
SAMPLING_PERIOD = 5 # time of the sampling period

CONFIG = {"queries.resourceUsage.rate": 5,
          "cache.sysCalls.size": 600,
          "log.sysCallMade": log.DEBUG,
          "log.sysCallCached": None,
          "log.sysCallFailed": log.INFO,
          "log.sysCallCacheGrowing": log.INFO,
          "log.stats.failedProcResolution": log.DEBUG,
          "log.stats.procResolutionFailover": log.INFO,
          "log.stats.failedPsResolution": log.INFO}

def loadConfig(config):
  config.update(CONFIG)

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

def isAvailable(command, cached=True):
  """
  Checks the current PATH to see if a command is available or not. If a full
  call is provided then this just checks the first command (for instance
  "ls -a | grep foo" is truncated to "ls"). This returns True if an accessible
  executable by the name is found and False otherwise.
  
  Arguments:
    command - command for which to search
    cached  - this makes use of available cached results if true, otherwise
              they're overwritten
  """
  
  if " " in command: command = command.split(" ")[0]
  
  if cached and command in CMD_AVAILABLE_CACHE:
    return CMD_AVAILABLE_CACHE[command]
  else:
    cmdExists = False
    for path in os.environ["PATH"].split(os.pathsep):
      cmdPath = os.path.join(path, command)
      
      if os.path.exists(cmdPath) and os.access(cmdPath, os.X_OK):
        cmdExists = True
        break
    
    CMD_AVAILABLE_CACHE[command] = cmdExists
    return cmdExists

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

def getProcessName(pid, default = None, cacheFailure = True):
  """
  Provides the name associated with the given process id. This isn't available
  on all platforms.
  
  Arguments:
    pid          - process id for the process being returned
    default      - result if the process name can't be retrieved (raises an
                   IOError on failure instead if undefined)
    cacheFailure - if the lookup fails and there's a default then caches the
                   default value to prevent further lookups
  """
  
  if pid in PROCESS_NAME_CACHE:
    return PROCESS_NAME_CACHE[pid]
  
  processName, raisedExc = "", None
  
  # fetch it from proc contents if available
  if procTools.isProcAvailable():
    try:
      processName = procTools.getStats(pid, procTools.Stat.COMMAND)[0]
    except IOError, exc:
      raisedExc = exc
  
  # fall back to querying via ps
  if not processName:
    # the ps call formats results as:
    # COMMAND
    # tor
    psCall = call("ps -p %s -o command" % pid)
    
    if psCall and len(psCall) >= 2 and not " " in psCall[1]:
      processName, raisedExc = psCall[1].strip(), None
    else:
      raisedExc = ValueError("Unexpected output from ps: %s" % psCall)
  
  if raisedExc:
    if default == None: raise raisedExc
    else:
      if cacheFailure:
        PROCESS_NAME_CACHE[pid] = default
      
      return default
  else:
    processName = os.path.basename(processName)
    PROCESS_NAME_CACHE[pid] = processName
    return processName

def getPwd(pid):
  """
  Provices the working directory of the given process. This raises an IOError
  if it can't be determined.
  
  Arguments:
    pid - pid of the process
  """
  
  if not pid: raise IOError("we couldn't get the pid")
  elif pid in PWD_CACHE: return PWD_CACHE[pid]
  
  # try fetching via the proc contents if available
  if procTools.isProcAvailable():
    try:
      pwd = procTools.getPwd(pid)
      PWD_CACHE[pid] = pwd
      return pwd
    except IOError: pass # fall back to pwdx
  elif os.uname()[0] in ("Darwin", "FreeBSD", "OpenBSD"):
    # BSD neither useres the above proc info nor does it have pwdx. Use lsof to
    # determine this instead:
    # https://trac.torproject.org/projects/tor/ticket/4236
    #
    # ~$ lsof -a -p 75717 -d cwd -Fn
    # p75717
    # n/Users/atagar/tor/src/or
    
    try:
      results = call("lsof -a -p %s -d cwd -Fn" % pid)
      
      if results and len(results) == 2 and results[1].startswith("n/"):
        pwd = results[1][1:].strip()
        PWD_CACHE[pid] = pwd
        return pwd
    except IOError, exc: pass
  
  try:
    # pwdx results are of the form:
    # 3799: /home/atagar
    # 5839: No such process
    results = call("pwdx %s" % pid)
    if not results:
      raise IOError("pwdx didn't return any results")
    elif results[0].endswith("No such process"):
      raise IOError("pwdx reported no process for pid " + pid)
    elif len(results) != 1 or results[0].count(" ") != 1:
      raise IOError("we got unexpected output from pwdx")
    else:
      pwd = results[0][results[0].find(" ") + 1:].strip()
      PWD_CACHE[pid] = pwd
      return pwd
  except IOError, exc:
    raise IOError("the pwdx call failed: " + str(exc))

def expandRelativePath(path, ownerPid):
  """
  Expands relative paths to be an absolute path with reference to a given
  process. This raises an IOError if the process pwd is required and can't be
  resolved.
  
  Arguments:
    path     - path to be expanded
    ownerPid - pid of the process to which the path belongs
  """
  
  if not path or path[0] == "/": return path
  else:
    if path.startswith("./"): path = path[2:]
    processPwd = getPwd(ownerPid)
    return "%s/%s" % (processPwd, path)

def call(command, cacheAge=0, suppressExc=False, quiet=True):
  """
  Convenience function for performing system calls, providing:
  - suppression of any writing to stdout, both directing stderr to /dev/null
    and checking for the existence of commands before executing them
  - logging of results (command issued, runtime, success/failure, etc)
  - optional exception suppression and caching (the max age for cached results
    is a minute)
  
  Arguments:
    command     - command to be issued
    cacheAge    - uses cached results rather than issuing a new request if last
                  fetched within this number of seconds (if zero then all
                  caching functionality is skipped)
    suppressExc - provides None in cases of failure if True, otherwise IOErrors
                  are raised
    quiet       - if True, "2> /dev/null" is appended to all commands
  """
  
  # caching functionality (fetching and trimming)
  if cacheAge > 0:
    global CALL_CACHE
    
    # keeps consistency that we never use entries over a minute old (these
    # results are 'dirty' and might be trimmed at any time)
    cacheAge = min(cacheAge, 60)
    cacheSize = CONFIG["cache.sysCalls.size"]
    
    # if the cache is especially large then trim old entries
    if len(CALL_CACHE) > cacheSize:
      CALL_CACHE_LOCK.acquire()
      
      # checks that we haven't trimmed while waiting
      if len(CALL_CACHE) > cacheSize:
        # constructs a new cache with only entries less than a minute old
        newCache, currentTime = {}, time.time()
        
        for cachedCommand, cachedResult in CALL_CACHE.items():
          if currentTime - cachedResult[0] < 60:
            newCache[cachedCommand] = cachedResult
        
        # if the cache is almost as big as the trim size then we risk doing this
        # frequently, so grow it and log
        if len(newCache) > (0.75 * cacheSize):
          cacheSize = len(newCache) * 2
          CONFIG["cache.sysCalls.size"] = cacheSize
          
          msg = "growing system call cache to %i entries" % cacheSize
          log.log(CONFIG["log.sysCallCacheGrowing"], msg)
        
        CALL_CACHE = newCache
      CALL_CACHE_LOCK.release()
    
    # checks if we can make use of cached results
    if command in CALL_CACHE and time.time() - CALL_CACHE[command][0] < cacheAge:
      cachedResults = CALL_CACHE[command][1]
      cacheAge = time.time() - CALL_CACHE[command][0]
      
      if isinstance(cachedResults, IOError):
        if IS_FAILURES_CACHED:
          msg = "system call (cached failure): %s (age: %0.1f, error: %s)" % (command, cacheAge, str(cachedResults))
          log.log(CONFIG["log.sysCallCached"], msg)
          
          if suppressExc: return None
          else: raise cachedResults
        else:
          # flag was toggled after a failure was cached - reissue call, ignoring the cache
          return call(command, 0, suppressExc, quiet)
      else:
        msg = "system call (cached): %s (age: %0.1f)" % (command, cacheAge)
        log.log(CONFIG["log.sysCallCached"], msg)
        
        return cachedResults
  
  startTime = time.time()
  commandCall, results, errorExc = None, None, None
  
  # Gets all the commands involved, taking piping into consideration. If the
  # pipe is quoted (ie, echo "an | example") then it's ignored.
  
  commandComp = []
  for component in command.split("|"):
    if not commandComp or component.count("\"") % 2 == 0:
      commandComp.append(component)
    else:
      # pipe is within quotes
      commandComp[-1] += "|" + component
  
  # preprocessing for the commands to prevent anything going to stdout
  for i in range(len(commandComp)):
    subcommand = commandComp[i].strip()
    
    if not isAvailable(subcommand): errorExc = IOError("'%s' is unavailable" % subcommand.split(" ")[0])
    if quiet: commandComp[i] = "%s 2> /dev/null" % subcommand
  
  # processes the system call
  if not errorExc:
    try:
      commandCall = os.popen(" | ".join(commandComp))
      results = commandCall.readlines()
    except IOError, exc:
      errorExc = exc
  
  # make sure sys call is closed
  if commandCall: commandCall.close()
  
  if errorExc:
    # log failure and either provide None or re-raise exception
    msg = "system call (failed): %s (error: %s)" % (command, str(errorExc))
    log.log(CONFIG["log.sysCallFailed"], msg)
    
    if cacheAge > 0 and IS_FAILURES_CACHED:
      CALL_CACHE_LOCK.acquire()
      CALL_CACHE[command] = (time.time(), errorExc)
      CALL_CACHE_LOCK.release()
    
    if suppressExc: return None
    else: raise errorExc
  else:
    # log call information and if we're caching then save the results
    currentTime = time.time()
    runtime = currentTime - startTime
    msg = "system call: %s (runtime: %0.2f)" % (command, runtime)
    log.log(CONFIG["log.sysCallMade"], msg)
    
    # append the runtime, and remove any outside of the sampling period
    RUNTIMES.append((currentTime, runtime))
    while RUNTIMES and currentTime - RUNTIMES[0][0] > SAMPLING_PERIOD:
      RUNTIMES.pop(0)
    
    if cacheAge > 0:
      CALL_CACHE_LOCK.acquire()
      CALL_CACHE[command] = (time.time(), results)
      CALL_CACHE_LOCK.release()
    
    return results

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
    self._useProc = procTools.isProcAvailable()
    
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
          utime, stime, startTime = procTools.getStats(self.processPid, procTools.Stat.CPU_UTIME, procTools.Stat.CPU_STIME, procTools.Stat.START_TIME)
          totalCpuTime = float(utime) + float(stime)
          cpuDelta = totalCpuTime - self._lastCpuTotal
          newValues["cpuSampling"] = cpuDelta / timeSinceReset
          newValues["cpuAvg"] = totalCpuTime / (time.time() - float(startTime))
          newValues["_lastCpuTotal"] = totalCpuTime
          
          memUsage = int(procTools.getMemoryUsage(self.processPid)[0])
          totalMemory = procTools.getPhysicalMemory()
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
          
          psCall = call("ps -p %s -o cputime,etime,rss,%%mem" % self.processPid)
          
          isSuccessful = False
          if psCall and len(psCall) >= 2:
            stats = psCall[1].strip().split()
            
            if len(stats) == 4:
              try:
                totalCpuTime = uiTools.parseShortTimeLabel(stats[0])
                uptime = uiTools.parseShortTimeLabel(stats[1])
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
            msg = "Failed three attempts to get process resource usage from proc, falling back to ps (%s)" % exc
            log.log(CONFIG["log.stats.procResolutionFailover"], msg)
            
            self._useProc = False
            self._failureCount = 1 # prevents lastQueryFailed() from thinking that we succeeded
          else:
            # wait a bit and try again
            msg = "Unable to query process resource usage from proc (%s)" % exc
            log.log(CONFIG["log.stats.failedProcResolution"], msg)
            self._cond.acquire()
            if not self._halt: self._cond.wait(0.5)
            self._cond.release()
        else:
          # exponential backoff on making failed ps calls
          sleepTime = 0.01 * (2 ** self._failureCount) + self._failureCount
          msg = "Unable to query process resource usage from ps, waiting %0.2f seconds (%s)" % (sleepTime, exc)
          log.log(CONFIG["log.stats.failedProcResolution"], msg)
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

