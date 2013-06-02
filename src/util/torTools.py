"""
Helper for working with an active tor process. This both provides a wrapper for
accessing stem and notifications of state changes to subscribers.
"""

import os
import pwd
import time
import math
import thread
import threading
import Queue

import stem
import stem.control
import stem.descriptor
import stem.util.system

from util import connections

from stem.util import conf, enum, log, proc, str_tools, system

# Addresses of the default directory authorities for tor version 0.2.3.0-alpha
# (this comes from the dirservers array in src/or/config.c).
DIR_SERVERS = [("86.59.21.38", "80"),         # tor26
               ("128.31.0.39", "9031"),       # moria1
               ("216.224.124.114", "9030"),   # ides
               ("212.112.245.170", "80"),     # gabelmoo
               ("194.109.206.212", "80"),     # dizum
               ("193.23.244.244", "80"),      # dannenberg
               ("208.83.223.34", "443"),      # urras
               ("213.115.239.118", "443"),    # maatuska
               ("82.94.251.203", "80")]       # Tonga

# message logged by default when a controller can't set an event type
DEFAULT_FAILED_EVENT_MSG = "Unsupported event type: %s"

CONTROLLER = None # singleton Controller instance

UNDEFINED = "<Undefined_ >"

UNKNOWN = "UNKNOWN" # value used by cached information if undefined

CONFIG = conf.config_dict("arm", {
  "features.pathPrefix": "",
})

# events used for controller functionality:
# NEWDESC, NS, and NEWCONSENSUS - used for cache invalidation
REQ_EVENTS = {"NEWDESC": "information related to descriptors will grow stale",
              "NS": "information related to the consensus will grow stale",
              "NEWCONSENSUS": "information related to the consensus will grow stale"}

def getConn():
  """
  Singleton constructor for a Controller. Be aware that this starts as being
  uninitialized, needing a stem Controller before it's fully functional.
  """
  
  global CONTROLLER
  if CONTROLLER == None: CONTROLLER = Controller()
  return CONTROLLER

