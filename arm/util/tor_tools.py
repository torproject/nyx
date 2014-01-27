"""
Helper for working with an active tor process. This both provides a wrapper for
accessing stem and notifications of state changes to subscribers.
"""

import math
import os
import threading
import time

import stem
import stem.control

from stem.util import log, proc, system

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
    self._fingerprint_mappings = None     # mappings of ip -> [(port, fingerprint), ...]
    self._fingerprint_lookup_cache = {}   # lookup cache with (ip, port) -> fingerprint mappings
    self._nickname_lookup_cache = {}      # lookup cache with fingerprint -> nickname mappings
    self._address_lookup_cache = {}       # lookup cache with fingerprint -> (ip address, or port) mappings
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

      self._fingerprint_mappings = None
      self._fingerprint_lookup_cache = {}
      self._nickname_lookup_cache = {}
      self._address_lookup_cache = {}
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

  def get_hidden_service_ports(self, default = []):
    """
    Provides the target ports hidden services are configured to use.

    Arguments:
      default - value provided back if unable to query the hidden service ports
    """

    result = []
    hs_options = self.controller.get_conf_map("HiddenServiceOptions", {})

    for entry in hs_options.get("HiddenServicePort", []):
      # HiddenServicePort entries are of the form...
      #
      #   VIRTPORT [TARGET]
      #
      # ... with the TARGET being an address, port, or address:port. If the
      # target port isn't defined then uses the VIRTPORT.

      hs_port = None

      if ' ' in entry:
        virtport, target = entry.split(' ', 1)

        if ':' in target:
          hs_port = target.split(':', 1)[1]  # target is an address:port
        elif target.isdigit():
          hs_port = target  # target is a port
        else:
          hs_port = virtport  # target is an address
      else:
        hs_port = entry  # just has the virtual port

      if hs_port.isdigit():
        result.append(hs_port)

    if result:
      return result
    else:
      return default

  def get_my_bandwidth_rate(self, default = None):
    """
    Provides the effective relaying bandwidth rate of this relay. Currently
    this doesn't account for SETCONF events.

    Arguments:
      default - result if the query fails
    """

    # effective relayed bandwidth is the minimum of BandwidthRate,
    # MaxAdvertisedBandwidth, and RelayBandwidthRate (if set)

    effective_rate = int(self.get_option("BandwidthRate", None))

    relay_rate = self.get_option("RelayBandwidthRate", None)

    if relay_rate and relay_rate != "0":
      effective_rate = min(effective_rate, int(relay_rate))

    max_advertised = self.get_option("MaxAdvertisedBandwidth", None)

    if max_advertised:
      effective_rate = min(effective_rate, int(max_advertised))

    if effective_rate is not None:
      return effective_rate
    else:
      return default

  def get_my_bandwidth_burst(self, default = None):
    """
    Provides the effective bandwidth burst rate of this relay. Currently this
    doesn't account for SETCONF events.

    Arguments:
      default - result if the query fails
    """

    # effective burst (same for BandwidthBurst and RelayBandwidthBurst)
    effective_burst = int(self.get_option("BandwidthBurst", None))

    relay_burst = self.get_option("RelayBandwidthBurst", None)

    if relay_burst and relay_burst != "0":
      effective_burst = min(effective_burst, int(relay_burst))

    if effective_burst is not None:
      return effective_burst
    else:
      return default

  def get_my_bandwidth_observed(self, default = None):
    """
    Provides the relay's current observed bandwidth (the throughput determined
    from historical measurements on the client side). This is used in the
    heuristic used for path selection if the measured bandwidth is undefined.
    This is fetched from the descriptors and hence will get stale if
    descriptors aren't periodically updated.

    Arguments:
      default - result if the query fails
    """

    my_fingerprint = self.get_info("fingerprint", None)

    if my_fingerprint:
      my_descriptor = self.controller.get_server_descriptor(my_fingerprint)

      if my_descriptor:
        return my_descriptor.observed_bandwidth

    return default

  def get_my_bandwidth_measured(self, default = None):
    """
    Provides the relay's current measured bandwidth (the throughput as noted by
    the directory authorities and used by clients for relay selection). This is
    undefined if not in the consensus or with older versions of Tor. Depending
    on the circumstances this can be from a variety of things (observed,
    measured, weighted measured, etc) as described by:
    https://trac.torproject.org/projects/tor/ticket/1566

    Arguments:
      default - result if the query fails
    """

    # TODO: Tor is documented as providing v2 router status entries but
    # actually looks to be v3. This needs to be sorted out between stem
    # and tor.

    my_fingerprint = self.get_info("fingerprint", None)

    if my_fingerprint:
      my_status_entry = self.controller.get_network_status(my_fingerprint)

      if my_status_entry and hasattr(my_status_entry, 'bandwidth'):
        return my_status_entry.bandwidth

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

  def is_geoip_unavailable(self):
    """
    Provides true if we've concluded that our geoip database is unavailable,
    false otherwise.
    """

    if self.is_alive():
      return self.controller.is_geoip_unavailable()
    else:
      return False

  def get_my_user(self):
    """
    Provides the user this process is running under. If unavailable this
    provides None.
    """

    return self.controller.get_user(None)

  def get_my_file_descriptor_usage(self):
    """
    Provides the number of file descriptors currently being used by this
    process. This returns None if this can't be determined.
    """

    # The file descriptor usage is the size of the '/proc/<pid>/fd' contents
    # http://linuxshellaccount.blogspot.com/2008/06/finding-number-of-open-file-descriptors.html
    # I'm not sure about other platforms (like BSD) so erroring out there.

    self.conn_lock.acquire()

    result = None

    if self.is_alive() and proc.is_available():
      my_pid = self.controller.get_pid(None)

      if my_pid:
        try:
          result = len(os.listdir("/proc/%s/fd" % my_pid))
        except:
          pass

    self.conn_lock.release()

    return result

  def get_start_time(self):
    """
    Provides the unix time for when the tor process first started. If this
    can't be determined then this provides None.
    """

    try:
      return system.get_start_time(self.controller.get_pid())
    except:
      return None

  def is_exiting_allowed(self, ip_address, port):
    """
    Checks if the given destination can be exited to by this relay, returning
    True if so and False otherwise.
    """

    self.conn_lock.acquire()

    result = False

    if self.is_alive():
      # If we allow any exiting then this could be relayed DNS queries,
      # otherwise the policy is checked. Tor still makes DNS connections to
      # test when exiting isn't allowed, but nothing is relayed over them.
      # I'm registering these as non-exiting to avoid likely user confusion:
      # https://trac.torproject.org/projects/tor/ticket/965

      our_policy = self.get_exit_policy()

      if our_policy and our_policy.is_exiting_allowed() and port == "53":
        result = True
      else:
        result = our_policy and our_policy.can_exit_to(ip_address, port)

    self.conn_lock.release()

    return result

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

  def get_relay_fingerprint(self, relay_address, relay_port = None, get_all_matches = False):
    """
    Provides the fingerprint associated with the given address. If there's
    multiple potential matches or the mapping is unknown then this returns
    None. This disambiguates the fingerprint if there's multiple relays on
    the same ip address by several methods, one of them being to pick relays
    we have a connection with.

    Arguments:
      relay_address  - address of relay to be returned
      relay_port     - orport of relay (to further narrow the results)
      get_all_matches - ignores the relay_port and provides all of the
                      (port, fingerprint) tuples matching the given
                      address
    """

    self.conn_lock.acquire()

    result = None

    if self.is_alive():
      if get_all_matches:
        # populates the ip -> fingerprint mappings if not yet available
        if self._fingerprint_mappings is None:
          self._fingerprint_mappings = self._get_fingerprint_mappings()

        if relay_address in self._fingerprint_mappings:
          result = self._fingerprint_mappings[relay_address]
        else:
          result = []
      else:
        # query the fingerprint if it isn't yet cached
        if not (relay_address, relay_port) in self._fingerprint_lookup_cache:
          relay_fingerprint = self._get_relay_fingerprint(relay_address, relay_port)
          self._fingerprint_lookup_cache[(relay_address, relay_port)] = relay_fingerprint

        result = self._fingerprint_lookup_cache[(relay_address, relay_port)]

    self.conn_lock.release()

    return result

  def get_relay_nickname(self, relay_fingerprint):
    """
    Provides the nickname associated with the given relay. This provides None
    if no such relay exists, and "Unnamed" if the name hasn't been set.

    Arguments:
      relay_fingerprint - fingerprint of the relay
    """

    self.conn_lock.acquire()

    result = None

    if self.is_alive():
      # query the nickname if it isn't yet cached
      if not relay_fingerprint in self._nickname_lookup_cache:
        if relay_fingerprint == self.get_info("fingerprint", None):
          # this is us, simply check the config
          my_nickname = self.get_option("Nickname", "Unnamed")
          self._nickname_lookup_cache[relay_fingerprint] = my_nickname
        else:
          ns_entry = self.controller.get_network_status(relay_fingerprint, None)

          if ns_entry:
            self._nickname_lookup_cache[relay_fingerprint] = ns_entry.nickname

      result = self._nickname_lookup_cache[relay_fingerprint]

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

  def get_relay_address(self, relay_fingerprint, default = None):
    """
    Provides the (IP Address, ORPort) tuple for a given relay. If the lookup
    fails then this returns the default.

    Arguments:
      relay_fingerprint - fingerprint of the relay
    """

    self.conn_lock.acquire()

    result = default

    if self.is_alive():
      # query the address if it isn't yet cached
      if not relay_fingerprint in self._address_lookup_cache:
        if relay_fingerprint == self.get_info("fingerprint", None):
          # this is us, simply check the config
          my_address = self.get_info("address", None)
          my_or_port = self.get_option("ORPort", None)

          if my_address and my_or_port:
            self._address_lookup_cache[relay_fingerprint] = (my_address, my_or_port)
        else:
          # check the consensus for the relay
          ns_entry = self.get_consensus_entry(relay_fingerprint)

          if ns_entry:
            ns_line_comp = ns_entry.split("\n")[0].split(" ")

            if len(ns_line_comp) >= 8:
              self._address_lookup_cache[relay_fingerprint] = (ns_line_comp[6], ns_line_comp[7])

      result = self._address_lookup_cache.get(relay_fingerprint, default)

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

    self._fingerprint_lookup_cache = {}
    self._nickname_lookup_cache = {}
    self._address_lookup_cache = {}
    self._consensus_lookup_cache = {}

    if self._fingerprint_mappings is not None:
      self._fingerprint_mappings = self._get_fingerprint_mappings(event.desc)

    self.conn_lock.release()

  def new_desc_event(self, event):
    self.conn_lock.acquire()

    desc_fingerprints = [fingerprint for (fingerprint, nickname) in event.relays]

    # If we're tracking ip address -> fingerprint mappings then update with
    # the new relays.

    self._fingerprint_lookup_cache = {}
    self._descriptor_lookup_cache = {}

    if self._fingerprint_mappings is not None:
      for fingerprint in desc_fingerprints:
        # gets consensus data for the new descriptor

        try:
          desc = self.controller.get_network_status(fingerprint)
        except stem.ControllerError:
          continue

        # updates fingerprintMappings with new data

        if desc.address in self._fingerprint_mappings:
          # if entry already exists with the same orport, remove it

          orport_match = None

          for entry_port, entry_fingerprint in self._fingerprint_mappings[desc.address]:
            if entry_port == desc.or_port:
              orport_match = (entry_port, entry_fingerprint)
              break

          if orport_match:
            self._fingerprint_mappings[desc.address].remove(orport_match)

          # add the new entry

          self._fingerprint_mappings[desc.address].append((desc.or_port, desc.fingerprint))
        else:
          self._fingerprint_mappings[desc.address] = [(desc.or_port, desc.fingerprint)]

    self.conn_lock.release()

  def _get_fingerprint_mappings(self, descriptors = None):
    """
    Provides IP address to (port, fingerprint) tuple mappings for all of the
    currently cached relays.

    Arguments:
      descriptors - router status entries (fetched if not provided)
    """

    results = {}

    if self.is_alive():
      # fetch the current network status if not provided

      if not descriptors:
        try:
          descriptors = self.controller.get_network_statuses()
        except stem.ControllerError:
          descriptors = []

      # construct mappings of ips to relay data

      for desc in descriptors:
        results.setdefault(desc.address, []).append((desc.or_port, desc.fingerprint))

    return results

  def _get_relay_fingerprint(self, relay_address, relay_port):
    """
    Provides the fingerprint associated with the address/port combination.

    Arguments:
      relay_address - address of relay to be returned
      relay_port    - orport of relay (to further narrow the results)
    """

    # If we were provided with a string port then convert to an int (so
    # lookups won't mismatch based on type).

    if isinstance(relay_port, str):
      relay_port = int(relay_port)

    # checks if this matches us

    if relay_address == self.get_info("address", None):
      if not relay_port or relay_port == self.get_option("ORPort", None):
        return self.get_info("fingerprint", None)

    # if we haven't yet populated the ip -> fingerprint mappings then do so

    if self._fingerprint_mappings is None:
      self._fingerprint_mappings = self._get_fingerprint_mappings()

    potential_matches = self._fingerprint_mappings.get(relay_address)

    if not potential_matches:
      return None  # no relay matches this ip address

    if len(potential_matches) == 1:
      # There's only one relay belonging to this ip address. If the port
      # matches then we're done.

      match = potential_matches[0]

      if relay_port and match[0] != relay_port:
        return None
      else:
        return match[1]
    elif relay_port:
      # Multiple potential matches, so trying to match based on the port.
      for entry_port, entry_fingerprint in potential_matches:
        if entry_port == relay_port:
          return entry_fingerprint

    return None
