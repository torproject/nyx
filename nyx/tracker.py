# Copyright 2013-2017, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Background tasks for gathering information about the tor process.

::

  get_connection_tracker - provides a ConnectionTracker for our tor process
  get_resource_tracker - provides a ResourceTracker for our tor process
  get_port_usage_tracker - provides a PortUsageTracker for our system
  get_consensus_tracker - provides a ConsensusTracker for our tor process

  stop_trackers - halts any active trackers

  Daemon - common parent for resolvers
    |- ConnectionTracker - periodically checks the connections established by tor
    |  |- get_custom_resolver - provide the custom conntion resolver we're using
    |  |- set_custom_resolver - overwrites automatic resolver selecion with a custom resolver
    |  +- get_value - provides our latest connection results
    |
    |- ResourceTracker - periodically checks the resource usage of tor
    |  +- get_value - provides our latest resource usage results
    |
    |- PortUsageTracker - provides information about port usage on the local system
    |  +- get_processes_using_ports - mapping of ports to the processes using it
    |
    |- run_counter - number of successful runs
    |- get_rate - provides the rate at which we run
    |- set_rate - sets the rate at which we run
    |- set_paused - pauses or continues work
    +- stop - stops further work by the daemon

  ConsensusTracker - performant lookups for consensus related information
    |- update - updates the consensus information we're based on
    |- my_router_status_entry - provides the router status entry for ourselves
    |- get_relay_nickname - provides the nickname for a given relay
    |- get_relay_fingerprints - provides relays running at a location
    +- get_relay_address - provides the address a relay is running at

.. data:: Resources

  Resource usage information retrieved about the tor process.

  :var float cpu_sample: average cpu usage since we last checked
  :var float cpu_average: average cpu usage since we first started tracking the process
  :var float cpu_total: total cpu time the process has used since starting
  :var int memory_bytes: memory usage of the process in bytes
  :var float memory_percent: percentage of our memory used by this process
  :var float timestamp: unix timestamp for when this information was fetched
