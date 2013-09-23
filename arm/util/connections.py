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

from stem.util import conf, connection, log, system

RESOLVER = None

def conf_handler(key, value):
  if key.startswith("port.label."):
    portEntry = key[11:]

    divIndex = portEntry.find("-")
    if divIndex == -1:
      # single port
      if portEntry.isdigit():
        PORT_USAGE[portEntry] = value
      else:
        msg = "Port value isn't numeric for entry: %s" % key
        log.notice(msg)
    else:
      try:
        # range of ports (inclusive)
        minPort = int(portEntry[:divIndex])
        maxPort = int(portEntry[divIndex + 1:])
        if minPort > maxPort: raise ValueError()

        for port in range(minPort, maxPort + 1):
          PORT_USAGE[str(port)] = value
      except ValueError:
        msg = "Unable to parse port range for entry: %s" % key
        log.notice(msg)

CONFIG = conf.config_dict('arm', {
  'queries.connections.minRate': 5,
  'msg.unable_to_use_resolver': '',
  'msg.unable_to_use_all_resolvers': '',
}, conf_handler)

PORT_USAGE = {}

def getPortUsage(port):
  """
  Provides the common use of a given port. If no useage is known then this
  provides None.

  Arguments:
    port - port number to look up
  """

  return PORT_USAGE.get(port)

def get_resolver():
  """
  Singleton constructor for a connection resolver for tor's process.
  """

  global RESOLVER

  if RESOLVER is None:
    RESOLVER = ConnectionResolver()

  return RESOLVER

class Daemon(threading.Thread):
  """
  Daemon that can perform a unit of work at a given rate.
  """

  def __init__(self, rate):
    threading.Thread.__init__(self)
    self.daemon = True

    self._rate = rate
    self._last_ran = -1  # time when we last ran

    self._is_paused = False
    self._halt = False  # terminates thread if true
    self._cond = threading.Condition()  # used for pausing the thread

  def run(self):
    while not self._halt:
      time_since_last_ran = time.time() - self._last_ran

      if self._is_paused or time_since_last_ran < self._rate:
        sleep_duration = max(0.2, self._rate - time_since_last_ran)

        self._cond.acquire()
        if not self._halt:
          self._cond.wait(sleep_duration)
        self._cond.release()

        continue  # done waiting, try again

      self.task()
      self._last_ran = time.time()

  def task(self):
    """
    Task the resolver is meant to perform. This should be implemented by
    subclasses.
    """

    pass

  def get_rate(self):
    """
    Provides the rate at which we perform our given task.

    :returns: **float** for the rate in seconds at which we perform our task
    """

    return self._rate

  def set_rate(self, rate):
    """
    Sets the rate at which we perform our task in seconds.

    :param float rate: rate at which to perform work in seconds
    """

    self._rate = rate

  def set_paused(self, pause):
    """
    Either resumes or holds off on doing further work.

    :param bool pause: halts work if **True**, resumes otherwise
    """

    self._is_paused = pause

  def stop(self):
    """
    Halts further work and terminates the thread.
    """

    self._cond.acquire()
    self._halt = True
    self._cond.notifyAll()
    self._cond.release()


class ConnectionResolver(Daemon):
  """
  Daemon that periodically retrieves the connections made by a process.
  """

  def __init__(self):
    Daemon.__init__(self, CONFIG["queries.connections.minRate"])

    self._resolvers = connection.get_system_resolvers()
    self._connections = []
    self._custom_resolver = None
    self._resolution_counter = 0  # number of successful connection resolutions

    self._process_pid = None
    self._process_name = None

    # Number of times in a row we've either failed with our current resolver or
    # concluded that our rate is too low.

    self._failure_count = 0
    self._rate_too_low_count = 0

  def task(self):
    if self._custom_resolver:
      resolver = self._custom_resolver
      is_default_resolver = False
    elif self._resolvers:
      resolver = self._resolvers[0]
      is_default_resolver = True
    else:
      return  # nothing to resolve with

    try:
      start_time = time.time()
      self._connections = connection.get_connections(resolver, process_pid = self._process_pid, process_name = self._process_name)
      runtime = time.time() - start_time

      self._resolution_counter += 1

      if is_default_resolver:
        self._failure_count = 0

      # Reduce our rate if connection resolution is taking a long time. This is
      # most often an issue for extremely busy relays.

      min_rate = 100 * runtime

      if self.get_rate() < min_rate:
        self._rate_too_low_count += 1

        if self._rate_too_low_count >= 3:
          min_rate += 1  # little extra padding so we don't frequently update this
          self.set_rate(min_rate)
          self._rate_too_low_count = 0
          log.trace("connection lookup time increasing to %0.1f seconds per call" % min_rate)
      else:
        self._rate_too_low_count = 0
    except IOError as exc:
      log.info(exc)

      # Fail over to another resolver if we've repeatedly been unable to use
      # this one.

      if is_default_resolver:
        self._failure_count += 1

        if self._failure_count >= 3:
          self._resolvers.pop()
          self._failure_count = 0

          if self._resolvers:
            log.notice(CONFIG['msg.unable_to_use_resolver'].format(
              old_resolver = resolver,
              new_resolver = self._resolvers[0],
            ))
          else:
            log.notice(CONFIG['msg.unable_to_use_all_resolvers'])

  def set_process(self, pid, name):
    """
    Sets the process we retrieve connections for.

    :param int pid: process id
    :param str name: name of the process
    """

    self._process_pid = pid
    self._process_name = name

  def get_custom_resolver(self):
    """
    Provides the custom resolver the user has selected. This is **None** if
    we're picking resolvers dynamically.

    :returns: :data:`stem.util.connection.Resolver` we're overwritten to use
    """

    return self._custom_resolver

  def set_custom_resolver(self, resolver):
    """
    Sets the resolver used for connection resolution. If **None** then this is
    automatically determined based on what is available.

    :param stem.util.connection.Resolver resolver: resolver to use
    """

    self._custom_resolver = resolver

  def get_connections(self):
    """
    Provides the last queried connection results, an empty list if resolver
    has been stopped.

    :returns: **list** of :class:`~stem.util.connection.Connection` we last retrieved
    """

    if self._halt:
      return []
    else:
      return list(self._connections)

  def get_resolution_count(self):
    """
    Provides the number of successful resolutions so far. This can be used to
    determine if the connection results are new for the caller or not.
    """

    return self._resolution_counter


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
      lsofResults = system.call("lsof -nP " + " ".join(lsofArgs))
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

