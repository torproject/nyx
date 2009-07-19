#!/usr/bin/env python
# hostnameResolver.py -- Background thread for performing reverse DNS resolution.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import os
import time
import itertools
import Queue
from threading import Thread

RESOLVER_THREAD_POOL_SIZE = 5     # upping to around 30 causes the program to intermittently seize
RESOLVER_MAX_CACHE_SIZE = 5000
RESOLVER_CACHE_TRIM_SIZE = 2000   # entries removed when max cache size reached
DNS_ERROR_CODES = ("1(FORMERR)", "2(SERVFAIL)", "3(NXDOMAIN)", "4(NOTIMP)", "5(REFUSED)", "6(YXDOMAIN)", "7(YXRRSET)", "8(NXRRSET)", "9(NOTAUTH)", "10(NOTZONE)", "16(BADVERS)")

class HostnameResolver(Thread):
  """
  Background thread that quietly performs reverse DNS lookup of address with
  caching. This is non-blocking, providing None in the case of errors or
  new requests.
  """
  
  # Resolutions are made using os 'host' calls as opposed to 'gethostbyaddr' in
  # the socket module because the later appears to be a blocking call (ie, serial
  # requests which vastly reduces performance). In theory this shouldn't be the
  # case if your system has the gethostbyname_r function, which you can check
  # for with:
  # import distutils.sysconfig
  # distutils.sysconfig.get_config_var("HAVE_GETHOSTBYNAME_R")
  # however, I didn't find this to be the case. As always, suggestions welcome!
  
  def __init__(self):
    Thread.__init__(self)
    self.resolvedCache = {}           # IP Address => (hostname, age) (None if couldn't be resolved)
    self.unresolvedQueue = Queue.Queue()
    self.recentQueries = []           # recent resolution requests to prevent duplicate requests
    self.counter = itertools.count()  # atomic counter to track age of entries (for trimming)
    self.threadPool = []              # worker threads that process requests
    self.totalResolves = 0            # counter for the total number of addresses querried to be resolved
    self.isPaused = True
    
    for i in range(RESOLVER_THREAD_POOL_SIZE):
      t = _ResolverWorker(self.resolvedCache, self.unresolvedQueue, self.counter)
      t.setDaemon(True)
      t.setPaused(self.isPaused)
      t.start()
      self.threadPool.append(t)
  
  def resolve(self, ipAddr, blockTime = 0):
    """
    Provides hostname associated with an IP address. If not found this returns
    None and performs a reverse DNS lookup for future reference. This also
    provides None if the address couldn't be resolved. This can be made to block
    if some delay is tolerable.
    """
    
    # if outstanding requests are done then clear recentQueries so we can run erronious requests again
    if self.unresolvedQueue.empty(): self.recentQueries = []
    
    if ipAddr in self.resolvedCache.keys():
      return self.resolvedCache[ipAddr][0]
    elif ipAddr not in self.recentQueries:
      self.totalResolves += 1
      self.recentQueries.append(ipAddr)
      self.unresolvedQueue.put(ipAddr)
      
      if len(self.resolvedCache) > RESOLVER_MAX_CACHE_SIZE:
        # trims cache (clean out oldest entries)
        currentCount = self.counter.next()
        threshold = currentCount - (RESOLVER_MAX_CACHE_SIZE - RESOLVER_CACHE_TRIM_SIZE) # max count of entries being removed
        toDelete = []
        
        for (entryAddr, (entryHostname, entryAge)) in self.resolvedCache:
          if entryAge < threshold: toDelete.append(entryAddr)
        
        for entryAddr in toDelete: del self.resolvedCache[entryAddr]
      
      if blockTime > 0 and not self.isPaused:
        timeWaited = 0
        
        while ipAddr not in self.resolvedCache.keys() and timeWaited < blockTime:
          time.sleep(0.1)
          timeWaited += 0.1
        
        if ipAddr in self.resolvedCache.keys(): return self.resolvedCache[ipAddr][0]
        else: return None
  
  def setPaused(self, isPause):
    """
    If true, prevents further dns requests.
    """
    
    if isPause == self.isPaused: return
    self.isPaused = isPause
    for t in self.threadPool: t.setPaused(self.isPaused)

class _ResolverWorker(Thread):
  """
  Helper thread for HostnameResolver, performing lookups on unresolved IP
  addresses and adding the results to the resolvedCache.
  """
  
  def __init__(self, resolvedCache, unresolvedQueue, counter):
    Thread.__init__(self)
    self.resolvedCache = resolvedCache
    self.unresolvedQueue = unresolvedQueue
    self.counter = counter
    self.isPaused = False
  
  def run(self):
    while True:
      while self.isPaused: time.sleep(1)
      
      ipAddr = self.unresolvedQueue.get() # snag next available ip
      resolutionFailed = False            # if true don't cache results
      hostCall = os.popen("host %s" % ipAddr)
      
      try:
        hostname = hostCall.read().split()[-1:][0]
        
        if hostname == "reached":
          # got message: ";; connection timed out; no servers could be reached"
          resolutionFailed = True
        elif hostname in DNS_ERROR_CODES:
          # got error response (can't do resolution on address)
          hostname = None
        else:
          # strips off ending period
          hostname = hostname[:-1]
      except IOError: resolutionFailed = True # host call failed
      
      hostCall.close()
      if not resolutionFailed: self.resolvedCache[ipAddr] = (hostname, self.counter.next())
      self.unresolvedQueue.task_done() # signals that job's done
  
  def setPaused(self, isPause):
    """
    Puts further work on hold if true.
    """
    
    self.isPaused = isPause