"""

import collections
import os
import sys
import time
import threading

import stem.control
import stem.descriptor.router_status_entry
import stem.util.log

from nyx import tor_controller
from stem.util import conf, connection, enum, proc, str_tools, system

CONFIG = conf.config_dict('nyx', {
  'connection_rate': 5,
  'resource_rate': 5,
  'port_usage_rate': 5,
})

UNABLE_TO_USE_ANY_RESOLVER_MSG = """
We were unable to use any of your system's resolvers to get tor's connections.
This is fine, but means that the connections page will be empty. This is
usually permissions related so if you would like to fix this then run nyx with
the same user as tor (ie, "sudo -u <tor user> nyx").
""".strip()

CONNECTION_TRACKER = None
RESOURCE_TRACKER = None
PORT_USAGE_TRACKER = None
CONSENSUS_TRACKER = None

CustomResolver = enum.Enum(
  ('INFERENCE', 'by inference'),
)

# Extending stem's Connection tuple with attributes for the uptime of the
# connection.

Connection = collections.namedtuple('Connection', [
  'start_time',
  'is_legacy',  # boolean to indicate if the connection predated us
] + list(stem.util.connection.Connection._fields))

Resources = collections.namedtuple('Resources', [
  'cpu_sample',
  'cpu_average',
  'cpu_total',
  'memory_bytes',
  'memory_percent',
  'timestamp',
])

Process = collections.namedtuple('Process', [
  'pid',
  'name',
])


class UnresolvedResult(Exception):
  'Indicates the application being used by a port is still being determined.'


class UnknownApplication(Exception):
  'No application could be determined for this port.'


def get_connection_tracker():
  """
  Singleton for tracking the connections established by tor.
  """

  global CONNECTION_TRACKER

  if CONNECTION_TRACKER is None:
    CONNECTION_TRACKER = ConnectionTracker(CONFIG['connection_rate'])
    CONNECTION_TRACKER.start()

  return CONNECTION_TRACKER


def get_resource_tracker():
  """
  Singleton for tracking the resource usage of our tor process.
  """

  global RESOURCE_TRACKER

  if RESOURCE_TRACKER is None:
    RESOURCE_TRACKER = ResourceTracker(CONFIG['resource_rate'])
    RESOURCE_TRACKER.start()

  return RESOURCE_TRACKER


def get_port_usage_tracker():
  """
  Singleton for tracking the process using a set of ports.
  """

  global PORT_USAGE_TRACKER

  if PORT_USAGE_TRACKER is None:
    PORT_USAGE_TRACKER = PortUsageTracker(CONFIG['port_usage_rate'])
    PORT_USAGE_TRACKER.start()

  return PORT_USAGE_TRACKER


def get_consensus_tracker():
  """
  Singleton for tracking the connections established by tor.
  """

  global CONSENSUS_TRACKER

  if CONSENSUS_TRACKER is None:
    CONSENSUS_TRACKER = ConsensusTracker()

  return CONSENSUS_TRACKER


def stop_trackers():
  """
  Halts active trackers, providing back the thread shutting them down.

  :returns: **threading.Thread** shutting down the daemons
  """

  def halt_trackers():
    trackers = filter(lambda t: t and t.is_alive(), [
      CONNECTION_TRACKER,
      RESOURCE_TRACKER,
      PORT_USAGE_TRACKER,
    ])

    for tracker in trackers:
      tracker.stop()

    for tracker in trackers:
      tracker.join()

  halt_thread = threading.Thread(target = halt_trackers)
  halt_thread.setDaemon(True)
  halt_thread.start()
  return halt_thread


def _resources_via_ps(pid):
  """
  Fetches resource usage information about a given process via ps. This returns
  a tuple of the form...

    (total_cpu_time, uptime, memory_in_bytes, memory_in_percent)

  :param int pid: process to be queried

  :returns: **tuple** with the resource usage information

  :raises: **IOError** if unsuccessful
  """

  # ps results are of the form...
  #
  #     TIME     ELAPSED   RSS %MEM
  # 3-08:06:32 21-00:00:12 121844 23.5
  #
  # ... or if Tor has only recently been started...
  #
  #     TIME      ELAPSED    RSS %MEM
  #  0:04.40        37:57  18772  0.9

  try:
    ps_call = system.call('ps -p {pid} -o cputime,etime,rss,%mem'.format(pid = pid))
  except OSError as exc:
    raise IOError(exc)

  if ps_call and len(ps_call) >= 2:
    stats = ps_call[1].strip().split()

    if len(stats) == 4:
      try:
        total_cpu_time = str_tools.parse_short_time_label(stats[0])
        uptime = str_tools.parse_short_time_label(stats[1])
        memory_bytes = int(stats[2]) * 1024  # ps size is in kb
        memory_percent = float(stats[3]) / 100.0

        return (total_cpu_time, uptime, memory_bytes, memory_percent)
      except ValueError:
        pass

  raise IOError('unrecognized output from ps: %s' % ps_call)


def _resources_via_proc(pid):
  """
  Fetches resource usage information about a given process via proc. This
  returns a tuple of the form...

    (total_cpu_time, uptime, memory_in_bytes, memory_in_percent)

  :param int pid: process to be queried

  :returns: **tuple** with the resource usage information

  :raises: **IOError** if unsuccessful
  """

  utime, stime, start_time = proc.stats(
    pid,
    proc.Stat.CPU_UTIME,
    proc.Stat.CPU_STIME,
    proc.Stat.START_TIME,
  )

  total_cpu_time = float(utime) + float(stime)
  memory_in_bytes = proc.memory_usage(pid)[0]
  total_memory = proc.physical_memory()

  uptime = time.time() - float(start_time)
  memory_in_percent = float(memory_in_bytes) / total_memory

  return (total_cpu_time, uptime, memory_in_bytes, memory_in_percent)


def _process_for_ports(local_ports, remote_ports):
  """
  Provides the name of the process using the given ports.

  :param list local_ports: local port numbers to look up
  :param list remote_ports: remote port numbers to look up

  :returns: **dict** mapping the ports to the associated **Process**, or
    **None** if it can't be determined

  :raises: **IOError** if unsuccessful
  """

  def _parse_lsof_line(line):
    line_comp = line.split()

    if not line:
      return None, None, None, None  # blank line
    elif len(line_comp) != 10:
      raise ValueError('lines are expected to have ten fields: %s' % line)
    elif line_comp[9] != '(ESTABLISHED)':
      return None, None, None, None  # connection isn't established
    elif not line_comp[1].isdigit():
      raise ValueError('expected the pid (which is the second value) to be an integer: %s' % line)

    pid = int(line_comp[1])
    cmd = line_comp[0]
    port_map = line_comp[8]

    if '->' not in port_map:
      raise ValueError("'%s' is expected to be a '->' separated mapping" % port_map)

    local, remote = port_map.split('->', 1)

    if ':' not in local or ':' not in remote:
      raise ValueError("'%s' is expected to be 'address:port' entries" % port_map)

    local_port = local.split(':', 1)[1]
    remote_port = remote.split(':', 1)[1]

    if not connection.is_valid_port(local_port):
      raise ValueError("'%s' isn't a valid port" % local_port)
    elif not connection.is_valid_port(remote_port):
      raise ValueError("'%s' isn't a valid port" % remote_port)

    return int(local_port), int(remote_port), pid, cmd

  # atagar@fenrir:~/Desktop/nyx$ lsof -i tcp:51849 -i tcp:37277
  # COMMAND  PID   USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
  # tor     2001 atagar   14u  IPv4  14048      0t0  TCP localhost:9051->localhost:37277 (ESTABLISHED)
  # tor     2001 atagar   15u  IPv4  22024      0t0  TCP localhost:9051->localhost:51849 (ESTABLISHED)
  # python  2462 atagar    3u  IPv4  14047      0t0  TCP localhost:37277->localhost:9051 (ESTABLISHED)
  # python  3444 atagar    3u  IPv4  22023      0t0  TCP localhost:51849->localhost:9051 (ESTABLISHED)

  try:
    lsof_cmd = 'lsof -nP ' + ' '.join(['-i tcp:%s' % port for port in (local_ports + remote_ports)])
    lsof_call = system.call(lsof_cmd)
  except OSError as exc:
    raise IOError(exc)

  if lsof_call:
    results = {}

    if lsof_call[0].startswith('COMMAND  '):
      lsof_call = lsof_call[1:]  # strip the title line

    for line in lsof_call:
      try:
        local_port, remote_port, pid, cmd = _parse_lsof_line(line)

        if local_port in local_ports:
          results[local_port] = Process(pid, cmd)
        elif remote_port in remote_ports:
          results[remote_port] = Process(pid, cmd)
      except ValueError as exc:
        raise IOError('unrecognized output from lsof (%s): %s' % (exc, line))

    for unknown_port in set(local_ports).union(remote_ports).difference(results.keys()):
      results[unknown_port] = None

    return results

  raise IOError('no results from lsof')


class Daemon(threading.Thread):
  """
  Daemon that can perform a given action at a set rate. Subclasses are expected
  to implement our _task() method with the work to be done.
  """

  def __init__(self, rate):
    super(Daemon, self).__init__()
    self.setDaemon(True)

    self._process_lock = threading.RLock()
    self._process_pid = None
    self._process_name = None

    self._rate = rate
    self._last_ran = -1  # time when we last ran
    self._run_counter = 0  # counter for the number of successful runs

    self._is_paused = False
    self._pause_condition = threading.Condition()
    self._halt = False  # terminates thread if true

    controller = tor_controller()
    controller.add_status_listener(self._tor_status_listener)
    self._tor_status_listener(controller, stem.control.State.INIT, None)

  def run(self):
    while not self._halt:
      time_since_last_ran = time.time() - self._last_ran

      if self._is_paused or time_since_last_ran < self._rate:
        sleep_duration = max(0.02, self._rate - time_since_last_ran)

        with self._pause_condition:
          if not self._halt:
            self._pause_condition.wait(sleep_duration)

        continue  # done waiting, try again

      with self._process_lock:
        if self._process_pid is not None:
          is_successful = self._task(self._process_pid, self._process_name)
        else:
          is_successful = False

        if is_successful:
          self._run_counter += 1

      self._last_ran = time.time()

  def _task(self, process_pid, process_name):
    """
    Task the resolver is meant to perform. This should be implemented by
    subclasses.

    :param int process_pid: pid of the process we're tracking
    :param str process_name: name of the process we're tracking

    :returns: **bool** indicating if our run was successful or not
    """

    return True

  def run_counter(self):
    """
    Provides the number of times we've successful runs so far. This can be used
    by callers to determine if our results have been seen by them before or
    not.

    :returns: **int** for the run count we're on
    """

    return self._run_counter

  def get_rate(self):
    """
    Provides the rate at which we perform our task.

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

    with self._pause_condition:
      self._halt = True
      self._pause_condition.notifyAll()

  def _tor_status_listener(self, controller, event_type, _):
    with self._process_lock:
      if not self._halt and event_type in (stem.control.State.INIT, stem.control.State.RESET):
        tor_pid = controller.get_pid(None)
        tor_cmd = system.name_by_pid(tor_pid) if tor_pid else None

        self._process_pid = tor_pid
        self._process_name = tor_cmd if tor_cmd else 'tor'
      elif event_type == stem.control.State.CLOSED:
        self._process_pid = None
        self._process_name = None

  def __enter__(self):
    self.start()
    return self

  def __exit__(self, exit_type, value, traceback):
    self.stop()
    self.join()


