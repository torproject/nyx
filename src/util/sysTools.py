"""
Helper functions for working with the underlying system.
"""

import os
import time
import threading

from util import log, procTools

# mapping of commands to if they're available or not
CMD_AVAILABLE_CACHE = {}

# cached system call results, mapping the command issued to the (time, results) tuple
CALL_CACHE = {}
IS_FAILURES_CACHED = True           # caches both successful and failed results if true
CALL_CACHE_LOCK = threading.RLock() # governs concurrent modifications of CALL_CACHE

PROCESS_NAME_CACHE = {} # mapping of pids to their process names

CONFIG = {"cache.sysCalls.size": 600,
          "log.sysCallMade": log.DEBUG,
          "log.sysCallCached": None,
          "log.sysCallFailed": log.INFO,
          "log.sysCallCacheGrowing": log.INFO}

def loadConfig(config):
  config.update(CONFIG)

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
  Provides the name associated with the given process id. This is platform
  specific and only implemented for Linux.
  
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
  if os.uname()[0] == "Linux":
    try:
      processName = procTools.getStats(pid, procTools.STAT_COMMAND)[0]
    except IOError, exc:
      raisedExc = exc
  
  if raisedExc:
    if default == None: raise raisedExc
    else:
      if cacheFailure:
        PROCESS_NAME_CACHE[pid] = default
      
      return default
  else:
    PROCESS_NAME_CACHE[pid] = processName
    return processName

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
    msg = "system call: %s (runtime: %0.2f)" % (command, time.time() - startTime)
    log.log(CONFIG["log.sysCallMade"], msg)
    
    if cacheAge > 0:
      CALL_CACHE_LOCK.acquire()
      CALL_CACHE[command] = (time.time(), results)
      CALL_CACHE_LOCK.release()
    
    return results

