"""
Fetches connection data (IP addresses and ports) associated with a given
process. This sort of data can be retrieved via a variety of common *nix
utilities:
- netstat   netstat -npt | grep <pid>/<process>
- ss        ss -p | grep "\"<process>\",<pid>"
- lsof      lsof -nPi | grep "<process>\s*<pid>.*(ESTABLISHED)"

all queries dump its stderr (directing it to /dev/null). Unfortunately FreeBSD
lacks support for the needed netstat flags and has a completely different
program for 'ss', so this is quite likely to fail there.
"""

import sys
import time
import threading

from util import log, sysTools

# enums for connection resolution utilities
CMD_NETSTAT, CMD_SS, CMD_LSOF = range(1, 4)
CMD_STR = {CMD_NETSTAT: "netstat", CMD_SS: "ss", CMD_LSOF: "lsof"}

# If true this provides new instantiations for resolvers if the old one has
# been stopped. This can make it difficult ensure all threads are terminated
# when accessed concurrently.
RECREATE_HALTED_RESOLVERS = False

# formatted strings for the commands to be executed with the various resolvers
# options are:
# n = prevents dns lookups, p = include process, t = tcp only
# output:
# tcp  0  0  127.0.0.1:9051  127.0.0.1:53308  ESTABLISHED 9912/tor
# *note: bsd uses a different variant ('-t' => '-p tcp', but worse an
#   equivilant -p doesn't exist so this can't function)
RUN_NETSTAT = "netstat -npt | grep %s/%s"

# n = numeric ports, p = include process
# output:
# ESTAB  0  0  127.0.0.1:9051  127.0.0.1:53308  users:(("tor",9912,20))
# *note: under freebsd this command belongs to a spreadsheet program
RUN_SS = "ss -np | grep \"\\\"%s\\\",%s\""

# n = prevent dns lookups, P = show port numbers (not names), i = ip only
# output:
# tor  9912  atagar  20u  IPv4  33453  TCP 127.0.0.1:9051->127.0.0.1:53308
RUN_LSOF = "lsof -nPi | grep \"%s\s*%s.*(ESTABLISHED)\""

RESOLVERS = []                      # connection resolvers available via the singleton constructor
RESOLVER_FAILURE_TOLERANCE = 3      # number of subsequent failures before moving on to another resolver
RESOLVER_SERIAL_FAILURE_MSG = "Querying connections with %s failed, trying %s"
RESOLVER_FINAL_FAILURE_MSG = "All connection resolvers failed"
CONFIG = {"queries.connections.minRate": 5,
          "log.connLookupFailed": log.INFO,
          "log.connLookupFailover": log.NOTICE,
          "log.connLookupAbandon": log.WARN,
          "log.connLookupRateGrowing": None}

def loadConfig(config):
  config.update(CONFIG)

def getConnections(resolutionCmd, processName, processPid = ""):
  """
  Retrieves a list of the current connections for a given process, providing a
  tuple list of the form:
  [(local_ipAddr1, local_port1, foreign_ipAddr1, foreign_port1), ...]
  this raises an IOError if no connections are available or resolution fails
  (in most cases these appear identical). Common issues include:
    - insufficient permissions
    - resolution command is unavailable
    - usage of the command is non-standard (particularly an issue for BSD)
  
  Arguments:
    resolutionCmd - command to use in resolving the address
    processName   - name of the process for which connections are fetched
    processPid    - process ID (this helps improve accuracy)
  """
  
  if resolutionCmd == CMD_NETSTAT: cmd = RUN_NETSTAT % (processPid, processName)
  elif resolutionCmd == CMD_SS: cmd = RUN_SS % (processName, processPid)
  else: cmd = RUN_LSOF % (processName, processPid)
  
  # raises an IOError if the command fails or isn't available
  results = sysTools.call(cmd)
  
  if not results: raise IOError("No results found using: %s" % cmd)
  
  # parses results for the resolution command
  conn = []
  for line in results:
    comp = line.split()
    
    if resolutionCmd == CMD_NETSTAT or resolutionCmd == CMD_SS:
      localIp, localPort = comp[3].split(":")
      foreignIp, foreignPort = comp[4].split(":")
    else:
      local, foreign = comp[8].split("->")
      localIp, localPort = local.split(":")
      foreignIp, foreignPort = foreign.split(":")
    
    conn.append((localIp, localPort, foreignIp, foreignPort))
  
  return conn