class ConnectionTracker(Daemon):
  """
  Periodically retrieves the connections established by tor.
  """

  def __init__(self, rate):
    super(ConnectionTracker, self).__init__(rate)

    self._connections = []
    self._start_times = {}  # connection => (unix_timestamp, is_legacy)
    self._custom_resolver = None
    self._is_first_run = True

    # Number of times in a row we've either failed with our current resolver or
    # concluded that our rate is too low.

    self._failure_count = 0
    self._rate_too_low_count = 0

    # If 'DisableDebuggerAttachment 0' is set we can do normal connection
    # resolution. Otherwise connection resolution by inference is the only game
    # in town.

    if tor_controller().get_conf('DisableDebuggerAttachment', None) == '0':
      self._resolvers = connection.system_resolvers()
    else:
      self._resolvers = [CustomResolver.INFERENCE]

    stem.util.log.info('Operating System: %s, Connection Resolvers: %s' % (os.uname()[0], ', '.join(self._resolvers)))

  def _task(self, process_pid, process_name):
    if self._custom_resolver:
      resolver = self._custom_resolver
      is_default_resolver = False
    elif self._resolvers:
      resolver = self._resolvers[0]
      is_default_resolver = True
    else:
      return False  # nothing to resolve with

    try:
      start_time = time.time()
      new_connections, new_start_times = [], {}

      if resolver == CustomResolver.INFERENCE:
        # provide connections going to a relay or one of our tor ports

        connections = []
        controller = tor_controller()
        consensus_tracker = get_consensus_tracker()

        for conn in proc.connections(user = controller.get_user(None)):
          if conn.remote_port in consensus_tracker.get_relay_fingerprints(conn.remote_address):
            connections.append(conn)  # outbound to another relay
          elif conn.local_port in controller.get_ports(stem.control.Listener.OR, []):
            connections.append(conn)  # inbound to our ORPort
          elif conn.local_port in controller.get_ports(stem.control.Listener.DIR, []):
            connections.append(conn)  # inbound to our DirPort
          elif conn.local_port in controller.get_ports(stem.control.Listener.CONTROL, []):
            connections.append(conn)  # controller connection
      else:
        connections = connection.get_connections(resolver, process_pid = process_pid, process_name = process_name)

      for conn in connections:
        conn_start_time, is_legacy = self._start_times.get(conn, (start_time, self._is_first_run))
        new_start_times[conn] = (conn_start_time, is_legacy)
        new_connections.append(Connection(conn_start_time, is_legacy, *conn))

      self._connections = new_connections
      self._start_times = new_start_times
      self._is_first_run = False

      runtime = time.time() - start_time

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
          stem.util.log.debug('connection lookup time increasing to %0.1f seconds per call' % min_rate)
      else:
        self._rate_too_low_count = 0

      return True
    except IOError as exc:
      stem.util.log.info(str(exc))

      # Fail over to another resolver if we've repeatedly been unable to use
      # this one.

      if is_default_resolver:
        self._failure_count += 1

        if self._failure_count >= 3:
          self._resolvers.pop(0)
          self._failure_count = 0

          if self._resolvers:
            stem.util.log.notice('Unable to query connections with %s, trying %s' % (resolver, self._resolvers[0]))
          else:
            stem.util.log.notice(UNABLE_TO_USE_ANY_RESOLVER_MSG)

      return False

  def get_custom_resolver(self):
    """
    Provides the custom resolver the user has selected. This is **None** if
    we're picking resolvers dynamically.

    :returns: :data:`~stem.util.connection.Resolver` we're overwritten to use
    """

    return self._custom_resolver

  def set_custom_resolver(self, resolver):
    """
    Sets the resolver used for connection resolution. If **None** then this is
    automatically determined based on what is available.

    :param stem.util.connection.Resolver resolver: resolver to use
    """

    self._custom_resolver = resolver

  def get_value(self):
    """
    Provides a listing of tor's latest connections.

    :returns: **list** of :class:`~nyx.tracker.Connection` we last
      retrieved, an empty list if our tracker's been stopped
    """

    if self._halt:
      return []
    else:
      return list(self._connections)


