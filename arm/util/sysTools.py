"""
Helper functions for working with the underlying system.
"""

import collections
import time
import threading

import arm.util.tracker

from stem.util import conf, log, proc, str_tools, system

RESOURCE_TRACKERS = {}  # mapping of pids to their resource tracker instances

# Runtimes for system calls, used to estimate cpu usage. Entries are tuples of
# the form:
# (time called, runtime)
RUNTIMES = []
SAMPLING_PERIOD = 5 # time of the sampling period

# Process resources we poll...
#
#  cpu_sample - average cpu usage since we last checked
#  cpu_average - average cpu usage since we first started tracking the process
#  memory_bytes - memory usage of the process in bytes
#  memory_precent - percentage of our memory used by this process

Resources = collections.namedtuple('Resources', [
  'cpu_sample',
  'cpu_average',
  'memory_bytes',
  'memory_percent',
])

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
  tracker = ResourceTracker(pid)
  RESOURCE_TRACKERS[pid] = tracker
  tracker.start()
  return tracker

class ResourceTracker(arm.util.tracker.Daemon):
  """
  Periodically fetches the resource usage (cpu and memory usage) for a given
  process.
  """

  def __init__(self, processPid):
    """
    Initializes a new resolver daemon. When no longer needed it's suggested
    that this is stopped.

    Arguments:
      processPid  - pid of the process being tracked
    """

    arm.util.tracker.Daemon.__init__(self, CONFIG["queries.resourceUsage.rate"])

    self.processPid = processPid

    self._last_sample = None

    # resolves usage via proc results if true, ps otherwise
    self._useProc = proc.is_available()

    # used to get the deltas when querying cpu time
    self._lastCpuTotal = 0

    self.lastLookup = -1
    self._valLock = threading.RLock()

    # sequential times we've failed with this method of resolution
    self._failureCount = 0

  def getResourceUsage(self):
    """
    Provides the last cached resource usage as a named tuple of the form:
    (cpuUsage_sampling, cpuUsage_avg, memUsage_bytes, memUsage_percent)
    """

    self._valLock.acquire()

    if self._last_sample is None:
      result = Resources(0.0, 0.0, 0, 0.0)
    else:
      result = self._last_sample
    self._valLock.release()

    return result

  def lastQueryFailed(self):
    """
    Provides true if, since we fetched the currently cached results, we've
    failed to get new results. False otherwise.
    """

    return self._failureCount != 0

  def task(self):
    timeSinceReset = time.time() - self.lastLookup

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
      else:
        # exponential backoff on making failed ps calls
        sleepTime = 0.01 * (2 ** self._failureCount) + self._failureCount
        log.debug("Unable to query process resource usage from ps, waiting %0.2f seconds (%s)" % (sleepTime, exc))

    # sets the new values
    if newValues:
      # If this is the first run then the cpuSampling stat is meaningless
      # (there isn't a previous tick to sample from so it's zero at this
      # point). Setting it to the average, which is a fairer estimate.
      if self.lastLookup == -1:
        newValues["cpuSampling"] = newValues["cpuAvg"]

      self._valLock.acquire()
      self._last_sample = Resources(newValues["cpuSampling"], newValues["cpuAvg"], newValues["memUsage"], newValues["memUsagePercentage"])
      self._lastCpuTotal = newValues["_lastCpuTotal"]
      self.lastLookup = time.time()
      self._failureCount = 0
      self._valLock.release()
      return True
    else:
      return False
