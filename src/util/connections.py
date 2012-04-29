"""
Fetches connection data (IP addresses and ports) associated with a given
process. This sort of data can be retrieved via a variety of common *nix
utilities:
- netstat   netstat -np | grep "ESTABLISHED <pid>/<process>"
- sockstat  sockstat | egrep "<process> *<pid>.*ESTABLISHED"
- lsof      lsof -wnPi | egrep "^<process> *<pid>.*((UDP.*)|(\(ESTABLISHED\)))"
- ss        ss -nptu | grep "ESTAB.*\"<process>\",<pid>"

all queries dump its stderr (directing it to /dev/null). Results include UDP
and established TCP connections.

FreeBSD lacks support for the needed netstat flags and has a completely
different program for 'ss'. However, lsof works and there's a couple other
options that perform even better (thanks to Fabian Keil and Hans Schnehl):
- sockstat    sockstat -4c | grep '<process> *<pid>'
- procstat    procstat -f <pid> | grep TCP | grep -v 0.0.0.0:0
"""

import os
import time
import threading

from util import enum, log, procTools, sysTools

# enums for connection resolution utilities
Resolver = enum.Enum(("PROC", "proc"),
                     ("NETSTAT", "netstat"),
                     ("SS", "ss"),
                     ("LSOF", "lsof"),
                     ("SOCKSTAT", "sockstat"),
                     ("BSD_SOCKSTAT", "sockstat (bsd)"),
                     ("BSD_PROCSTAT", "procstat (bsd)"))

# If true this provides new instantiations for resolvers if the old one has
# been stopped. This can make it difficult ensure all threads are terminated
# when accessed concurrently.
RECREATE_HALTED_RESOLVERS = False

# formatted strings for the commands to be executed with the various resolvers
# options are:
# n = prevents dns lookups, p = include process
# output:
# tcp  0  0  127.0.0.1:9051  127.0.0.1:53308  ESTABLISHED 9912/tor
# *note: bsd uses a different variant ('-t' => '-p tcp', but worse an
#   equivilant -p doesn't exist so this can't function)
RUN_NETSTAT = "netstat -np | grep \"ESTABLISHED %s/%s\""

# n = numeric ports, p = include process, t = tcp sockets, u = udp sockets
# output:
# ESTAB  0  0  127.0.0.1:9051  127.0.0.1:53308  users:(("tor",9912,20))
# *note: under freebsd this command belongs to a spreadsheet program
RUN_SS = "ss -nptu | grep \"ESTAB.*\\\"%s\\\",%s\""

# n = prevent dns lookups, P = show port numbers (not names), i = ip only,
# -w = no warnings
# output:
# tor  3873  atagar  45u  IPv4  40994  0t0  TCP 10.243.55.20:45724->194.154.227.109:9001 (ESTABLISHED)
# 
# oddly, using the -p flag via:
# lsof      lsof -nPi -p <pid> | grep "^<process>.*(ESTABLISHED)"
# is much slower (11-28% in tests I ran)
RUN_LSOF = "lsof -wnPi | egrep \"^%s *%s.*((UDP.*)|(\\(ESTABLISHED\\)))\""

# output:
# atagar  tor  3475  tcp4  127.0.0.1:9051  127.0.0.1:38942  ESTABLISHED
# *note: this isn't available by default under ubuntu
RUN_SOCKSTAT = "sockstat | egrep \"%s *%s.*ESTABLISHED\""

RUN_BSD_SOCKSTAT = "sockstat -4c | grep '%s *%s'"
RUN_BSD_PROCSTAT = "procstat -f %s | grep TCP | grep -v 0.0.0.0:0"

RESOLVERS = []                      # connection resolvers available via the singleton constructor
RESOLVER_FAILURE_TOLERANCE = 3      # number of subsequent failures before moving on to another resolver
RESOLVER_SERIAL_FAILURE_MSG = "Unable to query connections with %s, trying %s"
RESOLVER_FINAL_FAILURE_MSG = "All connection resolvers failed"
CONFIG = {"queries.connections.minRate": 5,
          "log.connResolverOptions": log.INFO,
          "log.connLookupFailed": log.INFO,
          "log.connLookupFailover": log.NOTICE,
          "log.connLookupAbandon": log.NOTICE,
          "log.connLookupRateGrowing": None,
          "log.configEntryTypeError": log.NOTICE}