class ResourceTracker(Daemon):
  """
  Periodically retrieves the resource usage of tor.
  """

  def __init__(self, rate):
    super(ResourceTracker, self).__init__(rate)

    self._resources = None
    self._use_proc = proc.is_available()  # determines if we use proc or ps for lookups
    self._failure_count = 0  # number of times in a row we've failed to get results

  def get_value(self):
    """
    Provides tor's latest resource usage.

    :returns: latest :data:`~nyx.tracker.Resources` we've polled
    """

    result = self._resources
    return result if result else Resources(0.0, 0.0, 0.0, 0, 0.0, 0.0)

  def _task(self, process_pid, process_name):
    try:
      resolver = _resources_via_proc if self._use_proc else _resources_via_ps
      total_cpu_time, uptime, memory_in_bytes, memory_in_percent = resolver(process_pid)

      if self._resources:
        cpu_sample = (total_cpu_time - self._resources.cpu_total) / self._resources.cpu_total
      else:
        cpu_sample = 0.0  # we need a prior datapoint to give a sampling

      self._resources = Resources(
        cpu_sample = cpu_sample,
        cpu_average = total_cpu_time / uptime,
        cpu_total = total_cpu_time,
        memory_bytes = memory_in_bytes,
        memory_percent = memory_in_percent,
        timestamp = time.time(),
      )

      self._failure_count = 0
      return True
    except IOError as exc:
      self._failure_count += 1

      if self._use_proc:
        if self._failure_count >= 3:
          # We've failed three times resolving via proc. Warn, and fall back
          # to ps resolutions.

          self._use_proc = False
          self._failure_count = 0

          stem.util.log.info('Failed three attempts to get process resource usage from proc, falling back to ps (%s)' % exc)
        else:
          stem.util.log.debug('Unable to query process resource usage from proc (%s)' % exc)
      else:
        if self._failure_count >= 3:
          # Give up on further attempts.

          stem.util.log.info('Failed three attempts to get process resource usage from ps, giving up on getting resource usage information (%s)' % exc)
          self.stop()
        else:
          stem.util.log.debug('Unable to query process resource usage from ps (%s)' % exc)

      return False