class Controller:
  """
  Stem wrapper providing convenience functions (mostly from the days of using
  TorCtl), listener functionality for tor's state, and the capability for
  controller connections to be restarted if closed.
  """
  
  def __init__(self):
    self.controller = None
    self.connLock = threading.RLock()
    self.controllerEvents = []          # list of successfully set controller events
    self._fingerprintMappings = None    # mappings of ip -> [(port, fingerprint), ...]
    self._fingerprintLookupCache = {}   # lookup cache with (ip, port) -> fingerprint mappings
    self._nicknameLookupCache = {}      # lookup cache with fingerprint -> nickname mappings
    self._nicknameToFpLookupCache = {}  # lookup cache with nickname -> fingerprint mappings
    self._addressLookupCache = {}       # lookup cache with fingerprint -> (ip address, or port) mappings
    self._consensusLookupCache = {}     # lookup cache with network status entries
    self._descriptorLookupCache = {}    # lookup cache with relay descriptors
    self._isReset = False               # internal flag for tracking resets
    self._lastNewnym = 0                # time we last sent a NEWNYM signal
    
    # Logs issues and notices when fetching the path prefix if true. This is
    # only done once for the duration of the application to avoid pointless
    # messages.
    self._pathPrefixLogging = True
  
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
      self.connLock.acquire()
      
      if self.controller: self.close() # shut down current connection
      self.controller = controller
      log.info("Stem connected to tor version %s" % self.controller.get_version())
      
      self.controller.add_event_listener(self.ns_event, stem.control.EventType.NS)
      self.controller.add_event_listener(self.new_consensus_event, stem.control.EventType.NEWCONSENSUS)
      self.controller.add_event_listener(self.new_desc_event, stem.control.EventType.NEWDESC)
      
      # reset caches for ip -> fingerprint lookups
      self._fingerprintMappings = None
      self._fingerprintLookupCache = {}
      self._nicknameLookupCache = {}
      self._nicknameToFpLookupCache = {}
      self._addressLookupCache = {}
      self._consensusLookupCache = {}
      self._descriptorLookupCache = {}
      
      # time that we sent our last newnym signal
      self._lastNewnym = 0
      
      self.connLock.release()
  
  def close(self):
    """
    Closes the current stem instance and notifies listeners.
    """
    
    self.connLock.acquire()
    if self.controller:
      self.controller.close()
      self.controller = None
      self.connLock.release()
    else: self.connLock.release()
  
  def getController(self):
    return self.controller

  def isAlive(self):
    """
    Returns True if this has been initialized with a working stem instance,
    False otherwise.
    """
    
    self.connLock.acquire()
    
    result = False
    if self.controller:
      if self.controller.is_alive(): result = True
      else: self.close()
    
    self.connLock.release()
    return result
  
  def getInfo(self, param, default = UNDEFINED):
    """
    Queries the control port for the given GETINFO option, providing the
    default if the response is undefined or fails for any reason (error
    response, control port closed, initiated, etc).
    
    Arguments:
      param   - GETINFO option to be queried
      default - result if the query fails
    """
    
    self.connLock.acquire()
    
    try:
      if not self.isAlive():
        if default != UNDEFINED:
          return default
        else:
          raise stem.SocketClosed()
      
      if default != UNDEFINED:
        return self.controller.get_info(param, default)
      else:
        return self.controller.get_info(param)
    except stem.SocketClosed, exc:
      self.close()
      raise exc
    finally:
      self.connLock.release()
  
  def getOption(self, param, default = UNDEFINED, multiple = False):
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
    
    self.connLock.acquire()
    
    try:
      if not self.isAlive():
        if default != UNDEFINED:
          return default
        else:
          raise stem.SocketClosed()
      
      if default != UNDEFINED:
        return self.controller.get_conf(param, default, multiple)
      else:
        return self.controller.get_conf(param, multiple = multiple)
    except stem.SocketClosed, exc:
      self.close()
      raise exc
    finally:
      self.connLock.release()
  
  def setOption(self, param, value = None):
    """
    Issues a SETCONF to set the given option/value pair. An exeptions raised
    if it fails to be set. If no value is provided then this sets the option to
    0 or NULL.
    
    Arguments:
      param - configuration option to be set
      value - value to set the parameter to (this can be either a string or a
              list of strings)
    """
    
    self.setOptions(((param, value),))
  
  def setOptions(self, paramList, isReset = False):
    """
    Issues a SETCONF to replace a set of configuration options. This takes a
    list of parameter/new value tuple pairs. Values can be...
    - a string to set a single value
    - a list of strings to set a series of values (for instance the ExitPolicy)
    - None to set the value to 0 or NULL
    
    Arguments:
      paramList - list of parameter/value tuple pairs
      isReset   - issues a RESETCONF instead of SETCONF, causing any None
                  mappings to revert the parameter to its default rather than
                  set it to 0 or NULL
    """
    
    self.connLock.acquire()
    
    try:
      if not self.isAlive():
        raise stem.SocketClosed()
      
      self.controller.set_options(paramList, isReset)
    except stem.SocketClosed, exc:
      self.close()
      raise exc
    finally:
      self.connLock.release()
  
  def saveConf(self):
    """
    Calls tor's SAVECONF method.
    """
    
    self.connLock.acquire()
    
    if self.isAlive():
      self.controller.save_conf()
    
    self.connLock.release()
  
  def sendNewnym(self):
    """
    Sends a newnym request to Tor. These are rate limited so if it occures
    more than once within a ten second window then the second is delayed.
    """
    
    self.connLock.acquire()
    
    if self.isAlive():
      self._lastNewnym = time.time()
      self.controller.signal(stem.Signal.NEWNYM)
    
    self.connLock.release()
  
  def isNewnymAvailable(self):
    """
    True if Tor will immediately respect a newnym request, false otherwise.
    """
    
    if self.isAlive():
      return self.getNewnymWait() == 0
    else: return False
  
  def getNewnymWait(self):
    """
    Provides the number of seconds until a newnym signal would be respected.
    """
    
    # newnym signals can occure at the rate of one every ten seconds
    # TODO: this can't take other controllers into account :(
    return max(0, math.ceil(self._lastNewnym + 10 - time.time()))
  
  def getCircuits(self, default = []):
    """
    This provides a list with tuples of the form:
    (circuitID, status, purpose, (fingerprint1, fingerprint2...))
    
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
          fp = self.getNicknameFingerprint(nickname)
          
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
  
  def getHiddenServicePorts(self, default = []):
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
        result.append(hsPort)
    
    if result:
      return result
    else:
      return default
  
  def getMyBandwidthRate(self, default = None):
    """
    Provides the effective relaying bandwidth rate of this relay. Currently
    this doesn't account for SETCONF events.
    
    Arguments:
      default - result if the query fails
    """
    
    # effective relayed bandwidth is the minimum of BandwidthRate,
    # MaxAdvertisedBandwidth, and RelayBandwidthRate (if set)
    effectiveRate = int(self.getOption("BandwidthRate", None))
    
    relayRate = self.getOption("RelayBandwidthRate", None)
    if relayRate and relayRate != "0":
      effectiveRate = min(effectiveRate, int(relayRate))
    
    maxAdvertised = self.getOption("MaxAdvertisedBandwidth", None)
    if maxAdvertised: effectiveRate = min(effectiveRate, int(maxAdvertised))
    
    if effectiveRate is not None:
      return effectiveRate
    else:
      return default
  
  def getMyBandwidthBurst(self, default = None):
    """
    Provides the effective bandwidth burst rate of this relay. Currently this
    doesn't account for SETCONF events.
    
    Arguments:
      default - result if the query fails
    """
    
    # effective burst (same for BandwidthBurst and RelayBandwidthBurst)
    effectiveBurst = int(self.getOption("BandwidthBurst", None))
    
    relayBurst = self.getOption("RelayBandwidthBurst", None)
    
    if relayBurst and relayBurst != "0":
      effectiveBurst = min(effectiveBurst, int(relayBurst))
    
    if effectiveBurst is not None:
      return effectiveBurst
    else:
      return default
  
  def getMyBandwidthObserved(self, default = None):
    """
    Provides the relay's current observed bandwidth (the throughput determined
    from historical measurements on the client side). This is used in the
    heuristic used for path selection if the measured bandwidth is undefined.
    This is fetched from the descriptors and hence will get stale if
    descriptors aren't periodically updated.
    
    Arguments:
      default - result if the query fails
    """
    
    myFingerprint = self.getInfo("fingerprint", None)
    
    if myFingerprint:
      myDescriptor = self.controller.get_server_descriptor(myFingerprint)
      
      if myDescriptor:
        result = myDescriptor.observed_bandwidth
    
    return default
  
  def getMyBandwidthMeasured(self, default = None):
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
    
    myFingerprint = self.getInfo("fingerprint", None)
    
    if myFingerprint:
      myStatusEntry = self.controller.get_network_status(myFingerprint)
      
      if myStatusEntry and hasattr(myStatusEntry, 'bandwidth'):
        return myStatusEntry.bandwidth
    
    return default
  
  def getMyFlags(self, default = None):
    """
    Provides the flags held by this relay.
    
    Arguments:
      default - result if the query fails or this relay isn't a part of the consensus yet
    """
    
    myFingerprint = self.getInfo("fingerprint", None)
    
    if myFingerprint:
      myStatusEntry = self.controller.get_network_status(myFingerprint)
      
      if myStatusEntry:
        return myStatusEntry.flags

    return default
  
  def getVersion(self):
    """
    Provides the version of our tor instance, this is None if we don't have a
    connection.
    """
    
    self.connLock.acquire()
    
    try:
      return self.controller.get_version()
    except stem.SocketClosed, exc:
      self.close()
      return None
    except:
      return None
    finally:
      self.connLock.release()
  
  def isGeoipUnavailable(self):
    """
    Provides true if we've concluded that our geoip database is unavailable,
    false otherwise.
    """
    
    if self.isAlive():
      return self.controller.is_geoip_unavailable()
    else:
      return False
  
  def getMyUser(self):
    """
    Provides the user this process is running under. If unavailable this
    provides None.
    """
    
    return self.controller.get_user(None)
  
  def getMyFileDescriptorUsage(self):
    """
    Provides the number of file descriptors currently being used by this
    process. This returns None if this can't be determined.
    """
    
    # The file descriptor usage is the size of the '/proc/<pid>/fd' contents
    # http://linuxshellaccount.blogspot.com/2008/06/finding-number-of-open-file-descriptors.html
    # I'm not sure about other platforms (like BSD) so erroring out there.
    
    self.connLock.acquire()
    
    result = None
    if self.isAlive() and proc.is_available():
      myPid = self.controller.get_pid(None)
      
      if myPid:
        try: result = len(os.listdir("/proc/%s/fd" % myPid))
        except: pass
    
    self.connLock.release()
    
    return result
  
  def getMyFileDescriptorLimit(self):
    """
    Provides the maximum number of file descriptors this process can have.
    Only the Tor process itself reliably knows this value, and the option for
    getting this was added in Tor 0.2.3.x-final. If that's unavailable then
    we can only estimate the file descriptor limit based on other factors.
    
    The return result is a tuple of the form:
    (fileDescLimit, isEstimate)
    and if all methods fail then both values are None.
    """
    
    # provides -1 if the query fails
    queriedLimit = self.getInfo("process/descriptor-limit", None)
    
    if queriedLimit != None and queriedLimit != "-1":
      return (int(queriedLimit), False)
    
    torUser = self.getMyUser()
    
    # This is guessing the open file limit. Unfortunately there's no way
    # (other than "/usr/proc/bin/pfiles pid | grep rlimit" under Solaris)
    # to get the file descriptor limit for an arbitrary process.
    
    if torUser == "debian-tor":
      # probably loaded via /etc/init.d/tor which changes descriptor limit
      return (8192, True)
    else:
      # uses ulimit to estimate (-H is for hard limit, which is what tor uses)
      ulimitResults = system.call("ulimit -Hn")
      
      if ulimitResults:
        ulimit = ulimitResults[0].strip()
        
        if ulimit.isdigit():
          return (int(ulimit), True)

    return (None, None)
  
  def getMyDirAuthorities(self):
    """
    Provides a listing of IP/port tuples for the directory authorities we've
    been configured to use. If set in the configuration then these are custom
    authorities, otherwise its an estimate of what Tor has been hardcoded to
    use (unfortunately, this might be out of date).
    """
    
    # There's two configuration options that can overwrite the default
    # authorities: DirServer and AlternateDirAuthority.
    
    # TODO: Both options accept a set of flags to more precisely set what they
    # overwrite. Ideally this would account for these flags to more accurately
    # identify authority connections from relays.
    
    dirServerCfg = self.getOption("DirServer", [], True)
    altDirAuthCfg = self.getOption("AlternateDirAuthority", [], True)
    altAuthoritiesCfg = dirServerCfg + altDirAuthCfg
    
    if altAuthoritiesCfg:
      result = []
      
      # entries are of the form:
      # [nickname] [flags] address:port fingerprint
      for entry in altAuthoritiesCfg:
        locationComp = entry.split()[-2] # address:port component
        result.append(tuple(locationComp.split(":", 1)))
      
      return result
    else:
      return list(DIR_SERVERS)
  
  def getPathPrefix(self):
    """
    Provides the path prefix that should be used for fetching tor resources.
    If undefined and Tor is inside a jail under FreeBsd then this provides the
    jail's path.
    """
    
    # make sure the path prefix is valid and exists (providing a notice if not)
    prefixPath = CONFIG["features.pathPrefix"].strip()
    
    if not prefixPath and os.uname()[0] == "FreeBSD":
      prefixPath = system.get_bsd_jail_path(getConn().controller.get_pid(0))
      
      if prefixPath and self._pathPrefixLogging:
        log.info("Adjusting paths to account for Tor running in a jail at: %s" % prefixPath)
    
    if prefixPath:
      # strips off ending slash from the path
      if prefixPath.endswith("/"): prefixPath = prefixPath[:-1]
      
      # avoid using paths that don't exist
      if self._pathPrefixLogging and prefixPath and not os.path.exists(prefixPath):
        log.notice("The prefix path set in your config (%s) doesn't exist." % prefixPath)
        prefixPath = ""
    
    self._pathPrefixLogging = False # prevents logging if fetched again
    return prefixPath
  
  def getStartTime(self):
    """
    Provides the unix time for when the tor process first started. If this
    can't be determined then this provides None.
    """
    
    try:
      return system.get_start_time(self.controller.get_pid())
    except:
      return None
  
  def isExitingAllowed(self, ipAddress, port):
    """
    Checks if the given destination can be exited to by this relay, returning
    True if so and False otherwise.
    """
    
    self.connLock.acquire()
    
    result = False
    if self.isAlive():
      # If we allow any exiting then this could be relayed DNS queries,
      # otherwise the policy is checked. Tor still makes DNS connections to
      # test when exiting isn't allowed, but nothing is relayed over them.
      # I'm registering these as non-exiting to avoid likely user confusion:
      # https://trac.torproject.org/projects/tor/ticket/965
      
      our_policy = self.getExitPolicy()
      
      if our_policy and our_policy.is_exiting_allowed() and port == "53": result = True
      else: result = our_policy and our_policy.can_exit_to(ipAddress, port)
    
    self.connLock.release()
    
    return result
  
  def getExitPolicy(self):
    """
    Provides an ExitPolicy instance for the head of this relay's exit policy
    chain. If there's no active connection then this provides None.
    """
    
    self.connLock.acquire()
    
    result = None
    if self.isAlive():
      try:
        result = self.controller.get_exit_policy(param)
      except:
        pass
    
    self.connLock.release()
    
    return result
  
  def getConsensusEntry(self, relayFingerprint):
    """
    Provides the most recently available consensus information for the given
    relay. This is none if no such information exists.
    
    Arguments:
      relayFingerprint - fingerprint of the relay
    """
    
    self.connLock.acquire()
    
    result = None
    if self.isAlive():
      if not relayFingerprint in self._consensusLookupCache:
        nsEntry = self.getInfo("ns/id/%s" % relayFingerprint, None)
        self._consensusLookupCache[relayFingerprint] = nsEntry
      
      result = self._consensusLookupCache[relayFingerprint]
    
    self.connLock.release()
    
    return result
  
  def getDescriptorEntry(self, relayFingerprint):
    """
    Provides the most recently available descriptor information for the given
    relay. Unless FetchUselessDescriptors is set this may frequently be
    unavailable. If no such descriptor is available then this returns None.
    
    Arguments:
      relayFingerprint - fingerprint of the relay
    """
    
    self.connLock.acquire()
    
    result = None
    if self.isAlive():
      if not relayFingerprint in self._descriptorLookupCache:
        descEntry = self.getInfo("desc/id/%s" % relayFingerprint, None)
        self._descriptorLookupCache[relayFingerprint] = descEntry
      
      result = self._descriptorLookupCache[relayFingerprint]
    
    self.connLock.release()
    
    return result
  
  def getRelayFingerprint(self, relayAddress, relayPort = None, getAllMatches = False):
    """
    Provides the fingerprint associated with the given address. If there's
    multiple potential matches or the mapping is unknown then this returns
    None. This disambiguates the fingerprint if there's multiple relays on
    the same ip address by several methods, one of them being to pick relays
    we have a connection with.
    
    Arguments:
      relayAddress  - address of relay to be returned
      relayPort     - orport of relay (to further narrow the results)
      getAllMatches - ignores the relayPort and provides all of the
                      (port, fingerprint) tuples matching the given
                      address
    """
    
    self.connLock.acquire()
    
    result = None
    if self.isAlive():
      if getAllMatches:
        # populates the ip -> fingerprint mappings if not yet available
        if self._fingerprintMappings == None:
          self._fingerprintMappings = self._getFingerprintMappings()
        
        if relayAddress in self._fingerprintMappings:
          result = self._fingerprintMappings[relayAddress]
        else: result = []
      else:
        # query the fingerprint if it isn't yet cached
        if not (relayAddress, relayPort) in self._fingerprintLookupCache:
          relayFingerprint = self._getRelayFingerprint(relayAddress, relayPort)
          self._fingerprintLookupCache[(relayAddress, relayPort)] = relayFingerprint
        
        result = self._fingerprintLookupCache[(relayAddress, relayPort)]
    
    self.connLock.release()
    
    return result
  
  def getRelayNickname(self, relayFingerprint):
    """
    Provides the nickname associated with the given relay. This provides None
    if no such relay exists, and "Unnamed" if the name hasn't been set.
    
    Arguments:
      relayFingerprint - fingerprint of the relay
    """
    
    self.connLock.acquire()
    
    result = None
    if self.isAlive():
      # query the nickname if it isn't yet cached
      if not relayFingerprint in self._nicknameLookupCache:
        if relayFingerprint == self.getInfo("fingerprint", None):
          # this is us, simply check the config
          myNickname = self.getOption("Nickname", "Unnamed")
          self._nicknameLookupCache[relayFingerprint] = myNickname
        else:
          # check the consensus for the relay
          nsEntry = self.getConsensusEntry(relayFingerprint)
          
          if nsEntry: relayNickname = nsEntry[2:nsEntry.find(" ", 2)]
          else: relayNickname = None
          
          self._nicknameLookupCache[relayFingerprint] = relayNickname
      
      result = self._nicknameLookupCache[relayFingerprint]
    
    self.connLock.release()
    
    return result
  
  def getRelayExitPolicy(self, relayFingerprint):
    """
    Provides the ExitPolicy instance associated with the given relay. The tor
    consensus entries don't indicate if private addresses are rejected or
    address-specific policies, so this is only used as a fallback if a recent
    descriptor is unavailable. This returns None if unable to determine the
    policy.
    
    Arguments:
      relayFingerprint - fingerprint of the relay
    """
    
    self.connLock.acquire()
    
    result = None
    if self.isAlive():
      # attempts to fetch the policy via the descriptor
      descriptor = self.controller.get_server_descriptor(relayFingerprint, None)
      
      if descriptor:
        result = descriptor.exit_policy
    
    self.connLock.release()
    
    return result
  
  def getRelayAddress(self, relayFingerprint, default = None):
    """
    Provides the (IP Address, ORPort) tuple for a given relay. If the lookup
    fails then this returns the default.
    
    Arguments:
      relayFingerprint - fingerprint of the relay
    """
    
    self.connLock.acquire()
    
    result = default
    if self.isAlive():
      # query the address if it isn't yet cached
      if not relayFingerprint in self._addressLookupCache:
        if relayFingerprint == self.getInfo("fingerprint", None):
          # this is us, simply check the config
          myAddress = self.getInfo("address", None)
          myOrPort = self.getOption("ORPort", None)
          
          if myAddress and myOrPort:
            self._addressLookupCache[relayFingerprint] = (myAddress, myOrPort)
        else:
          # check the consensus for the relay
          nsEntry = self.getConsensusEntry(relayFingerprint)
          
          if nsEntry:
            nsLineComp = nsEntry.split("\n")[0].split(" ")
            
            if len(nsLineComp) >= 8:
              self._addressLookupCache[relayFingerprint] = (nsLineComp[6], nsLineComp[7])
      
      result = self._addressLookupCache.get(relayFingerprint, default)
    
    self.connLock.release()
    
    return result
  
  def getAllRelayAddresses(self, default = {}):
    """
    Provides a mapping of...
    Relay IP Address -> [(ORPort, Fingerprint)...]
    
    for all relays currently in the cached consensus.
    
    Arguments:
      default - value returned if the query fails
    """
    
    self.connLock.acquire()
    
    result = default
    
    if self.isAlive():
      # check both if the cached mappings are unset or blank
      if not self._fingerprintMappings:
        self._fingerprintMappings = self._getFingerprintMappings()
      
      # Make a shallow copy of the results. This doesn't protect the internal
      # listings, but good enough for the moment.
      # TODO: change the [(port, fingerprint)...] lists to tuples?
      if self._fingerprintMappings != {}:
        result = dict(self._fingerprintMappings)
    
    self.connLock.release()
    
    return result
  
  def getNicknameFingerprint(self, relayNickname):
    """
    Provides the fingerprint associated with the given relay. This provides
    None if no such relay exists.
    
    Arguments:
      relayNickname - nickname of the relay
    """
    
    self.connLock.acquire()
    
    result = None
    if self.isAlive():
      if not relayNickname in self._nicknameToFpLookupCache:
        consensusEntry = self.controller.get_network_status(relayNickname, None)
        
        if consensusEntry:
          self._nicknameToFpLookupCache[relayNickname] = consensusEntry.fingerprint
      
      result = self._nicknameToFpLookupCache.get(relayNickname)
    
    self.connLock.release()
    
    return result
  
  def addEventListener(self, listener, *eventTypes):
    """
    Directs further tor controller events to callback functions of the
    listener. If a new control connection is initialized then this listener is
    reattached.
    """
    
    self.connLock.acquire()
    if self.isAlive(): self.controller.add_event_listener(listener, *eventTypes)
    self.connLock.release()
  
  def removeEventListener(self, listener):
    """
    Stops the given event listener from being notified of further events.
    """
    
    self.connLock.acquire()
    if self.isAlive(): self.controller.remove_event_listener(listener)
    self.connLock.release()
  
  def addStatusListener(self, callback):
    """
    Directs further events related to tor's controller status to the callback
    function.
    
    Arguments:
      callback - functor that'll accept the events, expected to be of the form:
                 myFunction(controller, eventType)
    """
    
    self.controller.add_status_listener(callback)
  
  def getControllerEvents(self):
    """
    Provides the events the controller's currently configured to listen for.
    """
    
    return list(self.controllerEvents)
  
  def reload(self, issueSighup = False):
    """
    This resets tor (sending a RELOAD signal to the control port) causing tor's
    internal state to be reset and the torrc reloaded. This can either be done
    by...
      - the controller via a RELOAD signal (default and suggested)
          conn.send_signal("RELOAD")
      - system reload signal (hup)
          pkill -sighup tor
    
    The later isn't really useful unless there's some reason the RELOAD signal
    won't do the trick. Both methods raise an IOError in case of failure.
    
    Arguments:
      issueSighup - issues a sighup rather than a controller RELOAD signal
    """
    
    self.connLock.acquire()
    
    raisedException = None
    if self.isAlive():
      if not issueSighup:
        try:
          self.controller.signal(stem.Signal.RELOAD)
        except Exception, exc:
          # new torrc parameters caused an error (tor's likely shut down)
          raisedException = IOError(str(exc))
      else:
        try:
          # Redirects stderr to stdout so we can check error status (output
          # should be empty if successful). Example error:
          # pkill: 5592 - Operation not permitted
          #
          # note that this may provide multiple errors, even if successful,
          # hence this:
          #   - only provide an error if Tor fails to log a sighup
          #   - provide the error message associated with the tor pid (others
          #     would be a red herring)
          if not system.is_available("pkill"):
            raise IOError("pkill command is unavailable")
          
          self._isReset = False
          pkillCall = os.popen("pkill -sighup ^tor$ 2> /dev/stdout")
          pkillOutput = pkillCall.readlines()
          pkillCall.close()
          
          # Give the sighupTracker a moment to detect the sighup signal. This
          # is, of course, a possible concurrency bug. However I'm not sure
          # of a better method for blocking on this...
          waitStart = time.time()
          while time.time() - waitStart < 1:
            time.sleep(0.1)
            if self._isReset: break
          
          if not self._isReset:
            errorLine, torPid = "", self.controller.get_pid(None)

            if torPid:
              for line in pkillOutput:
                if line.startswith("pkill: %s - " % torPid):
                  errorLine = line
                  break
            
            if errorLine: raise IOError(" ".join(errorLine.split()[3:]))
            else: raise IOError("failed silently")
        except IOError, exc:
          raisedException = exc
    
    self.connLock.release()
    
    if raisedException: raise raisedException
  
  def shutdown(self, force = False):
    """
    Sends a shutdown signal to the attached tor instance. For relays the
    actual shutdown is delayed for thirty seconds unless the force flag is
    given. This raises an IOError if a signal is sent but fails.
    
    Arguments:
      force - triggers an immediate shutdown for relays if True
    """
    
    self.connLock.acquire()
    
    raisedException = None
    if self.isAlive():
      try:
        isRelay = self.getOption("ORPort", None) != None
        
        if force:
          self.controller.signal(stem.Signal.HALT)
        else:
          self.controller.signal(stem.Signal.SHUTDOWN)
        
        # shuts down control connection if we aren't making a delayed shutdown
        if force or not isRelay: self.close()
      except Exception, exc:
        raisedException = IOError(str(exc))
    
    self.connLock.release()
    
    if raisedException: raise raisedException
  
  def ns_event(self, event):
    self._consensusLookupCache = {}
  
  def new_consensus_event(self, event):
    self.connLock.acquire()
    
    # reconstructs consensus based mappings
    self._fingerprintLookupCache = {}
    self._nicknameLookupCache = {}
    self._nicknameToFpLookupCache = {}
    self._addressLookupCache = {}
    self._consensusLookupCache = {}
    
    if self._fingerprintMappings != None:
      self._fingerprintMappings = self._getFingerprintMappings(event.desc)
    
    self.connLock.release()
  
  def new_desc_event(self, event):
    self.connLock.acquire()
    
    myFingerprint = self.getInfo("fingerprint", None)
    desc_fingerprints = [fingerprint for (fingerprint, nickname) in event.relays]
    
    # If we're tracking ip address -> fingerprint mappings then update with
    # the new relays.
    self._fingerprintLookupCache = {}
    self._descriptorLookupCache = {}
    
    if self._fingerprintMappings != None:
      for fingerprint in desc_fingerprints:
        # gets consensus data for the new descriptor
        try: desc = self.controller.get_network_status(fingerprint)
        except stem.ControllerError: continue
        
        # updates fingerprintMappings with new data
        if desc.address in self._fingerprintMappings:
          # if entry already exists with the same orport, remove it
          orportMatch = None
          for entryPort, entryFingerprint in self._fingerprintMappings[desc.address]:
            if entryPort == desc.or_port:
              orportMatch = (entryPort, entryFingerprint)
              break
          
          if orportMatch: self._fingerprintMappings[desc.address].remove(orportMatch)
          
          # add the new entry
          self._fingerprintMappings[desc.address].append((desc.or_port, desc.fingerprint))
        else:
          self._fingerprintMappings[desc.address] = [(desc.or_port, desc.fingerprint)]
    
    self.connLock.release()
  
  def _getFingerprintMappings(self, descriptors = None):
    """
    Provides IP address to (port, fingerprint) tuple mappings for all of the
    currently cached relays.
    
    Arguments:
      descriptors - router status entries (fetched if not provided)
    """
    
    results = {}
    if self.isAlive():
      # fetch the current network status if not provided
      if not descriptors:
        try: descriptors = self.controller.get_network_statuses()
        except stem.ControllerError: descriptors = []
      
      # construct mappings of ips to relay data
      for desc in descriptors:
        results.setdefault(desc.address, []).append((desc.or_port, desc.fingerprint))
    
    return results
  
  def _getRelayFingerprint(self, relayAddress, relayPort):
    """
    Provides the fingerprint associated with the address/port combination.
    
    Arguments:
      relayAddress - address of relay to be returned
      relayPort    - orport of relay (to further narrow the results)
    """
    
    # If we were provided with a string port then convert to an int (so
    # lookups won't mismatch based on type).
    if isinstance(relayPort, str): relayPort = int(relayPort)
    
    # checks if this matches us
    if relayAddress == self.getInfo("address", None):
      if not relayPort or relayPort == self.getOption("ORPort", None):
        return self.getInfo("fingerprint", None)
    
    # if we haven't yet populated the ip -> fingerprint mappings then do so
    if self._fingerprintMappings == None:
      self._fingerprintMappings = self._getFingerprintMappings()
    
    potentialMatches = self._fingerprintMappings.get(relayAddress)
    if not potentialMatches: return None # no relay matches this ip address
    
    if len(potentialMatches) == 1:
      # There's only one relay belonging to this ip address. If the port
      # matches then we're done.
      match = potentialMatches[0]
      
      if relayPort and match[0] != relayPort: return None
      else: return match[1]
    elif relayPort:
      # Multiple potential matches, so trying to match based on the port.
      for entryPort, entryFingerprint in potentialMatches:
        if entryPort == relayPort:
          return entryFingerprint
    
    return None

