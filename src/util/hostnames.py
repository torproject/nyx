"""
Service providing hostname resolution via reverse DNS lookups. This provides
both resolution via a thread pool (looking up several addresses at a time) and
caching of the results. If used, it's advisable that this service is stopped
when it's no longer needed. All calls are both non-blocking and thread safe.

Be aware that this relies on querying the system's DNS servers, possibly
leaking the requested addresses to third parties.
"""

# The only points of concern in terms of concurrent calls are the RESOLVER and
# RESOLVER.resolvedCache. This services provides (mostly) non-locking thread
# safety via the following invariants:
# - Resolver and cache instances are non-destructible
#     Nothing can be removed or invalidated. Rather, halting resolvers and
#     trimming the cache are done via reassignment (pointing the RESOLVER or
#     RESOLVER.resolvedCache to another copy).
# - Functions create and use local references to the resolver and its cache
#     This is for consistency (ie, all operations are done on the same resolver
#     or cache instance regardless of concurrent assignments). Usually it's
#     assigned to a local variable called 'resolverRef' or 'cacheRef'.
# - Locks aren't necessary, but used to help in the following cases:
#     - When assigning to the RESOLVER (to avoid orphaned instances with
#       running thread pools).
#     - When adding/removing from the cache (prevents workers from updating
#       an outdated cache reference).

import time
import socket
import threading
import itertools
import Queue
import distutils.sysconfig

from util import log, sysTools

RESOLVER = None                       # hostname resolver (service is stopped if None)
RESOLVER_LOCK = threading.RLock()     # regulates assignment to the RESOLVER
RESOLVER_COUNTER = itertools.count()  # atomic counter, providing the age for new entries (for trimming)
DNS_ERROR_CODES = ("1(FORMERR)", "2(SERVFAIL)", "3(NXDOMAIN)", "4(NOTIMP)", "5(REFUSED)", "6(YXDOMAIN)",
                   "7(YXRRSET)", "8(NXRRSET)", "9(NOTAUTH)", "10(NOTZONE)", "16(BADVERS)")

CONFIG = {"queries.hostnames.poolSize": 5,
          "queries.hostnames.useSocketModule": False,
          "cache.hostnames.size": 700000,
          "cache.hostnames.trimSize": 200000,
          "log.hostnameCacheTrimmed": log.INFO}

def loadConfig(config):
  config.update(CONFIG, {
    "queries.hostnames.poolSize": 1,
    "cache.hostnames.size": 100,
    "cache.hostnames.trimSize": 10})
  
  CONFIG["cache.hostnames.trimSize"] = min(CONFIG["cache.hostnames.trimSize"], CONFIG["cache.hostnames.size"] / 2)

def start():
  """
  Primes the service to start resolving addresses. Calling this explicitly is
  not necessary since resolving any address will start the service if it isn't
  already running.
  """
  
  global RESOLVER
  RESOLVER_LOCK.acquire()
  if not isRunning(): RESOLVER = _Resolver()
  RESOLVER_LOCK.release()

def stop():
  """
  Halts further resolutions and stops the service. This joins on the resolver's
  thread pool and clears its lookup cache.
  """
  
  global RESOLVER
  RESOLVER_LOCK.acquire()
  if isRunning():
    # Releases resolver instance. This is done first so concurrent calls to the
    # service won't try to use it. However, using a halted instance is fine and
    # all calls currently in progress can still proceed on the RESOLVER's local
    # references.
    resolverRef, RESOLVER = RESOLVER, None
    
    # joins on its worker thread pool
    resolverRef.stop()
    for t in resolverRef.threadPool: t.join()
  RESOLVER_LOCK.release()

def setPaused(isPause):
  """
  Allows or prevents further hostname resolutions (resolutions still make use of
  cached entries if available). This starts the service if it isn't already
  running.
  
  Arguments:
    isPause - puts a freeze on further resolutions if true, allows them to
              continue otherwise
  """
  
  # makes sure a running resolver is set with the pausing setting
  RESOLVER_LOCK.acquire()
  start()
  RESOLVER.isPaused = isPause
  RESOLVER_LOCK.release()

def isRunning():
  """
  Returns True if the service is currently running, False otherwise.
  """
  
  return bool(RESOLVER)

def isPaused():
  """
  Returns True if the resolver is paused, False otherwise.
  """
  
  resolverRef = RESOLVER
  if resolverRef: return resolverRef.isPaused
  else: return False

def isResolving():
  """
  Returns True if addresses are currently waiting to be resolved, False
  otherwise.
  """
  
  resolverRef = RESOLVER
  if resolverRef: return not resolverRef.unresolvedQueue.empty()
  else: return False