class PortUsageTracker(Daemon):
  """
  Periodically retrieves the processes using a set of ports.
  """

  def __init__(self, rate):
    super(PortUsageTracker, self).__init__(rate)

    self._last_requested_local_ports = []
    self._last_requested_remote_ports = []
    self._processes_for_ports = {}
    self._failure_count = 0  # number of times in a row we've failed to get results

  def fetch(self, port):
    """
    Provides the process running on the given port. This retrieves the results
    from our cache, so it only works if we've already issued a query() request
    for it and gotten results.

    :param int port: port number to look up

    :returns: **Process** using the given port

    :raises:
      * :class:`nyx.tracker.UnresolvedResult` if the application is still
        being determined
      * :class:`nyx.tracker.UnknownApplication` if the we tried to resolve
        the application but it couldn't be determined
    """

    try:
      result = self._processes_for_ports[port]

      if result is None:
        raise UnknownApplication()
      else:
        return result
    except KeyError:
      raise UnresolvedResult()

  def query(self, local_ports, remote_ports):
    """
    Registers a given set of ports for further lookups, and returns the last
    set of 'port => process' mappings we retrieved. Note that this means that
    we will not return the requested ports unless they're requested again after
    a successful lookup has been performed.

    :param list local_ports: local port numbers to look up
    :param list remote_ports: remote port numbers to look up

    :returns: **dict** mapping port numbers to the **Process** using it
    """

    self._last_requested_local_ports = local_ports
    self._last_requested_remote_ports = remote_ports
    return self._processes_for_ports

  def _task(self, process_pid, process_name):
    local_ports = self._last_requested_local_ports
    remote_ports = self._last_requested_remote_ports

    if not local_ports and not remote_ports:
      return True

    result = {}

    # Use cached results from our last lookup if available.

    for port, process in self._processes_for_ports.items():
      if port in local_ports:
        result[port] = process
        local_ports.remove(port)
      elif port in remote_ports:
        result[port] = process
        remote_ports.remove(port)

    try:
      if local_ports or remote_ports:
        result.update(_process_for_ports(local_ports, remote_ports))

      self._processes_for_ports = result
      self._failure_count = 0
      return True
    except IOError as exc:
      self._failure_count += 1

      if self._failure_count >= 3:
        stem.util.log.info('Failed three attempts to determine the process using active ports (%s)' % exc)
        self.stop()
      else:
        stem.util.log.debug('Unable to query the processes using ports usage lsof (%s)' % exc)

      return False


