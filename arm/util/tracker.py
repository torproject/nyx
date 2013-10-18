"""
Background tasks for gathering informatino about the tor process.

::

  get_connection_resolver - provides a ConnectionResolver for our tor process

  Daemon - common parent for resolvers
    |- run_counter - number of successful runs
    |- get_rate - provides the rate at which we run
    |- set_rate - sets the rate at which we run
    |- set_paused - pauses or continues work
    +- stop - stops further work by the daemon

  ConnectionResolver - periodically queries tor's connection information
    |- set_process - set the pid and process name used for lookups
    |- get_custom_resolver - provide the custom conntion resolver we're using
    |- set_custom_resolver - overwrites automatic resolver selecion with a custom resolver
    |- get_connections - provides our latest connection results
    +- get_resolution_count - number of times we've fetched connection information
"""

import time
import threading

from stem.util import conf, connection, log

CONNECTION_RESOLVER = None

CONFIG = conf.config_dict('arm', {
  'queries.connections.minRate': 5,
  'msg.unable_to_use_resolver': '',
  'msg.unable_to_use_all_resolvers': '',
})

def get_connection_resolver():
  """
  Singleton constructor for a connection resolver for tor's process.
  """

  global CONNECTION_RESOLVER

  if CONNECTION_RESOLVER is None:
    CONNECTION_RESOLVER = ConnectionResolver()

  return CONNECTION_RESOLVER

class Daemon(threading.Thread):
  """
  Daemon that can perform a unit of work at a given rate.
  """

  def __init__(self, rate):
    threading.Thread.__init__(self)
    self.daemon = True

    self._rate = rate
    self._last_ran = -1  # time when we last ran
    self._run_counter = 0  # counter for the number of successful runs

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

      is_successful = self.task()

      if is_successful:
        self._run_counter += 1

      self._last_ran = time.time()

  def task(self):
    """
    Task the resolver is meant to perform. This should be implemented by
    subclasses.
    """

    pass

  def run_counter(self):
    """
    Provides the number of successful runs so far. This can be used to
    determine if the daemon's results are new for the caller or not.
    """

    return self._run_counter

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

      self._connections = connection.get_connections(
        resolver,
        process_pid = self._process_pid,
        process_name = self._process_name,
      )

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
          log.debug("connection lookup time increasing to %0.1f seconds per call" % min_rate)
      else:
        self._rate_too_low_count = 0

      return True
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

      return False

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
