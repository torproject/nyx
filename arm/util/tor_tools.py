"""
Helper for working with an active tor process. This both provides a wrapper for
accessing stem and notifications of state changes to subscribers.
"""

import threading

import stem
import stem.control

from stem.util import log

CONTROLLER = None  # singleton Controller instance

UNDEFINED = "<Undefined_ >"


def get_conn():
  """
  Singleton constructor for a Controller. Be aware that this starts as being
  uninitialized, needing a stem Controller before it's fully functional.
  """

  global CONTROLLER

  if CONTROLLER is None:
    CONTROLLER = Controller()

  return CONTROLLER


class Controller:
  """
  Stem wrapper providing convenience functions (mostly from the days of using
  TorCtl), listener functionality for tor's state, and the capability for
  controller connections to be restarted if closed.
  """

  def __init__(self):
    self.controller = None
    self.conn_lock = threading.RLock()
    self._consensus_lookup_cache = {}     # lookup cache with network status entries
    self._descriptor_lookup_cache = {}    # lookup cache with relay descriptors

  def init(self, controller):
    """
    Uses the given stem instance for future operations, notifying listeners
    about the change.

    Arguments:
      controller - stem based Controller instance
    """

    # TODO: We should reuse our controller instance so event listeners will be
    # re-attached. This is a point of regression until we do... :(

    if controller.is_alive() and controller != self.controller:
      self.conn_lock.acquire()

      if self.controller:
        self.close()  # shut down current connection

      self.controller = controller
      log.info("Stem connected to tor version %s" % self.controller.get_version())

      self.controller.add_event_listener(self.ns_event, stem.control.EventType.NS)
      self.controller.add_event_listener(self.new_consensus_event, stem.control.EventType.NEWCONSENSUS)
      self.controller.add_event_listener(self.new_desc_event, stem.control.EventType.NEWDESC)

      # reset caches for ip -> fingerprint lookups

      self._consensus_lookup_cache = {}
      self._descriptor_lookup_cache = {}

      self.conn_lock.release()

  def close(self):
    """
    Closes the current stem instance and notifies listeners.
    """

    self.conn_lock.acquire()

    if self.controller:
      self.controller.close()

    self.conn_lock.release()

  def get_controller(self):
    return self.controller

  def is_alive(self):
    """
    Returns True if this has been initialized with a working stem instance,
    False otherwise.
    """

    self.conn_lock.acquire()

    result = False

    if self.controller:
      if self.controller.is_alive():
        result = True
      else:
        self.close()

    self.conn_lock.release()
    return result

  def get_info(self, param, default = UNDEFINED):
    """
    Queries the control port for the given GETINFO option, providing the
    default if the response is undefined or fails for any reason (error
    response, control port closed, initiated, etc).

    Arguments:
      param   - GETINFO option to be queried
      default - result if the query fails
    """

    self.conn_lock.acquire()

    try:
      if not self.is_alive():
        if default != UNDEFINED:
          return default
        else:
          raise stem.SocketClosed()

      if default != UNDEFINED:
        return self.controller.get_info(param, default)
      else:
        return self.controller.get_info(param)
    except stem.SocketClosed as exc:
      self.close()
      raise exc
    finally:
      self.conn_lock.release()

  def get_option(self, param, default = UNDEFINED, multiple = False):
    """
    Queries the control port for the given configuration option, providing the
    default if the response is undefined or fails for any reason. If multiple
    values exist then this arbitrarily returns the first unless the multiple
    flag is set.

    Arguments:
      param     - configuration option to be queried
      default   - result if the query fails
      multiple  - provides a list with all returned values if true, otherwise
                  this just provides the first result
    """

    self.conn_lock.acquire()

    try:
      if not self.is_alive():
        if default != UNDEFINED:
          return default
        else:
          raise stem.SocketClosed()

      if default != UNDEFINED:
        return self.controller.get_conf(param, default, multiple)
      else:
        return self.controller.get_conf(param, multiple = multiple)
    except stem.SocketClosed as exc:
      self.close()
      raise exc
    finally:
      self.conn_lock.release()

  def set_option(self, param, value = None):
    """
    Issues a SETCONF to set the given option/value pair. An exeptions raised
    if it fails to be set. If no value is provided then this sets the option to
    0 or NULL.

    Arguments:
      param - configuration option to be set
      value - value to set the parameter to (this can be either a string or a
              list of strings)
    """

    self.conn_lock.acquire()

    try:
      if not self.is_alive():
        raise stem.SocketClosed()

      self.controller.set_conf(param, value)
    except stem.SocketClosed as exc:
      self.close()
      raise exc
    finally:
      self.conn_lock.release()

  def save_conf(self):
    """
    Calls tor's SAVECONF method.
    """

    self.conn_lock.acquire()

    if self.is_alive():
      self.controller.save_conf()

    self.conn_lock.release()

  def get_circuits(self, default = []):
    """
    This provides a list with tuples of the form:
    (circuit_id, status, purpose, (fingerprint1, fingerprint2...))

    Arguments:
      default - value provided back if unable to query the circuit-status
    """

    # TODO: We're losing caching around this. We should check to see the call
    # volume of this and probably add it to stem.

    results = []

    for entry in self.controller.get_circuits():
      fingerprints = []

      for fp, nickname in entry.path:
        if not fp:
          consensus_entry = self.controller.get_network_status(nickname, None)

          if consensus_entry:
            fp = consensus_entry.fingerprint

          # It shouldn't be possible for this lookup to fail, but we
          # need to fill something (callers won't expect our own client
          # paths to have unknown relays). If this turns out to be wrong
          # then log a warning.

          if not fp:
            log.warn("Unable to determine the fingerprint for a relay in our own circuit: %s" % nickname)
            fp = "0" * 40

        fingerprints.append(fp)

      results.append((int(entry.id), entry.status, entry.purpose, fingerprints))

    if results:
      return results
    else:
      return default

  def get_my_flags(self, default = None):
    """
    Provides the flags held by this relay.

    Arguments:
      default - result if the query fails or this relay isn't a part of the consensus yet
    """

    my_fingerprint = self.get_info("fingerprint", None)

    if my_fingerprint:
      my_status_entry = self.controller.get_network_status(my_fingerprint)

      if my_status_entry:
        return my_status_entry.flags

    return default

  def get_version(self):
    """
    Provides the version of our tor instance, this is None if we don't have a
    connection.
    """

    self.conn_lock.acquire()

    try:
      return self.controller.get_version()
    except stem.SocketClosed:
      self.close()
      return None
    except:
      return None
    finally:
      self.conn_lock.release()

  def get_my_user(self):
    """
    Provides the user this process is running under. If unavailable this
    provides None.
    """

    return self.controller.get_user(None)

  def get_exit_policy(self):
    """
    Provides an ExitPolicy instance for the head of this relay's exit policy
    chain. If there's no active connection then this provides None.
    """

    self.conn_lock.acquire()

    result = None

    if self.is_alive():
      try:
        result = self.controller.get_exit_policy(None)
      except:
        pass

    self.conn_lock.release()

    return result

  def get_consensus_entry(self, relay_fingerprint):
    """
    Provides the most recently available consensus information for the given
    relay. This is none if no such information exists.

    Arguments:
      relay_fingerprint - fingerprint of the relay
    """

    self.conn_lock.acquire()

    result = None

    if self.is_alive():
      if not relay_fingerprint in self._consensus_lookup_cache:
        ns_entry = self.get_info("ns/id/%s" % relay_fingerprint, None)
        self._consensus_lookup_cache[relay_fingerprint] = ns_entry

      result = self._consensus_lookup_cache[relay_fingerprint]

    self.conn_lock.release()

    return result

  def get_descriptor_entry(self, relay_fingerprint):
    """
    Provides the most recently available descriptor information for the given
    relay. Unless FetchUselessDescriptors is set this may frequently be
    unavailable. If no such descriptor is available then this returns None.

    Arguments:
      relay_fingerprint - fingerprint of the relay
    """

    self.conn_lock.acquire()

    result = None

    if self.is_alive():
      if not relay_fingerprint in self._descriptor_lookup_cache:
        desc_entry = self.get_info("desc/id/%s" % relay_fingerprint, None)
        self._descriptor_lookup_cache[relay_fingerprint] = desc_entry

      result = self._descriptor_lookup_cache[relay_fingerprint]

    self.conn_lock.release()

    return result

  def get_relay_exit_policy(self, relay_fingerprint):
    """
    Provides the ExitPolicy instance associated with the given relay. The tor
    consensus entries don't indicate if private addresses are rejected or
    address-specific policies, so this is only used as a fallback if a recent
    descriptor is unavailable. This returns None if unable to determine the
    policy.

    Arguments:
      relay_fingerprint - fingerprint of the relay
    """

    self.conn_lock.acquire()

    result = None

    if self.is_alive():
      # attempts to fetch the policy via the descriptor
      descriptor = self.controller.get_server_descriptor(relay_fingerprint, None)

      if descriptor:
        result = descriptor.exit_policy

    self.conn_lock.release()

    return result

  def add_event_listener(self, listener, *event_types):
    """
    Directs further tor controller events to callback functions of the
    listener. If a new control connection is initialized then this listener is
    reattached.
    """

    self.conn_lock.acquire()

    if self.is_alive():
      self.controller.add_event_listener(listener, *event_types)

    self.conn_lock.release()

  def remove_event_listener(self, listener):
    """
    Stops the given event listener from being notified of further events.
    """

    self.conn_lock.acquire()

    if self.is_alive():
      self.controller.remove_event_listener(listener)

    self.conn_lock.release()

  def add_status_listener(self, callback):
    """
    Directs further events related to tor's controller status to the callback
    function.

    Arguments:
      callback - functor that'll accept the events, expected to be of the form:
                 myFunction(controller, event_type)
    """

    self.controller.add_status_listener(callback)

  def reload(self):
    """
    This resets tor (sending a RELOAD signal to the control port) causing tor's
    internal state to be reset and the torrc reloaded.
    """

    self.conn_lock.acquire()

    try:
      if self.is_alive():
        try:
          self.controller.signal(stem.Signal.RELOAD)
        except Exception as exc:
          # new torrc parameters caused an error (tor's likely shut down)
          raise IOError(str(exc))
    finally:
      self.conn_lock.release()

  def shutdown(self, force = False):
    """
    Sends a shutdown signal to the attached tor instance. For relays the
    actual shutdown is delayed for thirty seconds unless the force flag is
    given. This raises an IOError if a signal is sent but fails.

    Arguments:
      force - triggers an immediate shutdown for relays if True
    """

    self.conn_lock.acquire()

    raised_exception = None

    if self.is_alive():
      try:
        is_relay = self.get_option("ORPort", None) is not None

        if force:
          self.controller.signal(stem.Signal.HALT)
        else:
          self.controller.signal(stem.Signal.SHUTDOWN)

        # shuts down control connection if we aren't making a delayed shutdown

        if force or not is_relay:
          self.close()
      except Exception as exc:
        raised_exception = IOError(str(exc))

    self.conn_lock.release()

    if raised_exception:
      raise raised_exception

  def ns_event(self, event):
    self._consensus_lookup_cache = {}

  def new_consensus_event(self, event):
    self.conn_lock.acquire()

    # reconstructs consensus based mappings

    self._consensus_lookup_cache = {}

    self.conn_lock.release()

  def new_desc_event(self, event):
    self.conn_lock.acquire()
    self._descriptor_lookup_cache = {}
    self.conn_lock.release()