class ConsensusTracker(object):
  """
  Provides performant lookups of consensus information.
  """

  def __init__(self):
    self._fingerprint_cache = {}  # {address => [(port, fingerprint), ..]} for relays
    self._nickname_cache = {}  # fingerprint => nickname lookup cache
    self._address_cache = {}

    self._my_router_status_entry = None
    self._my_router_status_entry_time = 0

    # Stem's get_network_statuses() is slow, and overkill for what we need
    # here. Just parsing the raw GETINFO response to cut startup time down.

    start_time = time.time()
    controller = tor_controller()
    ns_response = controller.get_info('ns/all', None)

    if ns_response:
      for line in ns_response.splitlines():
        if line.startswith('r '):
          r_comp = line.split(' ')

          address = r_comp[6]
          or_port = int(r_comp[7])
          fingerprint = stem.descriptor.router_status_entry._base64_to_hex(r_comp[2])
          nickname = r_comp[1]

          self._fingerprint_cache.setdefault(address, []).append((or_port, fingerprint))
          self._address_cache[fingerprint] = (address, or_port)
          self._nickname_cache[fingerprint] = nickname

      stem.util.log.info('Cached consensus data. Took %0.2fs. Cache size is %s for fingerprints, %s for addresses, and %s for nicknames' % (time.time() - start_time, stem.util.str_tools.size_label(sys.getsizeof(self._fingerprint_cache)), stem.util.str_tools.size_label(sys.getsizeof(self._address_cache)), stem.util.str_tools.size_label(sys.getsizeof(self._nickname_cache))))

    controller.add_event_listener(lambda event: self.update(event.desc), stem.control.EventType.NEWCONSENSUS)

  def update(self, router_status_entries):
    """
    Updates our cache with the given router status entries.

    :param list router_status_entries: router status entries to populate our cache with
    """

    new_fingerprint_cache = {}
    new_address_cache = {}
    new_nickname_cache = {}

    start_time = time.time()
    our_fingerprint = tor_controller().get_info('fingerprint', None)

    for desc in router_status_entries:
      new_fingerprint_cache.setdefault(desc.address, []).append((desc.or_port, desc.fingerprint))
      new_address_cache[desc.fingerprint] = (desc.address, desc.or_port)
      new_nickname_cache[desc.fingerprint] = desc.nickname

      if desc.fingerprint == our_fingerprint:
        self._my_router_status_entry = desc
        self._my_router_status_entry_time = time.time()

    self._fingerprint_cache = new_fingerprint_cache
    self._address_cache = new_address_cache
    self._nickname_cache = new_nickname_cache

    stem.util.log.info('Updated consensus cache. Took %0.2fs. Cache size is %s for fingerprints, %s for addresses, and %s for nicknames' % (time.time() - start_time, stem.util.str_tools.size_label(sys.getsizeof(self._fingerprint_cache)), stem.util.str_tools.size_label(sys.getsizeof(self._address_cache)), stem.util.str_tools.size_label(sys.getsizeof(self._nickname_cache))))

  def my_router_status_entry(self):
    """
    Provides the router status entry of ourselves. Descriptors are published
    hourly, and results are cached for five minutes.

    :returns: :class:`~stem.descriptor.router_status_entry.RouterStatusEntryV3`
      for ourselves, **None** if it cannot be retrieved
    """

    if self._my_router_status_entry is None or (time.time() - self._my_router_status_entry_time) > 300:
      self._my_router_status_entry = tor_controller().get_network_status(default = None)
      self._my_router_status_entry_time = time.time()

    return self._my_router_status_entry

  def get_relay_nickname(self, fingerprint):
    """
    Provides the nickname associated with the given relay.

    :param str fingerprint: relay to look up

    :returns: **str** with the nickname ("Unnamed" if unset), and **None** if
      no such relay exists
    """

    controller = tor_controller()

    if not fingerprint:
      return None
    elif fingerprint == controller.get_info('fingerprint', None):
      return controller.get_conf('Nickname', 'Unnamed')
    else:
      return self._nickname_cache.get(fingerprint)

  def get_relay_fingerprints(self, address):
    """
    Provides the relays running at a given location.

    :param str address: address to be checked

    :returns: **dict** of ORPorts to their fingerprint
    """

    controller = tor_controller()

    if address == controller.get_info('address', None):
      fingerprint = controller.get_info('fingerprint', None)
      ports = controller.get_ports(stem.control.Listener.OR, None)

      if fingerprint and ports:
        return dict([(port, fingerprint) for port in ports])

    return dict([(port, fp) for (port, fp) in self._fingerprint_cache.get(address, [])])

  def get_relay_address(self, fingerprint, default):
    """
    Provides the (address, port) tuple where a relay is running.

    :param str fingerprint: fingerprint to be checked

    :returns: **tuple** with a **str** address and **int** port
    """

    controller = tor_controller()

    if fingerprint == controller.get_info('fingerprint', None):
      my_address = controller.get_info('address', None)
      my_or_ports = controller.get_ports(stem.control.Listener.OR, [])

      if my_address and len(my_or_ports) == 1:
        return (my_address, my_or_ports[0])

    return self._address_cache.get(fingerprint, default)