PORT_USAGE = {}

def loadConfig(config):
  config.update(CONFIG)
  
  for configKey in config.getKeys():
    # fetches any port.label.* values
    if configKey.startswith("port.label."):
      portEntry = configKey[11:]
      purpose = config.get(configKey)
      
      divIndex = portEntry.find("-")
      if divIndex == -1:
        # single port
        if portEntry.isdigit():
          PORT_USAGE[portEntry] = purpose
        else:
          msg = "Port value isn't numeric for entry: %s" % configKey
          log.log(CONFIG["log.configEntryTypeError"], msg)
      else:
        try:
          # range of ports (inclusive)
          minPort = int(portEntry[:divIndex])
          maxPort = int(portEntry[divIndex + 1:])
          if minPort > maxPort: raise ValueError()
          
          for port in range(minPort, maxPort + 1):
            PORT_USAGE[str(port)] = purpose
        except ValueError:
          msg = "Unable to parse port range for entry: %s" % configKey
          log.log(CONFIG["log.configEntryTypeError"], msg)

def isValidIpAddress(ipStr):
  """
  Returns true if input is a valid IPv4 address, false otherwise.
  """
  
  # checks if theres four period separated values
  if not ipStr.count(".") == 3: return False
  
  # checks that each value in the octet are decimal values between 0-255
  for ipComp in ipStr.split("."):
    if not ipComp.isdigit() or int(ipComp) < 0 or int(ipComp) > 255:
      return False
  
  return True

def isIpAddressPrivate(ipAddr):
  """
  Provides true if the IP address belongs on the local network or belongs to
  loopback, false otherwise. These include:
  Private ranges: 10.*, 172.16.* - 172.31.*, 192.168.*
  Loopback: 127.*
  
  Arguments:
    ipAddr - IP address to be checked
  """
  
  # checks for any of the simple wildcard ranges
  if ipAddr.startswith("10.") or ipAddr.startswith("192.168.") or ipAddr.startswith("127."):
    return True
  
  # checks for the 172.16.* - 172.31.* range
  if ipAddr.startswith("172.") and ipAddr.count(".") == 3:
    secondOctet = ipAddr[4:ipAddr.find(".", 4)]
    
    if secondOctet.isdigit() and int(secondOctet) >= 16 and int(secondOctet) <= 31:
      return True
  
  return False

def ipToInt(ipAddr):
  """
  Provides an integer representation of the ip address, suitable for sorting.
  
  Arguments:
    ipAddr - ip address to be converted
  """
  
  total = 0
  
  for comp in ipAddr.split("."):
    total *= 255
    total += int(comp)
  
  return total

def getPortUsage(port):
  """
  Provides the common use of a given port. If no useage is known then this
  provides None.
  
  Arguments:
    port - port number to look up
  """
  
  return PORT_USAGE.get(port)