def resolve(ipAddr, timeout = 0, suppressIOExc = True):
  """
  Provides the hostname associated with a given IP address. By default this is
  a non-blocking call, fetching cached results if available and queuing the
  lookup if not. This provides None if the lookup fails (with a suppressed
  exception) or timeout is reached without resolution. This starts the service
  if it isn't already running.
  
  If paused this simply returns the cached reply (no request is queued and
  returns immediately regardless of the timeout argument).
  
  Requests may raise the following exceptions:
  - ValueError - address was unresolvable (includes the DNS error response)
  - IOError - lookup failed due to os or network issues (suppressed by default)
  
  Arguments:
    ipAddr        - ip address to be resolved
    timeout       - maximum duration to wait for a resolution (blocks to
                    completion if None)
    suppressIOExc - suppresses lookup errors and re-runs failed calls if true,
                    raises otherwise
  """
  
  # starts the service if it isn't already running (making sure we have an
  # instance in a thread safe fashion before continuing)
  resolverRef = RESOLVER
  if resolverRef == None:
    RESOLVER_LOCK.acquire()
    start()
    resolverRef = RESOLVER
    RESOLVER_LOCK.release()
  
  if resolverRef.isPaused:
    # get cache entry, raising if an exception and returning if a hostname
    cacheRef = resolverRef.resolvedCache
    
    if ipAddr in cacheRef:
      entry = cacheRef[ipAddr][0]
      if suppressIOExc and type(entry) == IOError: return None
      elif isinstance(entry, Exception): raise entry
      else: return entry
    else: return None
  elif suppressIOExc:
    # if resolver has cached an IOError then flush the entry (this defaults to
    # suppression since these error may be transient)
    cacheRef = resolverRef.resolvedCache
    flush = ipAddr in cacheRef and type(cacheRef[ipAddr]) == IOError
    
    try: return resolverRef.getHostname(ipAddr, timeout, flush)
    except IOError: return None
  else: return resolverRef.getHostname(ipAddr, timeout)

def getPendingCount():
  """
  Provides an approximate count of the number of addresses still pending
  resolution.
  """
  
  resolverRef = RESOLVER
  if resolverRef: return resolverRef.unresolvedQueue.qsize()
  else: return 0

def getRequestCount():
  """
  Provides the number of resolutions requested since starting the service.
  """
  
  resolverRef = RESOLVER
  if resolverRef: return resolverRef.totalResolves
  else: return 0

def _resolveViaSocket(ipAddr):
  """
  Performs hostname lookup via the socket module's gethostbyaddr function. This
  raises an IOError if the lookup fails (network issue) and a ValueError in
  case of DNS errors (address unresolvable).
  
  Arguments:
    ipAddr - ip address to be resolved
  """
  
  try:
    # provides tuple like: ('localhost', [], ['127.0.0.1'])
    return socket.gethostbyaddr(ipAddr)[0]
  except socket.herror, exc:
    if exc[0] == 2: raise IOError(exc[1]) # "Host name lookup failure"
    else: raise ValueError(exc[1]) # usually "Unknown host"
  except socket.error, exc: raise ValueError(exc[1])

def _resolveViaHost(ipAddr):
  """
  Performs a host lookup for the given IP, returning the resolved hostname.
  This raises an IOError if the lookup fails (os or network issue), and a
  ValueError in the case of DNS errors (address is unresolvable).
  
  Arguments:
    ipAddr - ip address to be resolved
  """
  
  hostname = sysTools.call("host %s" % ipAddr)[0].split()[-1:][0]
  
  if hostname == "reached":
    # got message: ";; connection timed out; no servers could be reached"
    raise IOError("lookup timed out")
  elif hostname in DNS_ERROR_CODES:
    # got error response (can't do resolution on address)
    raise ValueError("address is unresolvable: %s" % hostname)
  else:
    # strips off ending period and returns hostname
    return hostname[:-1]