def isResolverAlive(processName, processPid = ""):
  """
  This provides true if a singleton resolver instance exists for the given
  process/pid combination, false otherwise.
  
  Arguments:
    processName - name of the process being checked
    processPid  - pid of the process being checked, if undefined this matches
                  against any resolver with the process name
  """
  
  for resolver in RESOLVERS:
    if not resolver._halt and resolver.processName == processName and (not processPid or resolver.processPid == processPid):
      return True
  
  return False

def getResolver(processName, processPid = ""):
  """
  Singleton constructor for resolver instances. If a resolver already exists
  for the process then it's returned. Otherwise one is created and started.
  
  Arguments:
    processName - name of the process being resolved
    processPid  - pid of the process being resolved, if undefined this matches
                  against any resolver with the process name
  """
  
  # check if one's already been created
  haltedIndex = -1 # old instance of this resolver with the _halt flag set
  for i in range(len(RESOLVERS)):
    resolver = RESOLVERS[i]
    if resolver.processName == processName and (not processPid or resolver.processPid == processPid):
      if resolver._halt and RECREATE_HALTED_RESOLVERS: haltedIndex = i
      else: return resolver
  
  # make a new resolver
  r = ConnectionResolver(processName, processPid)
  r.start()
  
  # overwrites halted instance of this resolver if it exists, otherwise append
  if haltedIndex == -1: RESOLVERS.append(r)
  else: RESOLVERS[haltedIndex] = r
  return r

def test():
  # quick method for testing connection resolution
  userInput = raw_input("Enter query (<ss, netstat, lsof> PROCESS_NAME [PID]): ").split()
  
  # checks if there's enough arguments
  if len(userInput) == 0: sys.exit(0)
  elif len(userInput) == 1:
    print "no process name provided"
    sys.exit(1)
  
  # translates resolver string to enum
  userInput[0] = userInput[0].lower()
  if userInput[0] == "ss": userInput[0] = CMD_SS
  elif userInput[0] == "netstat": userInput[0] = CMD_NETSTAT
  elif userInput[0] == "lsof": userInput[0] = CMD_LSOF
  else:
    print "unrecognized type of resolver: %s" % userInput[2]
    sys.exit(1)
  
  # resolves connections
  try:
    if len(userInput) == 2: connections = getConnections(userInput[0], userInput[1])
    else: connections = getConnections(userInput[0], userInput[1], userInput[2])
  except IOError, exc:
    print exc
    sys.exit(1)
  
  # prints results
  print "-" * 40
  for lIp, lPort, fIp, fPort in connections:
    print "%s:%s -> %s:%s" % (lIp, lPort, fIp, fPort)