def getResolverCommand(resolutionCmd, processName, processPid = ""):
  """
  Provides the command that would be processed for the given resolver type.
  This raises a ValueError if either the resolutionCmd isn't recognized or a
  pid was requited but not provided.
  
  Arguments:
    resolutionCmd - command to use in resolving the address
    processName   - name of the process for which connections are fetched
    processPid    - process ID (this helps improve accuracy)
  """
  
  if not processPid:
    # the pid is required for procstat resolution
    if resolutionCmd == Resolver.BSD_PROCSTAT:
      raise ValueError("procstat resolution requires a pid")
    
    # if the pid was undefined then match any in that field
    processPid = "[0-9]*"
  
  if resolutionCmd == Resolver.PROC: return ""
  elif resolutionCmd == Resolver.NETSTAT: return RUN_NETSTAT % (processPid, processName)
  elif resolutionCmd == Resolver.SS: return RUN_SS % (processName, processPid)
  elif resolutionCmd == Resolver.LSOF: return RUN_LSOF % (processName, processPid)
  elif resolutionCmd == Resolver.SOCKSTAT: return RUN_SOCKSTAT % (processName, processPid)
  elif resolutionCmd == Resolver.BSD_SOCKSTAT: return RUN_BSD_SOCKSTAT % (processName, processPid)
  elif resolutionCmd == Resolver.BSD_PROCSTAT: return RUN_BSD_PROCSTAT % processPid
  else: raise ValueError("Unrecognized resolution type: %s" % resolutionCmd)

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
  
  if resolutionCmd == Resolver.PROC:
    # Attempts resolution via checking the proc contents.
    if not processPid:
      raise ValueError("proc resolution requires a pid")
    
    try:
      return procTools.getConnections(processPid)
    except Exception, exc:
      raise IOError(str(exc))
  else:
    # Queries a resolution utility (netstat, lsof, etc). This raises an
    # IOError if the command fails or isn't available.
    cmd = getResolverCommand(resolutionCmd, processName, processPid)
    results = sysTools.call(cmd)
    
    if not results: raise IOError("No results found using: %s" % cmd)
    
    # parses results for the resolution command
    conn = []
    for line in results:
      if resolutionCmd == Resolver.LSOF:
        # Different versions of lsof have different numbers of columns, so
        # stripping off the optional 'established' entry so we can just use
        # the last one.
        comp = line.replace("(ESTABLISHED)", "").strip().split()
      else: comp = line.split()
      
      if resolutionCmd == Resolver.NETSTAT:
        localIp, localPort = comp[3].split(":")
        foreignIp, foreignPort = comp[4].split(":")
      elif resolutionCmd == Resolver.SS:
        localIp, localPort = comp[4].split(":")
        foreignIp, foreignPort = comp[5].split(":")
      elif resolutionCmd == Resolver.LSOF:
        local, foreign = comp[-1].split("->")
        localIp, localPort = local.split(":")
        foreignIp, foreignPort = foreign.split(":")
      elif resolutionCmd == Resolver.SOCKSTAT:
        localIp, localPort = comp[4].split(":")
        foreignIp, foreignPort = comp[5].split(":")
      elif resolutionCmd == Resolver.BSD_SOCKSTAT:
        localIp, localPort = comp[5].split(":")
        foreignIp, foreignPort = comp[6].split(":")
      elif resolutionCmd == Resolver.BSD_PROCSTAT:
        localIp, localPort = comp[9].split(":")
        foreignIp, foreignPort = comp[10].split(":")
      
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

def getResolver(processName, processPid = "", alias=None):
  """
  Singleton constructor for resolver instances. If a resolver already exists
  for the process then it's returned. Otherwise one is created and started.
  
  Arguments:
    processName - name of the process being resolved
    processPid  - pid of the process being resolved, if undefined this matches
                  against any resolver with the process name
    alias       - alternative handle under which the resolver can be requested
  """
  
  # check if one's already been created
  requestHandle = alias if alias else processName
  haltedIndex = -1 # old instance of this resolver with the _halt flag set
  for i in range(len(RESOLVERS)):
    resolver = RESOLVERS[i]
    if resolver.handle == requestHandle and (not processPid or resolver.processPid == processPid):
      if resolver._halt and RECREATE_HALTED_RESOLVERS: haltedIndex = i
      else: return resolver
  
  # make a new resolver
  r = ConnectionResolver(processName, processPid, handle = requestHandle)
  r.start()
  
  # overwrites halted instance of this resolver if it exists, otherwise append
  if haltedIndex == -1: RESOLVERS.append(r)
  else: RESOLVERS[haltedIndex] = r
  return r