class _Resolver():
  """
  Performs reverse DNS resolutions. Lookups are a network bound operation so
  this spawns a pool of worker threads to do several at a time in parallel.
  """
  
  def __init__(self):
    # IP Address => (hostname/error, age), resolution failures result in a
    # ValueError with the lookup's status
    self.resolvedCache = {}
    
    self.resolvedLock = threading.RLock() # governs concurrent access when modifying resolvedCache
    self.unresolvedQueue = Queue.Queue()  # unprocessed lookup requests
    self.recentQueries = []               # recent resolution requests to prevent duplicate requests
    self.threadPool = []                  # worker threads that process requests
    self.totalResolves = 0                # counter for the total number of addresses queried to be resolved
    self.isPaused = False                 # prevents further resolutions if true
    self.halt = False                     # if true, tells workers to stop
    self.cond = threading.Condition()     # used for pausing threads
    
    # Determines if resolutions are made using os 'host' calls or python's
    # 'socket.gethostbyaddr'. The following checks if the system has the
    # gethostbyname_r function, which determines if python resolutions can be
    # done in parallel or not. If so, this is preferable.
    isSocketResolutionParallel = distutils.sysconfig.get_config_var("HAVE_GETHOSTBYNAME_R")
    self.useSocketResolution = CONFIG["queries.hostnames.useSocketModule"] and isSocketResolutionParallel
    
    for _ in range(CONFIG["queries.hostnames.poolSize"]):
      t = threading.Thread(target = self._workerLoop)
      t.setDaemon(True)
      t.start()
      self.threadPool.append(t)
  
  def getHostname(self, ipAddr, timeout, flushCache = False):
    """
    Provides the hostname, queuing the request and returning None if the
    timeout is reached before resolution. If a problem's encountered then this
    either raises an IOError (for os and network issues) or ValueError (for DNS
    resolution errors).
    
    Arguments:
      ipAddr     - ip address to be resolved
      timeout    - maximum duration to wait for a resolution (blocks to
                   completion if None)
      flushCache - if true the cache is skipped and address re-resolved
    """
    
    # if outstanding requests are done then clear recentQueries to allow
    # entries removed from the cache to be re-run
    if self.unresolvedQueue.empty(): self.recentQueries = []
    
    # copies reference cache (this is important in case the cache is trimmed
    # during this call)
    cacheRef = self.resolvedCache
    
    if not flushCache and ipAddr in cacheRef:
      # cached response is available - raise if an error, return if a hostname
      response = cacheRef[ipAddr][0]
      if isinstance(response, Exception): raise response
      else: return response
    elif flushCache or ipAddr not in self.recentQueries:
      # new request - queue for resolution
      self.totalResolves += 1
      self.recentQueries.append(ipAddr)
      self.unresolvedQueue.put(ipAddr)
    
    # periodically check cache if requester is willing to wait
    if timeout == None or timeout > 0:
      startTime = time.time()
      
      while timeout == None or time.time() - startTime < timeout:
        if ipAddr in cacheRef:
          # address was resolved - raise if an error, return if a hostname
          response = cacheRef[ipAddr][0]
          if isinstance(response, Exception): raise response
          else: return response
        else: time.sleep(0.1)
    
    return None # timeout reached without resolution
  
  def stop(self):
    """
    Halts further resolutions and terminates the thread.
    """
    
    self.cond.acquire()
    self.halt = True
    self.cond.notifyAll()
    self.cond.release()
  
  def _workerLoop(self):
    """
    Simple producer-consumer loop followed by worker threads. This takes
    addresses from the unresolvedQueue, attempts to look up its hostname, and
    adds its results or the error to the resolved cache. Resolver reference
    provides shared resources used by the thread pool.
    """
    
    while not self.halt:
      # if resolver is paused then put a hold on further resolutions
      if self.isPaused:
        self.cond.acquire()
        if not self.halt: self.cond.wait(1)
        self.cond.release()
        continue
      
      # snags next available ip, timeout is because queue can't be woken up
      # when 'halt' is set
      try: ipAddr = self.unresolvedQueue.get_nowait()
      except Queue.Empty:
        # no elements ready, wait a little while and try again
        self.cond.acquire()
        if not self.halt: self.cond.wait(1)
        self.cond.release()
        continue
      if self.halt: break
      
      try:
        if self.useSocketResolution: result = _resolveViaSocket(ipAddr)
        else: result = _resolveViaHost(ipAddr)
      except IOError, exc: result = exc # lookup failed
      except ValueError, exc: result = exc # dns error
      
      self.resolvedLock.acquire()
      self.resolvedCache[ipAddr] = (result, RESOLVER_COUNTER.next())
      
      # trim cache if excessively large (clearing out oldest entries)
      if len(self.resolvedCache) > CONFIG["cache.hostnames.size"]:
        # Providing for concurrent, non-blocking calls require that entries are
        # never removed from the cache, so this creates a new, trimmed version
        # instead.
        
        # determines minimum age of entries to be kept
        currentCount = RESOLVER_COUNTER.next()
        newCacheSize = CONFIG["cache.hostnames.size"] - CONFIG["cache.hostnames.trimSize"]
        threshold = currentCount - newCacheSize
        newCache = {}
        
        msg = "trimming hostname cache from %i entries to %i" % (len(self.resolvedCache), newCacheSize)
        log.log(CONFIG["log.hostnameCacheTrimmed"], msg)
        
        # checks age of each entry, adding to toDelete if too old
        for ipAddr, entry in self.resolvedCache.iteritems():
          if entry[1] >= threshold: newCache[ipAddr] = entry
        
        self.resolvedCache = newCache
      
      self.resolvedLock.release()
  
