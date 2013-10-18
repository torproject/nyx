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
import threading

from stem.util import conf, log, system

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