def getSystemResolvers(osType = None):
  """
  Provides the types of connection resolvers available on this operating
  system.
  
  Arguments:
    osType - operating system type, fetched from the os module if undefined
  """
  
  if osType == None: osType = os.uname()[0]
  
  if osType == "FreeBSD":
    resolvers = [Resolver.BSD_SOCKSTAT, Resolver.BSD_PROCSTAT, Resolver.LSOF]
  elif osType in ("OpenBSD", "Darwin"):
    resolvers = [Resolver.LSOF]
  else:
    resolvers = [Resolver.NETSTAT, Resolver.SOCKSTAT, Resolver.LSOF, Resolver.SS]
  
  # proc resolution, by far, outperforms the others so defaults to this is able
  if procTools.isProcAvailable():
    resolvers = [Resolver.PROC] + resolvers
  
  return resolvers

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
    resolverOptions   - resolvers to be cycled through (differ by os)
    
    * read-only
  """
  
  def __init__(self, processName, processPid = "", resolveRate = None, handle = None):
    """
    Initializes a new resolver daemon. When no longer needed it's suggested
    that this is stopped.
    
    Arguments:
      processName - name of the process being resolved
      processPid  - pid of the process being resolved
      resolveRate - time between resolving connections (in seconds, None if
                    chosen dynamically)
      handle      - name used to query this resolver, this is the processName
                    if undefined
    """
    
    threading.Thread.__init__(self)
    self.setDaemon(True)
    
    self.processName = processName
    self.processPid = processPid
    self.resolveRate = resolveRate
    self.handle = handle if handle else processName
    self.defaultRate = CONFIG["queries.connections.minRate"]
    self.lastLookup = -1
    self.overwriteResolver = None
    self.defaultResolver = Resolver.PROC
    
    osType = os.uname()[0]
    self.resolverOptions = getSystemResolvers(osType)
    
    log.log(CONFIG["log.connResolverOptions"], "Operating System: %s, Connection Resolvers: %s" % (osType, ", ".join(self.resolverOptions)))
    
    # sets the default resolver to be the first found in the system's PATH
    # (left as netstat if none are found)
    for resolver in self.resolverOptions:
      # Resolver strings correspond to their command with the exception of bsd
      # resolvers.
      resolverCmd = resolver.replace(" (bsd)", "")
      
      if resolver == Resolver.PROC or sysTools.isAvailable(resolverCmd):
        self.defaultResolver = resolver
        break
    
    self._connections = []        # connection cache (latest results)
    self._resolutionCounter = 0   # number of successful connection resolutions
    self._isPaused = False
    self._halt = False            # terminates thread if true
    self._cond = threading.Condition()  # used for pausing the thread
    self._subsiquentFailures = 0  # number of failed resolutions with the default in a row
    self._resolverBlacklist = []  # resolvers that have failed to resolve
    
    # Number of sequential times the threshold rate's been too low. This is to
    # avoid having stray spikes up the rate.
    self._rateThresholdBroken = 0
  
  def getOverwriteResolver(self):
    """
    Provides the resolver connection resolution is forced to use. This returns
    None if it's dynamically determined.
    """
    
    return self.overwriteResolver
     
  def setOverwriteResolver(self, overwriteResolver):
    """
    Sets the resolver used for connection resolution, if None then this is
    automatically determined based on what is available.
    
    Arguments:
      overwriteResolver - connection resolver to be used
    """
    
    self.overwriteResolver = overwriteResolver
  
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
        self._resolutionCounter += 1
        
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
      except (ValueError, IOError), exc:
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
            for r in self.resolverOptions:
              if not r in self._resolverBlacklist:
                newResolver = r
                break
            
            if newResolver:
              # provide notice that failures have occurred and resolver is changing
              msg = RESOLVER_SERIAL_FAILURE_MSG % (resolver, newResolver)
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
  
  def getResolutionCount(self):
    """
    Provides the number of successful resolutions so far. This can be used to
    determine if the connection results are new for the caller or not.
    """
    
    return self._resolutionCounter
  
  def getPid(self):
    """
    Provides the pid used to narrow down connection resolution. This is an
    empty string if undefined.
    """
    
    return self.processPid
  
  def setPid(self, processPid):
    """
    Sets the pid used to narrow down connection resultions.
    
    Arguments:
      processPid - pid for the process we're fetching connections for
    """
    
    self.processPid = processPid
  
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

class AppResolver:
  """
  Provides the names and pids of appliations attached to the given ports. This
  stops attempting to query if it fails three times without successfully
  getting lsof results.
  """
  
  def __init__(self, scriptName = "python"):
    """
    Constructs a resolver instance.
    
    Arguments:
      scriptName - name by which to all our own entries
    """
    
    self.scriptName = scriptName
    self.queryResults = {}
    self.resultsLock = threading.RLock()
    self._cond = threading.Condition()  # used for pausing when waiting for results
    self.isResolving = False  # flag set if we're in the process of making a query
    self.failureCount = 0     # -1 if we've made a successful query
  
  def getResults(self, maxWait=0):
    """
    Provides the last queried results. If we're in the process of making a
    query then we can optionally block for a time to see if it finishes.
    
    Arguments:
      maxWait - maximum second duration to block on getting results before
                returning
    """
    
    self._cond.acquire()
    if self.isResolving and maxWait > 0:
      self._cond.wait(maxWait)
    self._cond.release()
    
    self.resultsLock.acquire()
    results = dict(self.queryResults)
    self.resultsLock.release()
    
    return results
  
  def resolve(self, ports):
    """
    Queues the given listing of ports to be resolved. This clears the last set
    of results when completed.
    
    Arguments:
      ports - list of ports to be resolved to applications
    """
    
    if self.failureCount < 3:
      self.isResolving = True
      t = threading.Thread(target = self._queryApplications, kwargs = {"ports": ports})
      t.setDaemon(True)
      t.start()
  
  def _queryApplications(self, ports=[]):
    """
    Performs an lsof lookup on the given ports to get the command/pid tuples.
    
    Arguments:
      ports - list of ports to be resolved to applications
    """
    
    # atagar@fenrir:~/Desktop/arm$ lsof -i tcp:51849 -i tcp:37277
    # COMMAND  PID   USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
    # tor     2001 atagar   14u  IPv4  14048      0t0  TCP localhost:9051->localhost:37277 (ESTABLISHED)
    # tor     2001 atagar   15u  IPv4  22024      0t0  TCP localhost:9051->localhost:51849 (ESTABLISHED)
    # python  2462 atagar    3u  IPv4  14047      0t0  TCP localhost:37277->localhost:9051 (ESTABLISHED)
    # python  3444 atagar    3u  IPv4  22023      0t0  TCP localhost:51849->localhost:9051 (ESTABLISHED)
    
    if not ports:
      self.resultsLock.acquire()
      self.queryResults = {}
      self.isResolving = False
      self.resultsLock.release()
      
      # wakes threads waiting on results
      self._cond.acquire()
      self._cond.notifyAll()
      self._cond.release()
      
      return
    
    results = {}
    lsofArgs = []
    
    # Uses results from the last query if we have any, otherwise appends the
    # port to the lsof command. This has the potential for persisting dirty
    # results but if we're querying by the dynamic port on the local tcp
    # connections then this should be very rare (and definitely worth the
    # chance of being able to skip an lsof query altogether).
    for port in ports:
      if port in self.queryResults:
        results[port] = self.queryResults[port]
      else: lsofArgs.append("-i tcp:%s" % port)
    
    if lsofArgs:
      lsofResults = sysTools.call("lsof -nP " + " ".join(lsofArgs))
    else: lsofResults = None
    
    if not lsofResults and self.failureCount != -1:
      # lsof query failed and we aren't yet sure if it's possible to
      # successfully get results on this platform
      self.failureCount += 1
      self.isResolving = False
      return
    elif lsofResults:
      # (iPort, oPort) tuple for our own process, if it was fetched
      ourConnection = None
      
      for line in lsofResults:
        lineComp = line.split()
        
        if len(lineComp) == 10 and lineComp[9] == "(ESTABLISHED)":
          cmd, pid, _, _, _, _, _, _, portMap, _ = lineComp
          
          if "->" in portMap:
            iPort, oPort = portMap.split("->")
            iPort = iPort.split(":")[1]
            oPort = oPort.split(":")[1]
            
            # entry belongs to our own process
            if pid == str(os.getpid()):
              cmd = self.scriptName
              ourConnection = (iPort, oPort)
            
            if iPort.isdigit() and oPort.isdigit():
              newEntry = (iPort, oPort, cmd, pid)
              
              # adds the entry under the key of whatever we queried it with
              # (this might be both the inbound _and_ outbound ports)
              for portMatch in (iPort, oPort):
                if portMatch in ports:
                  if portMatch in results:
                    results[portMatch].append(newEntry)
                  else: results[portMatch] = [newEntry]
      
      # making the lsof call generated an extraneous sh entry for our own connection
      if ourConnection:
        for ourPort in ourConnection:
          if ourPort in results:
            shIndex = None
            
            for i in range(len(results[ourPort])):
              if results[ourPort][i][2] == "sh":
                shIndex = i
                break
            
            if shIndex != None:
              del results[ourPort][shIndex]
    
    self.resultsLock.acquire()
    self.failureCount = -1
    self.queryResults = results
    self.isResolving = False
    self.resultsLock.release()
    
    # wakes threads waiting on results
    self._cond.acquire()
    self._cond.notifyAll()
    self._cond.release()