class ConnectionResolver(threading.Thread):
  """
  Service that periodically queries for a process' current connections. This
  provides several benefits over on-demand queries:
  - queries are non-blocking (providing cached results)
  - falls back to use different resolution methods in case of repeated failures
  - avoids overly frequent querying of connection data, which can be demanding
    in terms of system resources
  
  Unless an overriding method of resolution is requested this defaults to
  choosing a resolver the following way:
  
  - Checks the current PATH to determine which resolvers are available. This
    uses the first of the following that's available:
      netstat, ss, lsof (picks netstat if none are found)
  
  - Attempts to resolve using the selection. Single failures are logged at the
    INFO level, and a series of failures at NOTICE. In the later case this
    blacklists the resolver, moving on to the next. If all resolvers fail this
    way then resolution's abandoned and logs a WARN message.
  
  The time between resolving connections, unless overwritten, is set to be
  either five seconds or ten times the runtime of the resolver (whichever is
  larger). This is to prevent systems either strapped for resources or with a
  vast number of connections from being burdened too heavily by this daemon.
  
  Parameters:
    processName       - name of the process being resolved
    processPid        - pid of the process being resolved
    resolveRate       - minimum time between resolving connections (in seconds,
                        None if using the default)
    * defaultRate     - default time between resolving connections
    lastLookup        - time connections were last resolved (unix time, -1 if
                        no resolutions have yet been successful)
    overwriteResolver - method of resolution (uses default if None)
    * defaultResolver - resolver used by default (None if all resolution
                        methods have been exhausted)
    
    * read-only
  """
  
  def __init__(self, processName, processPid = "", resolveRate = None):
    """
    Initializes a new resolver daemon. When no longer needed it's suggested
    that this is stopped.
    
    Arguments:
      processName - name of the process being resolved
      processPid  - pid of the process being resolved
      resolveRate - time between resolving connections (in seconds, None if
                    chosen dynamically)
    """
    
    threading.Thread.__init__(self)
    self.setDaemon(True)
    
    self.processName = processName
    self.processPid = processPid
    self.resolveRate = resolveRate
    self.defaultRate = CONFIG["queries.connections.minRate"]
    self.lastLookup = -1
    self.overwriteResolver = None
    self.defaultResolver = CMD_NETSTAT
    
    # sets the default resolver to be the first found in the system's PATH
    # (left as netstat if none are found)
    for resolver in [CMD_NETSTAT, CMD_SS, CMD_LSOF]:
      if sysTools.isAvailable(CMD_STR[resolver]):
        self.defaultResolver = resolver
        break
    
    self._connections = []        # connection cache (latest results)
    self._isPaused = False
    self._halt = False            # terminates thread if true
    self._cond = threading.Condition()  # used for pausing the thread
    self._subsiquentFailures = 0  # number of failed resolutions with the default in a row
    self._resolverBlacklist = []  # resolvers that have failed to resolve
    
    # Number of sequential times the threshold rate's been too low. This is to
    # avoid having stray spikes up the rate.
    self._rateThresholdBroken = 0
  
  def run(self):
    while not self._halt:
      minWait = self.resolveRate if self.resolveRate else self.defaultRate
      timeSinceReset = time.time() - self.lastLookup
      
      if self._isPaused or timeSinceReset < minWait:
        sleepTime = max(0.2, minWait - timeSinceReset)
        
        self._cond.acquire()
        if not self._halt: self._cond.wait(sleepTime)
        self._cond.release()
        
        continue # done waiting, try again
      
      isDefault = self.overwriteResolver == None
      resolver = self.defaultResolver if isDefault else self.overwriteResolver
      
      # checks if there's nothing to resolve with
      if not resolver:
        self.lastLookup = time.time() # avoids a busy wait in this case
        continue
      
      try:
        resolveStart = time.time()
        connResults = getConnections(resolver, self.processName, self.processPid)
        lookupTime = time.time() - resolveStart
        
        self._connections = connResults
        
        newMinDefaultRate = 100 * lookupTime
        if self.defaultRate < newMinDefaultRate:
          if self._rateThresholdBroken >= 3:
            # adding extra to keep the rate from frequently changing
            self.defaultRate = newMinDefaultRate + 0.5
            
            msg = "connection lookup time increasing to %0.1f seconds per call" % self.defaultRate
            log.log(CONFIG["log.connLookupRateGrowing"], msg)
          else: self._rateThresholdBroken += 1
        else: self._rateThresholdBroken = 0
        
        if isDefault: self._subsiquentFailures = 0
      except IOError, exc:
        # this logs in a couple of cases:
        # - special failures noted by getConnections (most cases are already
        # logged via sysTools)
        # - note fail-overs for default resolution methods
        if str(exc).startswith("No results found using:"):
          log.log(CONFIG["log.connLookupFailed"], str(exc))
        
        if isDefault:
          self._subsiquentFailures += 1
          
          if self._subsiquentFailures >= RESOLVER_FAILURE_TOLERANCE:
            # failed several times in a row - abandon resolver and move on to another
            self._resolverBlacklist.append(resolver)
            self._subsiquentFailures = 0
            
            # pick another (non-blacklisted) resolver
            newResolver = None
            for r in [CMD_NETSTAT, CMD_SS, CMD_LSOF]:
              if not r in self._resolverBlacklist:
                newResolver = r
                break
            
            if newResolver:
              # provide notice that failures have occurred and resolver is changing
              msg = RESOLVER_SERIAL_FAILURE_MSG % (CMD_STR[resolver], CMD_STR[newResolver])
              log.log(CONFIG["log.connLookupFailover"], msg)
            else:
              # exhausted all resolvers, give warning
              log.log(CONFIG["log.connLookupAbandon"], RESOLVER_FINAL_FAILURE_MSG)
            
            self.defaultResolver = newResolver
      finally:
        self.lastLookup = time.time()
  
  def getConnections(self):
    """
    Provides the last queried connection results, an empty list if resolver
    has been halted.
    """
    
    if self._halt: return []
    else: return list(self._connections)
  
  def setPaused(self, isPause):
    """
    Allows or prevents further connection resolutions (this still makes use of
    cached results).
    
    Arguments:
      isPause - puts a freeze on further resolutions if true, allows them to
                continue otherwise
    """
    
    if isPause == self._isPaused: return
    self._isPaused = isPause
  
  def stop(self):
    """
    Halts further resolutions and terminates the thread.
    """
    
    self._cond.acquire()
    self._halt = True
    self._cond.notifyAll()
    self._cond.release()

