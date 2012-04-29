"""
Helper for working with an active tor process. This both provides a wrapper for
accessing TorCtl and notifications of state changes to subscribers.
"""

import os
import pwd
import time
import math
import socket
import thread
import threading
import Queue

from TorCtl import TorCtl, TorUtil

from util import connections, enum, log, procTools, sysTools, uiTools

# enums for tor's controller state:
# INIT - attached to a new controller
# RESET - received a reset/sighup signal
# CLOSED - control port closed
State = enum.Enum("INIT", "RESET", "CLOSED")

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

# TODO: check version when reattaching to controller and if version changes, flush?
# Skips attempting to set events we've failed to set before. This avoids
# logging duplicate warnings but can be problematic if controllers belonging
# to multiple versions of tor are attached, making this unreflective of the
# controller's capabilites. However, this is a pretty bizarre edge case.
DROP_FAILED_EVENTS = True
FAILED_EVENTS = set()

CONTROLLER = None # singleton Controller instance

# Valid keys for the controller's getInfo cache. This includes static GETINFO
# options (unchangable, even with a SETCONF) and other useful stats
CACHE_ARGS = ("version", "config-file", "exit-policy/default", "fingerprint",
              "config/names", "info/names", "features/names", "events/names",
              "nsEntry", "descEntry", "address", "bwRate", "bwBurst",
              "bwObserved", "bwMeasured", "flags", "pid", "user", "fdLimit",
              "pathPrefix", "startTime", "authorities", "circuits", "hsPorts")
CACHE_GETINFO_PREFIX_ARGS = ("ip-to-country/", )

# Tor has a couple messages (in or/router.c) for when our ip address changes:
# "Our IP Address has changed from <previous> to <current>; rebuilding
#   descriptor (source: <source>)."
# "Guessed our IP address as <current> (source: <source>)."
# 
# It would probably be preferable to use the EXTERNAL_ADDRESS event, but I'm
# not quite sure why it's not provided by check_descriptor_ipaddress_changed
# so erring on the side of inclusiveness by using the notice event instead.
ADDR_CHANGED_MSG_PREFIX = ("Our IP Address has changed from", "Guessed our IP address as")

UNKNOWN = "UNKNOWN" # value used by cached information if undefined
CONFIG = {"torrc.map": {},
          "features.pathPrefix": "",
          "log.torCtlPortClosed": log.NOTICE,
          "log.torGetInfo": log.DEBUG,
          "log.torGetInfoCache": None,
          "log.torGetConf": log.DEBUG,
          "log.torGetConfCache": None,
          "log.torSetConf": log.INFO,
          "log.torPrefixPathInvalid": log.NOTICE,
          "log.bsdJailFound": log.INFO,
          "log.unknownBsdJailId": log.WARN,
          "log.geoipUnavailable": log.WARN}

# events used for controller functionality:
# NOTICE - used to detect when tor is shut down
# NEWDESC, NS, and NEWCONSENSUS - used for cache invalidation
REQ_EVENTS = {"NOTICE": "this will be unable to detect when tor is shut down",
              "NEWDESC": "information related to descriptors will grow stale",
              "NS": "information related to the consensus will grow stale",
              "NEWCONSENSUS": "information related to the consensus will grow stale"}

# number of sequential attempts before we decide that the Tor geoip database
# is unavailable
GEOIP_FAILURE_THRESHOLD = 5

# provides int -> str mappings for torctl event runlevels
TORCTL_RUNLEVELS = dict([(val, key) for (key, val) in TorUtil.loglevels.items()])

# ip address ranges substituted by the 'private' keyword
PRIVATE_IP_RANGES = ("0.0.0.0/8", "169.254.0.0/16", "127.0.0.0/8", "192.168.0.0/16", "10.0.0.0/8", "172.16.0.0/12")

# This prevents controllers from spawning worker threads (and by extension
# notifying status listeners). This is important when shutting down to prevent
# rogue threads from being alive during shutdown.

NO_SPAWN = False

# Flag to indicate if we're handling our first init signal. This is for
# startup performance so we don't introduce a sleep while initializing.
IS_STARTUP_SIGNAL = True

def loadConfig(config):
  config.update(CONFIG)

# TODO: temporary code until this is added to torctl as part of...
# https://trac.torproject.org/projects/tor/ticket/3638
def connect_socket(socketPath="/var/run/tor/control", ConnClass=TorCtl.Connection):
  """
  Connects to a unix domain socket available to controllers (set via tor's
  ControlSocket option). This raises an IOError if unable to do so.

  Arguments:
    socketPath - path of the socket to attach to
    ConnClass  - connection type to instantiate
  """

  try:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(socketPath)
    conn = ConnClass(s)
    conn.authenticate("")
    return conn
  except Exception, exc:
    raise IOError(exc)

def getPid(controlPort=9051, pidFilePath=None):
  """
  Attempts to determine the process id for a running tor process, using the
  following:
  1. GETCONF PidFile
  2. "pgrep -x tor"
  3. "pidof tor"
  4. "netstat -npl | grep 127.0.0.1:%s" % <tor control port>
  5. "ps -o pid -C tor"
  6. "sockstat -4l -P tcp -p %i | grep tor" % <tor control port>
  7. "ps axc | egrep \" tor$\""
  8. "lsof -wnPi | egrep \"^tor.*:%i\"" % <tor control port>
  
  If pidof or ps provide multiple tor instances then their results are
  discarded (since only netstat can differentiate using the control port). This
  provides None if either no running process exists or it can't be determined.
  
  Arguments:
    controlPort - control port of the tor process if multiple exist
    pidFilePath - path to the pid file generated by tor
  """
  
  # attempts to fetch via the PidFile, failing if:
  # - the option is unset
  # - unable to read the file (such as insufficient permissions)
  
  if pidFilePath:
    try:
      pidFile = open(pidFilePath, "r")
      pidEntry = pidFile.readline().strip()
      pidFile.close()
      
      if pidEntry.isdigit(): return pidEntry
    except: pass
  
  # attempts to resolve using pgrep, failing if:
  # - tor is running under a different name
  # - there are multiple instances of tor
  try:
    results = sysTools.call("pgrep -x tor")
    if len(results) == 1 and len(results[0].split()) == 1:
      pid = results[0].strip()
      if pid.isdigit(): return pid
  except IOError: pass
  
  # attempts to resolve using pidof, failing if:
  # - tor's running under a different name
  # - there's multiple instances of tor
  try:
    results = sysTools.call("pidof tor")
    if len(results) == 1 and len(results[0].split()) == 1:
      pid = results[0].strip()
      if pid.isdigit(): return pid
  except IOError: pass
  
  # attempts to resolve using netstat, failing if:
  # - tor's being run as a different user due to permissions
  try:
    results = sysTools.call("netstat -npl | grep 127.0.0.1:%i" % controlPort)
    
    if len(results) == 1:
      results = results[0].split()[6] # process field (ex. "7184/tor")
      pid = results[:results.find("/")]
      if pid.isdigit(): return pid
  except IOError: pass
  
  # attempts to resolve using ps, failing if:
  # - tor's running under a different name
  # - there's multiple instances of tor
  try:
    results = sysTools.call("ps -o pid -C tor")
    if len(results) == 2:
      pid = results[1].strip()
      if pid.isdigit(): return pid
  except IOError: pass
  
  # attempts to resolve using sockstat, failing if:
  # - sockstat doesn't accept the -4 flag (BSD only)
  # - tor is running under a different name
  # - there are multiple instances of Tor, using the
  #   same control port on different addresses.
  # 
  # TODO: the later two issues could be solved by filtering for the control
  # port IP address instead of the process name.
  try:
    results = sysTools.call("sockstat -4l -P tcp -p %i | grep tor" % controlPort)
    if len(results) == 1 and len(results[0].split()) == 7:
      pid = results[0].split()[2]
      if pid.isdigit(): return pid
  except IOError: pass
  
  # attempts to resolve via a ps command that works on the mac (this and lsof
  # are the only resolvers to work on that platform). This fails if:
  # - tor's running under a different name
  # - there's multiple instances of tor
  
  try:
    results = sysTools.call("ps axc | egrep \" tor$\"")
    if len(results) == 1 and len(results[0].split()) > 0:
      pid = results[0].split()[0]
      if pid.isdigit(): return pid
  except IOError: pass
  
  # attempts to resolve via lsof - this should work on linux, mac, and bsd -
  # this fails if:
  # - tor's running under a different name
  # - tor's being run as a different user due to permissions
  # - there are multiple instances of Tor, using the
  #   same control port on different addresses.
  
  try:
    results = sysTools.call("lsof -wnPi | egrep \"^tor.*:%i\"" % controlPort)
    
    # This can result in multiple entries with the same pid (from the query
    # itself). Checking all lines to see if they're in agreement about the pid.
    
    if results:
      pid = ""
      
      for line in results:
        lineComp = line.split()
        
        if len(lineComp) >= 2 and (not pid or lineComp[1] == pid):
          pid = lineComp[1]
        else: raise IOError
      
      if pid.isdigit(): return pid
  except IOError: pass
  
  return None

def getBsdJailId():
  """
  Get the FreeBSD jail id for the monitored Tor process.
  """
  
  # Output when called from a FreeBSD jail or when Tor isn't jailed:
  #   JID
  #    0
  # 
  # Otherwise it's something like:
  #   JID
  #    1
  
  torPid = getConn().getMyPid()
  psOutput = sysTools.call("ps -p %s -o jid" % torPid)
  
  if len(psOutput) == 2 and len(psOutput[1].split()) == 1:
    jid = psOutput[1].strip()
    if jid.isdigit(): return int(jid)
  
  log.log(CONFIG["log.unknownBsdJailId"], "Failed to figure out the FreeBSD jail id. Assuming 0.")
  return 0

def parseVersion(versionStr):
  """
  Parses the given version string into its expected components, for instance...
  '0.2.2.13-alpha (git-feb8c1b5f67f2c6f)'
  
  would provide:
  (0, 2, 2, 13, 'alpha')
  
  If the input isn't recognized then this returns None.
  
  Arguments:
    versionStr - version string to be parsed
  """
  
  # crops off extra arguments, for instance:
  # '0.2.2.13-alpha (git-feb8c1b5f67f2c6f)' -> '0.2.2.13-alpha'
  versionStr = versionStr.split()[0]
  
  result = None
  if versionStr.count(".") in (2, 3):
    # parses the optional suffix ('alpha', 'release', etc)
    if versionStr.count("-") == 1:
      versionStr, versionSuffix = versionStr.split("-")
    else: versionSuffix = ""
    
    # Parses the numeric portion of the version. This can have three or four
    # entries depending on if an optional patch level was provided.
    try:
      versionComp = [int(entry) for entry in versionStr.split(".")]
      if len(versionComp) == 3: versionComp += [0]
      result = tuple(versionComp + [versionSuffix])
    except ValueError: pass
  
  return result

def isVersion(myVersion, minVersion):
  """
  Checks if the given version meets a given minimum. Both arguments are
  expected to be version tuples. To get this from a version string use the
  parseVersion function.
  
  Arguments:
    myVersion  - tor version tuple
    minVersion - version tuple to be checked against
  """
  
  if myVersion[:4] == minVersion[:4]:
    return True # versions match
  else:
    # compares each of the numeric portions of the version
    for i in range(4):
      myVal, minVal = myVersion[i], minVersion[i]
      
      if myVal > minVal: return True
      elif myVal < minVal: return False
    
    return True # versions match (should have been caught above...)

def isTorRunning():
  """
  Simple check for if a tor process is running. If this can't be determined
  then this returns False.
  """
  
  # Linux and the BSD families have different variants of ps. Guess based on
  # os.uname() results which to try first, then fall back to the other.
  #
  # Linux
  #   -A          - Select all processes. Identical to -e.
  #   -co command - Shows just the base command.
  #
  # Mac / BSD
  #   -a        - Display information about other users' processes as well as
  #               your own.
  #   -o ucomm= - Shows just the ucomm attribute ("name to be used for
  #               accounting")
  
  primaryResolver, secondaryResolver = "ps -A co command", "ps -ao ucomm="
  
  if os.uname()[0] in ("Darwin", "FreeBSD", "OpenBSD"):
    primaryResolver, secondaryResolver = secondaryResolver, primaryResolver
  
  commandResults = sysTools.call(primaryResolver)
  if not commandResults:
    commandResults = sysTools.call(secondaryResolver)
  
  if commandResults:
    for cmd in commandResults:
      if cmd.strip() == "tor": return True
  
  return False

# ============================================================
# TODO: Remove when TorCtl can handle multiple auth methods
# https://trac.torproject.org/projects/tor/ticket/3958
#
# The following is a hacked up version of the fix in that ticket.
# ============================================================

class FixedConnection(TorCtl.Connection):
  def __init__(self, sock):
    TorCtl.Connection.__init__(self, sock)
    self._authTypes = []
    
  def get_auth_types(self):
    """
    Provides the list of authentication types used for the control port. Each
    are members of the AUTH_TYPE enumeration and return results will always
    have at least one result. This raises an IOError if the query to
    PROTOCOLINFO fails.
    """

    if not self._authTypes:
      # check PROTOCOLINFO for authentication type
      try:
        authInfo = self.sendAndRecv("PROTOCOLINFO\r\n")[1][1]
      except Exception, exc:
        if exc.message: excMsg = ": %s" % exc
        else: excMsg = ""
        raise IOError("Unable to query PROTOCOLINFO for the authentication type%s" % excMsg)

      # parses the METHODS and COOKIEFILE entries for details we need to
      # authenticate

      authTypes, cookiePath = [], None

      for entry in authInfo.split():
        if entry.startswith("METHODS="):
          # Comma separated list of our authentication types. If we have
          # multiple then any of them will work.

          methodsEntry = entry[8:]

          for authEntry in methodsEntry.split(","):
            if authEntry == "NULL":
              authTypes.append(TorCtl.AUTH_TYPE.NONE)
            elif authEntry == "HASHEDPASSWORD":
              authTypes.append(TorCtl.AUTH_TYPE.PASSWORD)
            elif authEntry == "COOKIE":
              authTypes.append(TorCtl.AUTH_TYPE.COOKIE)
            else:
              # not of a recognized authentication type (new addition to the
              # control-spec?)

              log.log(log.INFO, "Unrecognized authentication type: %s" % authEntry)
        elif entry.startswith("COOKIEFILE=\"") and entry.endswith("\""):
          # Quoted path of the authentication cookie. This only exists if we're
          # using cookie auth and, of course, doesn't account for chroot.

          cookiePath = entry[12:-1]

      # There should always be a AUTH METHODS entry. If we didn't then throw a
      # wobbly.

      if not authTypes:
        raise IOError("PROTOCOLINFO response didn't include any authentication methods")

      self._authType = authTypes[0]
      self._authTypes = authTypes
      self._cookiePath = cookiePath

    return list(self._authTypes)

  def authenticate(self, secret=""):
    """
    Authenticates to the control port. If an issue arises this raises either of
    the following:
      - IOError for failures in reading an authentication cookie or querying
        PROTOCOLINFO.
      - TorCtl.ErrorReply for authentication failures or if the secret is
        undefined when using password authentication
    """

    # fetches authentication type and cookie path if still unloaded
    if not self._authTypes: self.get_auth_types()

    # validates input
    if TorCtl.AUTH_TYPE.PASSWORD in self._authTypes and secret == "":
      raise TorCtl.ErrorReply("Unable to authenticate: no passphrase provided")

    # tries each of our authentication methods, throwing the last exception if
    # they all fail

    raisedExc = None
    for authMethod in self._authTypes:
      authCookie = None
      try:
        if authMethod == TorCtl.AUTH_TYPE.NONE:
          self.authenticate_password("")
        elif authMethod == TorCtl.AUTH_TYPE.PASSWORD:
          self.authenticate_password(secret)
        else:
          authCookie = open(self._cookiePath, "r")
          self.authenticate_cookie(authCookie)
          authCookie.close()

        # Did the above raise an exception? No? Cool, we're done.
        return
      except TorCtl.ErrorReply, exc:
        if authCookie: authCookie.close()
        issue = str(exc)

        # simplifies message if the wrong credentials were provided (common
        # mistake)
        if issue.startswith("515 Authentication failed: "):
          if issue[27:].startswith("Password did not match"):
            issue = "password incorrect"
          elif issue[27:] == "Wrong length on authentication cookie.":
            issue = "cookie value incorrect"

        raisedExc = TorCtl.ErrorReply("Unable to authenticate: %s" % issue)
      except IOError, exc:
        if authCookie: authCookie.close()
        issue = None

        # cleaner message for common errors
        if str(exc).startswith("[Errno 13] Permission denied"):
          issue = "permission denied"
        elif str(exc).startswith("[Errno 2] No such file or directory"):
          issue = "file doesn't exist"

        # if problem's recognized give concise message, otherwise print exception
        # string
        if issue: raisedExc = IOError("Failed to read authentication cookie (%s): %s" % (issue, self._cookiePath))
        else: raisedExc = IOError("Failed to read authentication cookie: %s" % exc)

    # if we got to this point then we failed to authenticate and should have a
    # raisedExc

    if raisedExc: raise raisedExc

def preauth_connect_alt(controlAddr="127.0.0.1", controlPort=9051,
                    ConnClass=FixedConnection):
  """
  Provides an uninitiated torctl connection components for the control port,
  returning a tuple of the form...
  (torctl connection, authTypes, authValue)

  The authValue corresponds to the cookie path if using an authentication
  cookie, otherwise this is the empty string. This raises an IOError in case
  of failure.

  Arguments:
    controlAddr - ip address belonging to the controller
    controlPort - port belonging to the controller
    ConnClass  - connection type to instantiate
  """

  conn = None
  try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((controlAddr, controlPort))
    conn = ConnClass(s)
    authTypes, authValue = conn.get_auth_types(), ""

    if TorCtl.AUTH_TYPE.COOKIE in authTypes:
      authValue = conn.get_auth_cookie_path()

    return (conn, authTypes, authValue)
  except socket.error, exc:
    if conn: conn.close()

    if "Connection refused" in exc.args:
      # most common case - tor control port isn't available
      raise IOError("Connection refused. Is the ControlPort enabled?")

    raise IOError("Failed to establish socket: %s" % exc)
  except Exception, exc:
    if conn: conn.close()
    raise IOError(exc)

# ============================================================

def getConn():
  """
  Singleton constructor for a Controller. Be aware that this starts as being
  uninitialized, needing a TorCtl instance before it's fully functional.
  """
  
  global CONTROLLER
  if CONTROLLER == None: CONTROLLER = Controller()
  return CONTROLLER

class Controller(TorCtl.PostEventListener):
  """
  TorCtl wrapper providing convenience functions, listener functionality for
  tor's state, and the capability for controller connections to be restarted
  if closed.
  """
  
  def __init__(self):
    TorCtl.PostEventListener.__init__(self)
    self.conn = None                    # None if uninitialized or controller's been closed
    self.connLock = threading.RLock()
    self.eventListeners = []            # instances listening for tor controller events
    self.torctlListeners = []           # callback functions for TorCtl events
    self.statusListeners = []           # callback functions for tor's state changes
    self.controllerEvents = []          # list of successfully set controller events
    self._fingerprintMappings = None    # mappings of ip -> [(port, fingerprint), ...]
    self._fingerprintLookupCache = {}   # lookup cache with (ip, port) -> fingerprint mappings
    self._fingerprintsAttachedCache = None # cache of relays we're connected to
    self._nicknameLookupCache = {}      # lookup cache with fingerprint -> nickname mappings
    self._nicknameToFpLookupCache = {}  # lookup cache with nickname -> fingerprint mappings
    self._addressLookupCache = {}       # lookup cache with fingerprint -> (ip address, or port) mappings
    self._consensusLookupCache = {}     # lookup cache with network status entries
    self._descriptorLookupCache = {}    # lookup cache with relay descriptors
    self._isReset = False               # internal flag for tracking resets
    self._status = State.CLOSED         # current status of the attached control port
    self._statusTime = 0                # unix time-stamp for the duration of the status
    self._lastNewnym = 0                # time we last sent a NEWNYM signal
    self.lastHeartbeat = 0              # time of the last tor event
    
    # Status signaling for when tor starts, stops, or is reset is done via
    # enquing the signal then spawning a handler thread. This is to provide
    # safety in race conditions, for instance if we sighup with a torrc that
    # causes tor to crash then we'll get both an INIT and CLOSED signal. It's
    # important in those cases that listeners get the correct signal last (in
    # that case CLOSED) so they aren't confused about what tor's current state
    # is.
    self._notificationQueue = Queue.Queue()
    
    self._exitPolicyChecker = None
    self._isExitingAllowed = False
    self._exitPolicyLookupCache = {}    # mappings of ip/port tuples to if they were accepted by the policy or not
    
    # Logs issues and notices when fetching the path prefix if true. This is
    # only done once for the duration of the application to avoid pointless
    # messages.
    self._pathPrefixLogging = True
    
    # cached parameters for GETINFO and custom getters (None if unset or
    # possibly changed)
    self._cachedParam = {}
    
    # cached GETCONF parameters, entries consisting of:
    # (option, fetch_type) => value
    self._cachedConf = {}
    
    # directs TorCtl to notify us of events
    TorUtil.logger = self
    TorUtil.loglevel = "DEBUG"
    
    # tracks the number of sequential geoip lookup failures
    self.geoipFailureCount = 0
  
  def init(self, conn=None):
    """
    Uses the given TorCtl instance for future operations, notifying listeners
    about the change.
    
    Arguments:
      conn - TorCtl instance to be used, if None then a new instance is fetched
             via the connect function
    """
    
    if conn == None:
      conn = TorCtl.connect()
      
      if conn == None: raise ValueError("Unable to initialize TorCtl instance.")
    
    if conn.is_live() and conn != self.conn:
      self.connLock.acquire()
      
      if self.conn: self.close() # shut down current connection
      self.conn = conn
      self.conn.add_event_listener(self)
      for listener in self.eventListeners: self.conn.add_event_listener(listener)
      
      # registers this as our first heartbeat
      self._updateHeartbeat()
      
      # reset caches for ip -> fingerprint lookups
      self._fingerprintMappings = None
      self._fingerprintLookupCache = {}
      self._fingerprintsAttachedCache = None
      self._nicknameLookupCache = {}
      self._nicknameToFpLookupCache = {}
      self._addressLookupCache = {}
      self._consensusLookupCache = {}
      self._descriptorLookupCache = {}
      
      self._exitPolicyChecker = self.getExitPolicy()
      self._isExitingAllowed = self._exitPolicyChecker.isExitingAllowed()
      self._exitPolicyLookupCache = {}
      
      # sets the events listened for by the new controller (incompatible events
      # are dropped with a logged warning)
      self.setControllerEvents(self.controllerEvents)
      
      self._status = State.INIT
      self._statusTime = time.time()
      
      # time that we sent our last newnym signal
      self._lastNewnym = 0
      
      # notifies listeners that a new controller is available
      if not NO_SPAWN:
        self._notificationQueue.put(State.INIT)
        thread.start_new_thread(self._notifyStatusListeners, ())
      
      self.connLock.release()
  
  def close(self):
    """
    Closes the current TorCtl instance and notifies listeners.
    """
    
    self.connLock.acquire()
    if self.conn:
      self.conn.close()
      self.conn = None
      
      self._status = State.CLOSED
      self._statusTime = time.time()
      
      # notifies listeners that the controller's been shut down
      if not NO_SPAWN:
        self._notificationQueue.put(State.CLOSED)
        thread.start_new_thread(self._notifyStatusListeners, ())
      
      self.connLock.release()
    else: self.connLock.release()
  
  def isAlive(self):
    """
    Returns True if this has been initialized with a working TorCtl instance,
    False otherwise.
    """
    
    self.connLock.acquire()
    
    result = False
    if self.conn:
      if self.conn.is_live(): result = True
      else: self.close()
    
    self.connLock.release()
    return result
  
  def getHeartbeat(self):
    """
    Provides the time of the last registered tor event (if listening for BW
    events then this should occure every second if relay's still responsive).
    This returns zero if this has never received an event.
    """
    
    return self.lastHeartbeat
  
  def getTorCtl(self):
    """
    Provides the current TorCtl connection. If unset or closed then this
    returns None.
    """
    
    self.connLock.acquire()
    result = None
    if self.isAlive(): result = self.conn
    self.connLock.release()
    
    return result
  
  def getInfo(self, param, default = None, suppressExc = True):
    """
    Queries the control port for the given GETINFO option, providing the
    default if the response is undefined or fails for any reason (error
    response, control port closed, initiated, etc).
    
    Arguments:
      param       - GETINFO option to be queried
      default     - result if the query fails and exception's suppressed
      suppressExc - suppresses lookup errors (returning the default) if true,
                    otherwise this raises the original exception
    """
    
    self.connLock.acquire()
    
    isGeoipRequest = param.startswith("ip-to-country/")
    
    # checks if this is an arg caching covers
    isCacheArg = param in CACHE_ARGS
    
    if not isCacheArg:
      for prefix in CACHE_GETINFO_PREFIX_ARGS:
        if param.startswith(prefix):
          isCacheArg = True
          break
    
    startTime = time.time()
    result, raisedExc, isFromCache = default, None, False
    if self.isAlive():
      cachedValue = self._cachedParam.get(param)
      
      if isCacheArg and cachedValue:
        result = cachedValue
        isFromCache = True
      elif isGeoipRequest and self.isGeoipUnavailable():
        # the geoip database aleady looks to be unavailable - abort the request
        raisedExc = TorCtl.ErrorReply("Tor geoip database is unavailable.")
      else:
        try:
          getInfoVal = self.conn.get_info(param)[param]
          if getInfoVal != None: result = getInfoVal
          if isGeoipRequest: self.geoipFailureCount = -1
        except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed), exc:
          if type(exc) == TorCtl.TorCtlClosed: self.close()
          raisedExc = exc
          
          if isGeoipRequest and not self.geoipFailureCount == -1:
            self.geoipFailureCount += 1
            
            if self.geoipFailureCount == GEOIP_FAILURE_THRESHOLD:
              log.log(CONFIG["log.geoipUnavailable"], "Tor geoip database is unavailable.")
    
    if isCacheArg and result and not isFromCache:
      self._cachedParam[param] = result
    
    if isFromCache:
      msg = "GETINFO %s (cache fetch)" % param
      log.log(CONFIG["log.torGetInfoCache"], msg)
    else:
      msg = "GETINFO %s (runtime: %0.4f)" % (param, time.time() - startTime)
      log.log(CONFIG["log.torGetInfo"], msg)
    
    self.connLock.release()
    
    if not suppressExc and raisedExc: raise raisedExc
    else: return result
  
  def getOption(self, param, default = None, multiple = False, suppressExc = True):
    """
    Queries the control port for the given configuration option, providing the
    default if the response is undefined or fails for any reason. If multiple
    values exist then this arbitrarily returns the first unless the multiple
    flag is set.
    
    Arguments:
      param       - configuration option to be queried
      default     - result if the query fails and exception's suppressed
      multiple    - provides a list with all returned values if true, otherwise
                    this just provides the first result
      suppressExc - suppresses lookup errors (returning the default) if true,
                    otherwise this raises the original exception
    """
    
    fetchType = "list" if multiple else "str"
    
    if param in CONFIG["torrc.map"]:
      # This is among the options fetched via a special command. The results
      # are a set of values that (hopefully) contain the one we were
      # requesting.
      configMappings = self._getOption(CONFIG["torrc.map"][param], default, "map", suppressExc)
      if param in configMappings:
        if fetchType == "list": return configMappings[param]
        else: return configMappings[param][0]
      else: return default
    else:
      return self._getOption(param, default, fetchType, suppressExc)
  
  def getOptionMap(self, param, default = None, suppressExc = True):
    """
    Queries the control port for the given configuration option, providing back
    a mapping of config options to a list of the values returned.
    
    There's three use cases for GETCONF:
    - a single value is provided
    - multiple values are provided for the option queried
    - a set of options that weren't necessarily requested are returned (for
      instance querying HiddenServiceOptions gives HiddenServiceDir,
      HiddenServicePort, etc)
    
    The vast majority of the options fall into the first two catagories, in
    which case calling getOption is sufficient. However, for the special
    options that give a set of values this provides back the full response. As
    of tor version 0.2.1.25 HiddenServiceOptions was the only option like this.
    
    The getOption function accounts for these special mappings, and the only
    advantage to this funtion is that it provides all related values in a
    single response.
    
    Arguments:
      param       - configuration option to be queried
      default     - result if the query fails and exception's suppressed
      suppressExc - suppresses lookup errors (returning the default) if true,
                    otherwise this raises the original exception
    """
    
    return self._getOption(param, default, "map", suppressExc)
  
  # TODO: cache isn't updated (or invalidated) during SETCONF events:
  # https://trac.torproject.org/projects/tor/ticket/1692
  def _getOption(self, param, default, fetchType, suppressExc):
    if not fetchType in ("str", "list", "map"):
      msg = "BUG: unrecognized fetchType in torTools._getOption (%s)" % fetchType
      log.log(log.ERR, msg)
      return default
    
    self.connLock.acquire()
    startTime, raisedExc, isFromCache = time.time(), None, False
    result = {} if fetchType == "map" else []
    
    if self.isAlive():
      if (param.lower(), fetchType) in self._cachedConf:
        isFromCache = True
        result = self._cachedConf[(param.lower(), fetchType)]
      else:
        try:
          if fetchType == "str":
            getConfVal = self.conn.get_option(param)[0][1]
            if getConfVal != None: result = getConfVal
          else:
            for key, value in self.conn.get_option(param):
              if value != None:
                if fetchType == "list": result.append(value)
                elif fetchType == "map":
                  if key in result: result[key].append(value)
                  else: result[key] = [value]
        except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed), exc:
          if type(exc) == TorCtl.TorCtlClosed: self.close()
          result, raisedExc = default, exc
    
    if not isFromCache:
      cacheValue = result
      if fetchType == "list": cacheValue = list(result)
      elif fetchType == "map": cacheValue = dict(result)
      self._cachedConf[(param.lower(), fetchType)] = cacheValue
    
    if isFromCache:
      msg = "GETCONF %s (cache fetch)" % param
      log.log(CONFIG["log.torGetConfCache"], msg)
    else:
      msg = "GETCONF %s (runtime: %0.4f)" % (param, time.time() - startTime)
      log.log(CONFIG["log.torGetConf"], msg)
    
    self.connLock.release()
    
    if not suppressExc and raisedExc: raise raisedExc
    elif result == []: return default
    else: return result
  
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
    
    # constructs the SETCONF string
    setConfComp = []
    
    for param, value in paramList:
      if isinstance(value, list) or isinstance(value, tuple):
        setConfComp += ["%s=\"%s\"" % (param, val.strip()) for val in value]
      elif value:
        setConfComp.append("%s=\"%s\"" % (param, value.strip()))
      else:
        setConfComp.append(param)
    
    setConfStr = " ".join(setConfComp)
    
    startTime, raisedExc = time.time(), None
    if self.isAlive():
      try:
        if isReset:
          self.conn.sendAndRecv("RESETCONF %s\r\n" % setConfStr)
        else:
          self.conn.sendAndRecv("SETCONF %s\r\n" % setConfStr)
        
        # flushing cached values (needed until we can detect SETCONF calls)
        for param, _ in paramList:
          for fetchType in ("str", "list", "map"):
            entry = (param.lower(), fetchType)
            
            if entry in self._cachedConf:
              del self._cachedConf[entry]
          
          # special caches for the exit policy
          if param.lower() == "exitpolicy":
            self._exitPolicyChecker = self.getExitPolicy()
            self._isExitingAllowed = self._exitPolicyChecker.isExitingAllowed()
            self._exitPolicyLookupCache = {}
      except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed), exc:
        if type(exc) == TorCtl.TorCtlClosed: self.close()
        elif type(exc) == TorCtl.ErrorReply:
          excStr = str(exc)
          if excStr.startswith("513 Unacceptable option value: "):
            # crops off the common error prefix
            excStr = excStr[31:]
            
            # Truncates messages like:
            # Value 'BandwidthRate la de da' is malformed or out of bounds.
            # to: Value 'la de da' is malformed or out of bounds.
            if excStr.startswith("Value '"):
              excStr = excStr.replace("%s " % param, "", 1)
            
            exc = TorCtl.ErrorReply(excStr)
        
        raisedExc = exc
    
    self.connLock.release()
    
    excLabel = "failed: \"%s\", " % raisedExc if raisedExc else ""
    msg = "SETCONF %s (%sruntime: %0.4f)" % (setConfStr, excLabel, time.time() - startTime)
    log.log(CONFIG["log.torSetConf"], msg)
    
    if raisedExc: raise raisedExc
  
  def sendNewnym(self):
    """
    Sends a newnym request to Tor. These are rate limited so if it occures
    more than once within a ten second window then the second is delayed.
    """
    
    self.connLock.acquire()
    
    if self.isAlive():
      self._lastNewnym = time.time()
      self.conn.send_signal("NEWNYM")
    
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
    
    return self._getRelayAttr("circuits", default)
  
  def getHiddenServicePorts(self, default = []):
    """
    Provides the target ports hidden services are configured to use.
    
    Arguments:
      default - value provided back if unable to query the hidden service ports
    """
    
    return self._getRelayAttr("hsPorts", default)
  
  def getMyNetworkStatus(self, default = None):
    """
    Provides the network status entry for this relay if available. This is
    occasionally expanded so results may vary depending on tor's version. For
    0.2.2.13 they contained entries like the following:
    
    r caerSidi p1aag7VwarGxqctS7/fS0y5FU+s 9On1TRGCEpljszPpJR1hKqlzaY8 2010-05-26 09:26:06 76.104.132.98 9001 0
    s Fast HSDir Named Running Stable Valid
    w Bandwidth=25300
    p reject 1-65535
    
    Arguments:
      default - result if the query fails
    """
    
    return self._getRelayAttr("nsEntry", default)
  
  def getMyDescriptor(self, default = None):
    """
    Provides the descriptor entry for this relay if available.
    
    Arguments:
      default - result if the query fails
    """
    
    return self._getRelayAttr("descEntry", default)
  
  def getMyBandwidthRate(self, default = None):
    """
    Provides the effective relaying bandwidth rate of this relay. Currently
    this doesn't account for SETCONF events.
    
    Arguments:
      default - result if the query fails
    """
    
    return self._getRelayAttr("bwRate", default)
  
  def getMyBandwidthBurst(self, default = None):
    """
    Provides the effective bandwidth burst rate of this relay. Currently this
    doesn't account for SETCONF events.
    
    Arguments:
      default - result if the query fails
    """
    
    return self._getRelayAttr("bwBurst", default)
  
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
    
    return self._getRelayAttr("bwObserved", default)
  
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
    
    return self._getRelayAttr("bwMeasured", default)
  
  def getMyFlags(self, default = None):
    """
    Provides the flags held by this relay.
    
    Arguments:
      default - result if the query fails or this relay isn't a part of the consensus yet
    """
    
    return self._getRelayAttr("flags", default)
  
  def isVersion(self, minVersionStr):
    """
    Checks if we meet the given version. Recognized versions are of the form:
    <major>.<minor>.<micro>[.<patch>][-<status_tag>]
    
    for instance, "0.2.2.13-alpha" or "0.2.1.5". This raises a ValueError if
    the input isn't recognized, and returns False if unable to fetch our
    instance's version.
    
    According to the spec the status_tag is purely informal, so it's ignored
    in comparisons.
    
    Arguments:
      minVersionStr - version to be compared against
    """
    
    minVersion = parseVersion(minVersionStr)
    
    if minVersion == None:
      raise ValueError("unrecognized version: %s" % minVersionStr)
    
    self.connLock.acquire()
    
    result = False
    if self.isAlive():
      myVersion = parseVersion(self.getInfo("version", ""))
      
      if not myVersion: result = False
      else: result = isVersion(myVersion, minVersion)
    
    self.connLock.release()
    
    return result
  
  def isGeoipUnavailable(self):
    """
    Provides true if we've concluded that our geoip database is unavailable,
    false otherwise.
    """
    
    return self.geoipFailureCount == GEOIP_FAILURE_THRESHOLD
  
  def getMyPid(self):
    """
    Provides the pid of the attached tor process (None if no controller exists
    or this can't be determined).
    """
    
    return self._getRelayAttr("pid", None)
  
  def getMyUser(self):
    """
    Provides the user this process is running under. If unavailable this
    provides None.
    """
    
    return self._getRelayAttr("user", None)
  
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
    if self.isAlive() and procTools.isProcAvailable():
      myPid = self.getMyPid()
      
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
    we estimate the file descriptor limit based on other factors.
    
    The return result is a tuple of the form:
    (fileDescLimit, isEstimate)
    and if all methods fail then both values are None.
    """
    
    return self._getRelayAttr("fdLimit", (None, True))
  
  def getMyDirAuthorities(self):
    """
    Provides a listing of IP/port tuples for the directory authorities we've
    been configured to use. If set in the configuration then these are custom
    authorities, otherwise its an estimate of what Tor has been hardcoded to
    use (unfortunately, this might be out of date).
    """
    
    return self._getRelayAttr("authorities", [])
  
  def getPathPrefix(self):
    """
    Provides the path prefix that should be used for fetching tor resources.
    If undefined and Tor is inside a jail under FreeBsd then this provides the
    jail's path.
    """
    
    return self._getRelayAttr("pathPrefix", "")
  
  def getStartTime(self):
    """
    Provides the unix time for when the tor process first started. If this
    can't be determined then this provides None.
    """
    
    return self._getRelayAttr("startTime", None)
  
  def getStatus(self):
    """
    Provides a tuple consisting of the control port's current status and unix
    time-stamp for when it became this way (zero if no status has yet to be
    set).
    """
    
    return (self._status, self._statusTime)
  
  def isExitingAllowed(self, ipAddress, port):
    """
    Checks if the given destination can be exited to by this relay, returning
    True if so and False otherwise.
    """
    
    self.connLock.acquire()
    
    result = False
    if self.isAlive():
      # query the policy if it isn't yet cached
      if not (ipAddress, port) in self._exitPolicyLookupCache:
        # If we allow any exiting then this could be relayed DNS queries,
        # otherwise the policy is checked. Tor still makes DNS connections to
        # test when exiting isn't allowed, but nothing is relayed over them.
        # I'm registering these as non-exiting to avoid likely user confusion:
        # https://trac.torproject.org/projects/tor/ticket/965
        
        if self._isExitingAllowed and port == "53": isAccepted = True
        else: isAccepted = self._exitPolicyChecker.check(ipAddress, port)
        self._exitPolicyLookupCache[(ipAddress, port)] = isAccepted
      
      result = self._exitPolicyLookupCache[(ipAddress, port)]
    
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
      if self.getOption("ORPort"):
        policyEntries = []
        for exitPolicy in self.getOption("ExitPolicy", [], True):
          policyEntries += [policy.strip() for policy in exitPolicy.split(",")]
        
        # appends the default exit policy
        defaultExitPolicy = self.getInfo("exit-policy/default")
        
        if defaultExitPolicy:
          policyEntries += defaultExitPolicy.split(",")
        
        # construct the policy chain backwards
        policyEntries.reverse()
        
        for entry in policyEntries:
          result = ExitPolicy(entry, result)
        
        # Checks if we are rejecting private connections. If set, this appends
        # 'reject private' and 'reject <my ip>' to the start of our policy chain.
        isPrivateRejected = self.getOption("ExitPolicyRejectPrivate", True)
        
        if isPrivateRejected:
          myAddress = self.getInfo("address")
          if myAddress: result = ExitPolicy("reject %s" % myAddress, result)
          
          result = ExitPolicy("reject private", result)
      else:
        # no ORPort is set so all relaying is disabled
        result = ExitPolicy("reject *:*", None)
    
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
        nsEntry = self.getInfo("ns/id/%s" % relayFingerprint)
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
        descEntry = self.getInfo("desc/id/%s" % relayFingerprint)
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
        if relayFingerprint == self.getInfo("fingerprint"):
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
  
  def getRelayExitPolicy(self, relayFingerprint, allowImprecision = True):
    """
    Provides the ExitPolicy instance associated with the given relay. The tor
    consensus entries don't indicate if private addresses are rejected or
    address-specific policies, so this is only used as a fallback if a recent
    descriptor is unavailable. This returns None if unable to determine the
    policy.
    
    Arguments:
      relayFingerprint - fingerprint of the relay
      allowImprecision - make use of consensus policies as a fallback
    """
    
    self.connLock.acquire()
    
    result = None
    if self.isAlive():
      # attempts to fetch the policy via the descriptor
      descriptor = self.getDescriptorEntry(relayFingerprint)
      
      if descriptor:
        exitPolicyEntries = []
        for line in descriptor.split("\n"):
          if line.startswith("accept ") or line.startswith("reject "):
            exitPolicyEntries.append(line)
        
        # construct the policy chain
        for entry in reversed(exitPolicyEntries):
          result = ExitPolicy(entry, result)
      elif allowImprecision:
        # Falls back to using the consensus entry, which is both less precise
        # and unavailable with older tor versions. This assumes that the relay
        # has ExitPolicyRejectPrivate set and won't include address-specific
        # policies.
        
        consensusLine, relayAddress = None, None
        
        nsEntry = self.getConsensusEntry(relayFingerprint)
        if nsEntry:
          for line in nsEntry.split("\n"):
            if line.startswith("r "):
              # fetch the relay's public address, which we'll need for the
              # ExitPolicyRejectPrivate policy entry
              
              lineComp = line.split(" ")
              if len(lineComp) >= 7 and connections.isValidIpAddress(lineComp[6]):
                relayAddress = lineComp[6]
            elif line.startswith("p "):
              consensusLine = line
              break
        
        if consensusLine:
          acceptance, ports = consensusLine.split(" ")[1:]
          
          # starts with a reject-all for whitelists and accept-all for blacklists
          if acceptance == "accept":
            result = ExitPolicy("reject *:*", None)
          else:
            result = ExitPolicy("accept *:*", None)
          
          # adds each of the ports listed in the consensus
          for port in reversed(ports.split(",")):
            result = ExitPolicy("%s *:%s" % (acceptance, port), result)
          
          # adds ExitPolicyRejectPrivate since this is the default
          if relayAddress: result = ExitPolicy("reject %s" % relayAddress, result)
          result = ExitPolicy("reject private", result)
    
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
        if relayFingerprint == self.getInfo("fingerprint"):
          # this is us, simply check the config
          myAddress = self.getInfo("address")
          myOrPort = self.getOption("ORPort")
          
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
      # determine the fingerprint if it isn't yet cached
      if not relayNickname in self._nicknameToFpLookupCache:
        # Fingerprints are base64 encoded hex with an extra '='. For instance...
        # GETINFO ns/name/torexp2 ->
        #   r torexp2 NPfjt8Vjr+drcbbFLQONN3KapNo LxoHteGax7ZNYh/9g/FF8I617fY 2011-04-27 15:20:35 141.161.20.50 9001 0
        # decode base64 of "NPfjt8Vjr+drcbbFLQONN3KapNo=" ->
        #   "4\xf7\xe3\xb7\xc5c\xaf\xe7kq\xb6\xc5-\x03\x8d7r\x9a\xa4\xda"
        # encode hex of the above ->
        #   "34f7e3b7c563afe76b71b6c52d038d37729aa4da"
        
        relayFingerprint = None
        consensusEntry = self.getInfo("ns/name/%s" % relayNickname)
        if consensusEntry:
          encodedFp = consensusEntry.split()[2]
          decodedFp = (encodedFp + "=").decode('base64').encode('hex')
          relayFingerprint = decodedFp.upper()
        
        self._nicknameToFpLookupCache[relayNickname] = relayFingerprint
      
      result = self._nicknameToFpLookupCache[relayNickname]
    
    self.connLock.release()
    
    return result
  
  def addEventListener(self, listener):
    """
    Directs further tor controller events to callback functions of the
    listener. If a new control connection is initialized then this listener is
    reattached.
    
    Arguments:
      listener - TorCtl.PostEventListener instance listening for events
    """
    
    self.connLock.acquire()
    self.eventListeners.append(listener)
    if self.isAlive(): self.conn.add_event_listener(listener)
    self.connLock.release()
  
  def addTorCtlListener(self, callback):
    """
    Directs further TorCtl events to the callback function. Events are composed
    of a runlevel and message tuple.
    
    Arguments:
      callback - functor that'll accept the events, expected to be of the form:
                 myFunction(runlevel, msg)
    """
    
    self.torctlListeners.append(callback)
  
  def addStatusListener(self, callback):
    """
    Directs further events related to tor's controller status to the callback
    function.
    
    Arguments:
      callback - functor that'll accept the events, expected to be of the form:
                 myFunction(controller, eventType)
    """
    
    self.statusListeners.append(callback)
  
  def removeStatusListener(self, callback):
    """
    Stops listener from being notified of further events. This returns true if a
    listener's removed, false otherwise.
    
    Arguments:
      callback - functor to be removed
    """
    
    if callback in self.statusListeners:
      self.statusListeners.remove(callback)
      return True
    else: return False
  
  def getControllerEvents(self):
    """
    Provides the events the controller's currently configured to listen for.
    """
    
    return list(self.controllerEvents)
  
  def setControllerEvents(self, events):
    """
    Sets the events being requested from any attached tor instance, logging
    warnings for event types that aren't supported (possibly due to version
    issues). Events in REQ_EVENTS will also be included, logging at the error
    level with an additional description in case of failure.
    
    This remembers the successfully set events and tries to request them from
    any tor instance it attaches to in the future too (again logging and
    dropping unsuccessful event types).
    
    This returns the listing of event types that were successfully set. If not
    currently attached to a tor instance then all events are assumed to be ok,
    then attempted when next attached to a control port.
    
    Arguments:
      events - listing of events to be set
    """
    
    self.connLock.acquire()
    
    returnVal = []
    if self.isAlive():
      events = set(events)
      events = events.union(set(REQ_EVENTS.keys()))
      unavailableEvents = set()
      
      # removes anything we've already failed to set
      if DROP_FAILED_EVENTS:
        unavailableEvents.update(events.intersection(FAILED_EVENTS))
        events.difference_update(FAILED_EVENTS)
      
      # initial check for event availability, using the 'events/names' GETINFO
      # option to detect invalid events
      validEvents = self.getInfo("events/names")
      
      if validEvents:
        validEvents = set(validEvents.split())
        unavailableEvents.update(events.difference(validEvents))
        events.intersection_update(validEvents)
      
      # attempt to set events via trial and error
      isEventsSet, isAbandoned = False, False
      
      while not isEventsSet and not isAbandoned:
        try:
          self.conn.set_events(list(events))
          isEventsSet = True
        except TorCtl.ErrorReply, exc:
          msg = str(exc)
          
          if "Unrecognized event" in msg:
            # figure out type of event we failed to listen for
            start = msg.find("event \"") + 7
            end = msg.rfind("\"")
            failedType = msg[start:end]
            
            unavailableEvents.add(failedType)
            events.discard(failedType)
          else:
            # unexpected error, abandon attempt
            isAbandoned = True
        except TorCtl.TorCtlClosed:
          self.close()
          isAbandoned = True
      
      FAILED_EVENTS.update(unavailableEvents)
      if not isAbandoned:
        # logs warnings or errors for failed events
        for eventType in unavailableEvents:
          defaultMsg = DEFAULT_FAILED_EVENT_MSG % eventType
          if eventType in REQ_EVENTS:
            log.log(log.ERR, defaultMsg + " (%s)" % REQ_EVENTS[eventType])
          else:
            log.log(log.WARN, defaultMsg)
        
        self.controllerEvents = list(events)
        returnVal = list(events)
    else:
      # attempts to set the events when next attached to a control port
      self.controllerEvents = list(events)
      returnVal = list(events)
    
    self.connLock.release()
    return returnVal
  
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
          self.conn.send_signal("RELOAD")
          self._cachedParam = {}
          self._cachedConf = {}
        except Exception, exc:
          # new torrc parameters caused an error (tor's likely shut down)
          # BUG: this doesn't work - torrc errors still cause TorCtl to crash... :(
          # http://bugs.noreply.org/flyspray/index.php?do=details&id=1329
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
          if not sysTools.isAvailable("pkill"):
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
            errorLine, torPid = "", self.getMyPid()
            if torPid:
              for line in pkillOutput:
                if line.startswith("pkill: %s - " % torPid):
                  errorLine = line
                  break
            
            if errorLine: raise IOError(" ".join(errorLine.split()[3:]))
            else: raise IOError("failed silently")
          
          self._cachedParam = {}
          self._cachedConf = {}
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
        isRelay = self.getOption("ORPort") != None
        signal = "HALT" if force else "SHUTDOWN"
        self.conn.send_signal(signal)
        
        # shuts down control connection if we aren't making a delayed shutdown
        if force or not isRelay: self.close()
      except Exception, exc:
        raisedException = IOError(str(exc))
    
    self.connLock.release()
    
    if raisedException: raise raisedException
  
  def msg_event(self, event):
    """
    Listens for reload signal (hup), which is either produced by:
    causing the torrc and internal state to be reset.
    """
    
    if event.level == "NOTICE" and event.msg.startswith("Received reload signal (hup)"):
      self.connLock.acquire()
      
      if self.isAlive():
        self._isReset = True
        
        self._status = State.RESET
        self._statusTime = time.time()
        
        if not NO_SPAWN:
          self._notificationQueue.put(State.RESET)
          thread.start_new_thread(self._notifyStatusListeners, ())
      
      self.connLock.release()
  
  def ns_event(self, event):
    self._updateHeartbeat()
    self._consensusLookupCache = {}
    
    myFingerprint = self.getInfo("fingerprint")
    if myFingerprint:
      for ns in event.nslist:
        if ns.idhex == myFingerprint:
          self._cachedParam["nsEntry"] = None
          self._cachedParam["flags"] = None
          self._cachedParam["bwMeasured"] = None
          return
    else:
      self._cachedParam["nsEntry"] = None
      self._cachedParam["flags"] = None
      self._cachedParam["bwMeasured"] = None
  
  def new_consensus_event(self, event):
    self._updateHeartbeat()
    
    self.connLock.acquire()
    
    self._cachedParam["nsEntry"] = None
    self._cachedParam["flags"] = None
    self._cachedParam["bwMeasured"] = None
    
    # reconstructs consensus based mappings
    self._fingerprintLookupCache = {}
    self._fingerprintsAttachedCache = None
    self._nicknameLookupCache = {}
    self._nicknameToFpLookupCache = {}
    self._addressLookupCache = {}
    self._consensusLookupCache = {}
    
    if self._fingerprintMappings != None:
      self._fingerprintMappings = self._getFingerprintMappings(event.nslist)
    
    self.connLock.release()
  
  def new_desc_event(self, event):
    self._updateHeartbeat()
    
    self.connLock.acquire()
    
    myFingerprint = self.getInfo("fingerprint")
    if not myFingerprint or myFingerprint in event.idlist:
      self._cachedParam["descEntry"] = None
      self._cachedParam["bwObserved"] = None
    
    # If we're tracking ip address -> fingerprint mappings then update with
    # the new relays.
    self._fingerprintLookupCache = {}
    self._fingerprintsAttachedCache = None
    self._descriptorLookupCache = {}
    
    if self._fingerprintMappings != None:
      for fingerprint in event.idlist:
        # gets consensus data for the new descriptor
        try: nsLookup = self.conn.get_network_status("id/%s" % fingerprint)
        except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed): continue
        
        if len(nsLookup) > 1:
          # multiple records for fingerprint (shouldn't happen)
          log.log(log.WARN, "Multiple consensus entries for fingerprint: %s" % fingerprint)
          continue
        
        # updates fingerprintMappings with new data
        newRelay = nsLookup[0]
        if newRelay.ip in self._fingerprintMappings:
          # if entry already exists with the same orport, remove it
          orportMatch = None
          for entryPort, entryFingerprint in self._fingerprintMappings[newRelay.ip]:
            if entryPort == newRelay.orport:
              orportMatch = (entryPort, entryFingerprint)
              break
          
          if orportMatch: self._fingerprintMappings[newRelay.ip].remove(orportMatch)
          
          # add the new entry
          self._fingerprintMappings[newRelay.ip].append((newRelay.orport, newRelay.idhex))
        else:
          self._fingerprintMappings[newRelay.ip] = [(newRelay.orport, newRelay.idhex)]
    
    self.connLock.release()
  
  def circ_status_event(self, event):
    self._updateHeartbeat()
    
    # CIRC events aren't required, but if one's received then flush this cache
    # since it uses circuit-status results.
    self.connLock.acquire()
    self._fingerprintsAttachedCache = None
    self.connLock.release()
    
    self._cachedParam["circuits"] = None
  
  def buildtimeout_set_event(self, event):
    self._updateHeartbeat()
  
  def stream_status_event(self, event):
    self._updateHeartbeat()
  
  def or_conn_status_event(self, event):
    self._updateHeartbeat()
  
  def stream_bw_event(self, event):
    self._updateHeartbeat()
  
  def bandwidth_event(self, event):
    self._updateHeartbeat()
  
  def address_mapped_event(self, event):
    self._updateHeartbeat()
  
  def unknown_event(self, event):
    self._updateHeartbeat()
  
  def log(self, level, msg, *args):
    """
    Tracks TorCtl events. Ugly hack since TorCtl/TorUtil.py expects a
    logging.Logger instance.
    """
    
    # notifies listeners of TorCtl events
    for callback in self.torctlListeners: callback(TORCTL_RUNLEVELS[level], msg)
    
    # if the message is informing us of our ip address changing then clear
    # its cached value
    for prefix in ADDR_CHANGED_MSG_PREFIX:
      if msg.startswith(prefix):
        self._cachedParam["address"] = None
        break
  
  def _updateHeartbeat(self):
    """
    Called on any event occurance to note the time it occured.
    """
    
    # alternative is to use the event's timestamp (via event.arrived_at)
    self.lastHeartbeat = time.time()
  
  def _getFingerprintMappings(self, nsList = None):
    """
    Provides IP address to (port, fingerprint) tuple mappings for all of the
    currently cached relays.
    
    Arguments:
      nsList - network status listing (fetched if not provided)
    """
    
    results = {}
    if self.isAlive():
      # fetch the current network status if not provided
      if not nsList:
        try: nsList = self.conn.get_network_status(get_iterator=True)
        except (socket.error, TorCtl.TorCtlClosed, TorCtl.ErrorReply): nsList = []
      
      # construct mappings of ips to relay data
      for relay in nsList:
        if relay.ip in results: results[relay.ip].append((relay.orport, relay.idhex))
        else: results[relay.ip] = [(relay.orport, relay.idhex)]
    
    return results
  
  def _getRelayFingerprint(self, relayAddress, relayPort):
    """
    Provides the fingerprint associated with the address/port combination.
    
    Arguments:
      relayAddress - address of relay to be returned
      relayPort    - orport of relay (to further narrow the results)
    """
    
    # Events can reset _fingerprintsAttachedCache to None, so all uses of this
    # function need to be under the connection lock (skipping that might also
    # scew with the conn usage of this function...)
    
    # If we were provided with a string port then convert to an int (so
    # lookups won't mismatch based on type).
    if isinstance(relayPort, str): relayPort = int(relayPort)
    
    # checks if this matches us
    if relayAddress == self.getInfo("address"):
      if not relayPort or relayPort == self.getOption("ORPort"):
        return self.getInfo("fingerprint")
    
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
    
    # Disambiguates based on our orconn-status and circuit-status results.
    # This only includes relays we're connected to, so chances are pretty
    # slim that we'll still have a problem narrowing this down. Note that we
    # aren't necessarily checking for events that can create new client
    # circuits (so this cache might be a little dirty).
    
    # populates the cache
    if self._fingerprintsAttachedCache == None:
      self._fingerprintsAttachedCache = []
      
      # orconn-status has entries of the form:
      # $33173252B70A50FE3928C7453077936D71E45C52=shiven CONNECTED
      orconnResults = self.getInfo("orconn-status")
      if orconnResults:
        for line in orconnResults.split("\n"):
          self._fingerprintsAttachedCache.append(line[1:line.find("=")])
      
      # circuit-status results (we only make connections to the first hop)
      for _, _, _, path in self.getCircuits():
        self._fingerprintsAttachedCache.append(path[0])
    
    # narrow to only relays we have a connection to
    attachedMatches = []
    for _, entryFingerprint in potentialMatches:
      if entryFingerprint in self._fingerprintsAttachedCache:
        attachedMatches.append(entryFingerprint)
    
    if len(attachedMatches) == 1:
      return attachedMatches[0]
    
    # Highly unlikely, but still haven't found it. Last we'll use some
    # tricks from Mike's ConsensusTracker, excluding possiblities that
    # have...
    # - lost their Running flag
    # - list a bandwidth of 0
    # - have 'opt hibernating' set
    # 
    # This involves constructing a TorCtl Router and checking its 'down'
    # flag (which is set by the three conditions above). This is the last
    # resort since it involves a couple GETINFO queries.
    
    for entryPort, entryFingerprint in list(potentialMatches):
      try:
        nsCall = self.conn.get_network_status("id/%s" % entryFingerprint)
        if not nsCall: raise TorCtl.ErrorReply() # network consensus couldn't be fetched
        nsEntry = nsCall[0]
        
        descEntry = self.getInfo("desc/id/%s" % entryFingerprint)
        if not descEntry: raise TorCtl.ErrorReply() # relay descriptor couldn't be fetched
        descLines = descEntry.split("\n")
        
        isDown = TorCtl.Router.build_from_desc(descLines, nsEntry).down
        if isDown: potentialMatches.remove((entryPort, entryFingerprint))
      except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed): pass
    
    if len(potentialMatches) == 1:
      return potentialMatches[0][1]
    else: return None
  
  def _getRelayAttr(self, key, default, cacheUndefined = True):
    """
    Provides information associated with this relay, using the cached value if
    available and otherwise looking it up.
    
    Arguments:
      key            - parameter being queried (from CACHE_ARGS)
      default        - value to be returned if undefined
      cacheUndefined - caches when values are undefined, avoiding further
                       lookups if true
    """
    
    # Several controller options were added in ticket 2291...
    # https://trac.torproject.org/projects/tor/ticket/2291
    # which is only available with newer tor versions (tested them against
    # Tor v0.2.3.0-alpha-dev). When using these options we need to be
    # especially careful to have good fallback logic.
    
    currentVal = self._cachedParam.get(key)
    if currentVal != None:
      if currentVal == UNKNOWN: return default
      else: return currentVal
    
    self.connLock.acquire()
    
    # Checks that the value is unset and we're running. One exception to this
    # is the pathPrefix which doesn't depend on having a connection and may be
    # needed for the init.
    currentVal, result = self._cachedParam.get(key), None
    if currentVal == None and (self.isAlive() or key == "pathPrefix"):
      # still unset - fetch value
      if key in ("nsEntry", "descEntry"):
        myFingerprint = self.getInfo("fingerprint")
        
        if myFingerprint:
          queryType = "ns" if key == "nsEntry" else "desc"
          queryResult = self.getInfo("%s/id/%s" % (queryType, myFingerprint))
          if queryResult: result = queryResult.split("\n")
      elif key == "bwRate":
        # effective relayed bandwidth is the minimum of BandwidthRate,
        # MaxAdvertisedBandwidth, and RelayBandwidthRate (if set)
        effectiveRate = int(self.getOption("BandwidthRate"))
        
        relayRate = self.getOption("RelayBandwidthRate")
        if relayRate and relayRate != "0":
          effectiveRate = min(effectiveRate, int(relayRate))
        
        maxAdvertised = self.getOption("MaxAdvertisedBandwidth")
        if maxAdvertised: effectiveRate = min(effectiveRate, int(maxAdvertised))
        
        result = effectiveRate
      elif key == "bwBurst":
        # effective burst (same for BandwidthBurst and RelayBandwidthBurst)
        effectiveBurst = int(self.getOption("BandwidthBurst"))
        
        relayBurst = self.getOption("RelayBandwidthBurst")
        if relayBurst and relayBurst != "0":
          effectiveBurst = min(effectiveBurst, int(relayBurst))
        
        result = effectiveBurst
      elif key == "bwObserved":
        for line in self.getMyDescriptor([]):
          if line.startswith("bandwidth"):
            # line should look something like:
            # bandwidth 40960 102400 47284
            comp = line.split()
            
            if len(comp) == 4 and comp[-1].isdigit():
              result = int(comp[-1])
              break
      elif key == "bwMeasured":
        # TODO: Currently there's no client side indication of what type of
        # measurement was used. Include this in results if it's ever available.
        
        for line in self.getMyNetworkStatus([]):
          if line.startswith("w Bandwidth="):
            bwValue = line[12:]
            if bwValue.isdigit(): result = int(bwValue)
            break
      elif key == "flags":
        for line in self.getMyNetworkStatus([]):
          if line.startswith("s "):
            result = line[2:].split()
            break
      elif key == "pid":
        result = self.getInfo("process/pid")
        
        if not result:
          result = getPid(int(self.getOption("ControlPort", 9051)), self.getOption("PidFile"))
      elif key == "user":
        # provides the empty string if the query fails
        queriedUser = self.getInfo("process/user")
        
        if queriedUser != None and queriedUser != "":
          result = queriedUser
        else:
          myPid = self.getMyPid()
          
          if myPid:
            # if proc contents are available then fetch the pid from there and
            # convert it to the username
            if procTools.isProcAvailable():
              try:
                myUid = procTools.getUid(myPid)
                if myUid and myUid.isdigit():
                  result = pwd.getpwuid(int(myUid)).pw_name
              except: pass
            
            # fall back to querying via ps
            if not result:
              psResults = sysTools.call("ps -o user %s" % myPid)
              if psResults and len(psResults) >= 2: result = psResults[1].strip()
      elif key == "fdLimit":
        # provides -1 if the query fails
        queriedLimit = self.getInfo("process/descriptor-limit")
        
        if queriedLimit != None and queriedLimit != "-1":
          result = (int(queriedLimit), False)
        else:
          torUser = self.getMyUser()
          
          # This is guessing the open file limit. Unfortunately there's no way
          # (other than "/usr/proc/bin/pfiles pid | grep rlimit" under Solaris)
          # to get the file descriptor limit for an arbitrary process.
          
          if torUser == "debian-tor":
            # probably loaded via /etc/init.d/tor which changes descriptor limit
            result = (8192, True)
          else:
            # uses ulimit to estimate (-H is for hard limit, which is what tor uses)
            ulimitResults = sysTools.call("ulimit -Hn")
            
            if ulimitResults:
              ulimit = ulimitResults[0].strip()
              if ulimit.isdigit(): result = (int(ulimit), True)
      elif key == "pathPrefix":
        # make sure the path prefix is valid and exists (providing a notice if not)
        prefixPath = CONFIG["features.pathPrefix"].strip()
        
        # adjusts the prefix path to account for jails under FreeBSD (many
        # thanks to Fabian Keil!)
        if not prefixPath and os.uname()[0] == "FreeBSD":
          jid = getBsdJailId()
          if jid != 0:
            # Output should be something like:
            #    JID  IP Address      Hostname      Path
            #      1  10.0.0.2        tor-jail      /usr/jails/tor-jail
            jlsOutput = sysTools.call("jls -j %s" % jid)
            
            if len(jlsOutput) == 2 and len(jlsOutput[1].split()) == 4:
              prefixPath = jlsOutput[1].split()[3]
              
              if self._pathPrefixLogging:
                msg = "Adjusting paths to account for Tor running in a jail at: %s" % prefixPath
                log.log(CONFIG["log.bsdJailFound"], msg)
        
        if prefixPath:
          # strips off ending slash from the path
          if prefixPath.endswith("/"): prefixPath = prefixPath[:-1]
          
          # avoid using paths that don't exist
          if self._pathPrefixLogging and prefixPath and not os.path.exists(prefixPath):
            msg = "The prefix path set in your config (%s) doesn't exist." % prefixPath
            log.log(CONFIG["log.torPrefixPathInvalid"], msg)
            prefixPath = ""
        
        self._pathPrefixLogging = False # prevents logging if fetched again
        result = prefixPath
      elif key == "startTime":
        myPid = self.getMyPid()
        
        if myPid:
          try:
            if procTools.isProcAvailable():
              result = float(procTools.getStats(myPid, procTools.Stat.START_TIME)[0])
            else:
              psCall = sysTools.call("ps -p %s -o etime" % myPid)
              
              if psCall and len(psCall) >= 2:
                etimeEntry = psCall[1].strip()
                result = time.time() - uiTools.parseShortTimeLabel(etimeEntry)
          except: pass
      elif key == "authorities":
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
        else: result = list(DIR_SERVERS)
      elif key == "circuits":
        # Parses our circuit-status results, for instance
        #  91 BUILT $E4AE6E2FE320FBBD31924E8577F3289D4BE0B4AD=Qwerty PURPOSE=GENERAL
        # would belong to a single hop circuit, most likely fetching the
        # consensus via a directory mirror.
        # 
        # The path is made up of "$<fingerprint>[=<nickname]" entries for new
        # versions of Tor, but in versions prior to 0.2.2.1-alpha this was
        # just "$<fingerprint>" OR <nickname>. The dolar sign can't be used in
        # nicknames so this can be used to differentiate.
        
        circStatusResults = self.getInfo("circuit-status")
        
        if circStatusResults == "":
          result = [] # we don't have any circuits
        elif circStatusResults != None:
          result = []
          
          for line in circStatusResults.split("\n"):
            # appends a tuple with the (status, purpose, path)
            lineComp = line.split(" ")
            if len(lineComp) < 3: continue
            
            # The third parameter is *optionally* the path. This is a pita to
            # parse out because we need to identify it verses the key=value
            # entries that might follow. To do this checking if...
            # - it lacks a '=' then it can't be a key=value entry
            # - if it has a '=' but starts with a '$' then this should be a
            #   $fingerprint=nickname entity
            
            if lineComp[2].count("=") == 1 and lineComp[2][0] != "$":
              continue
            
            path = []
            for hopEntry in lineComp[2].split(","):
              if hopEntry[0] == "$": path.append(hopEntry[1:41])
              else:
                relayFingerprint = self.getNicknameFingerprint(hopEntry)
                
                # It shouldn't be possible for this lookup to fail, but we
                # need to fill something (callers won't expect our own client
                # paths to have unknown relays). If this turns out to be wrong
                # then log a warning.
                
                if relayFingerprint: path.append(relayFingerprint)
                else:
                  msg = "Unable to determine the fingerprint for a relay in our own circuit: %s" % hopEntry
                  log.log(log.WARN, msg)
                  path.append("0" * 40)
            
            result.append((int(lineComp[0]), lineComp[1], lineComp[3][8:], tuple(path)))
      elif key == "hsPorts":
        result = []
        hsOptions = self.getOptionMap("HiddenServiceOptions")
        
        if hsOptions and "HiddenServicePort" in hsOptions:
          for hsEntry in hsOptions["HiddenServicePort"]:
            # hidden service port entries are of the form:
            # VIRTPORT [TARGET]
            # with the TARGET being an IP, port, or IP:Port. If the target port
            # isn't defined then uses the VIRTPORT.
            
            hsPort = None
            
            if " " in hsEntry:
              # parses the target, checking if it's a port or IP:Port combination
              hsTarget = hsEntry.split()[1]
              
              if ":" in hsTarget:
                hsPort = hsTarget.split(":")[1] # target is the IP:Port
              elif hsTarget.isdigit():
                hsPort = hsTarget # target is just the port
            else: hsPort = hsEntry # just has the virtual port
            
            if hsPort.isdigit():
              result.append(hsPort)
      
      # cache value
      if result != None: self._cachedParam[key] = result
      elif cacheUndefined: self._cachedParam[key] = UNKNOWN
    
    self.connLock.release()
    
    if result == None or result == UNKNOWN: return default
    else: return result
  
  def _notifyStatusListeners(self):
    """
    Sends a notice to all current listeners that a given change in tor's
    controller status has occurred.
    
    Arguments:
      eventType - enum representing tor's new status
    """
    
    global IS_STARTUP_SIGNAL
    
    # If there's a quick race state (for instance a sighup causing both an init
    # and close event) then give them a moment to enqueue. This way we can
    # coles the events and discard the inaccurate one.
    
    if not IS_STARTUP_SIGNAL:
      time.sleep(0.2)
    else: IS_STARTUP_SIGNAL = False
    
    self.connLock.acquire()
    
    try:
      eventType = self._notificationQueue.get(timeout=0)
      
      # checks that the notice is accurate for our current state
      if self.isAlive() != (eventType in (State.INIT, State.RESET)):
        eventType = None
    except Queue.Empty:
      eventType = None
    
    if eventType:
      # resets cached GETINFO and GETCONF parameters
      self._cachedParam = {}
      self._cachedConf = {}
      
      # gives a notice that the control port has closed
      if eventType == State.CLOSED:
        log.log(CONFIG["log.torCtlPortClosed"], "Tor control port closed")
      
      for callback in self.statusListeners:
        callback(self, eventType)
    
    self.connLock.release()

class ExitPolicyIterator:
  """
  Basic iterator for cycling through ExitPolicy entries.
  """
  
  def __init__(self, head):
    self.head = head
  
  def next(self):
    if self.head:
      lastHead = self.head
      self.head = self.head.nextRule
      return lastHead
    else: raise StopIteration

class ExitPolicy:
  """
  Single rule from the user's exit policy. These are chained together to form
  complete policies.
  """
  
  def __init__(self, ruleEntry, nextRule):
    """
    Exit policy rule constructor.
    
    Arguments:
      ruleEntry - tor exit policy rule (for instance, "reject *:135-139")
      nextRule  - next rule to be checked when queries don't match this policy
    """
    
    # cached summary string
    self.summaryStr = None
    
    # sanitize the input a bit, cleaning up tabs and stripping quotes
    ruleEntry = ruleEntry.replace("\\t", " ").replace("\"", "")
    
    self.ruleEntry = ruleEntry
    self.nextRule = nextRule
    self.isAccept = ruleEntry.startswith("accept")
    
    # strips off "accept " or "reject " and extra spaces
    ruleEntry = ruleEntry[7:].replace(" ", "")
    
    # split ip address (with mask if provided) and port
    if ":" in ruleEntry: entryIp, entryPort = ruleEntry.split(":", 1)
    else: entryIp, entryPort = ruleEntry, "*"
    
    # sets the ip address component
    self.isIpWildcard = entryIp == "*" or entryIp.endswith("/0")
    
    # checks for the private alias (which expands this to a chain of entries)
    if entryIp.lower() == "private":
      entryIp = PRIVATE_IP_RANGES[0]
      
      # constructs the chain backwards (last first)
      lastHop = self.nextRule
      prefix = "accept " if self.isAccept else "reject "
      suffix = ":" + entryPort
      for addr in PRIVATE_IP_RANGES[-1:0:-1]:
        lastHop = ExitPolicy(prefix + addr + suffix, lastHop)
      
      self.nextRule = lastHop # our next hop is the start of the chain
    
    if "/" in entryIp:
      ipComp = entryIp.split("/", 1)
      self.ipAddress = ipComp[0]
      self.ipMask = int(ipComp[1])
    else:
      self.ipAddress = entryIp
      self.ipMask = 32
    
    # constructs the binary address just in case of comparison with a mask
    if self.ipAddress != "*":
      self.ipAddressBin = ""
      for octet in self.ipAddress.split("."):
        # Converts the int to a binary string, padded with zeros. Source:
        # http://www.daniweb.com/code/snippet216539.html
        self.ipAddressBin += "".join([str((int(octet) >> y) & 1) for y in range(7, -1, -1)])
    else:
      self.ipAddressBin = "0" * 32
    
    # sets the port component
    self.minPort, self.maxPort = 0, 0
    self.isPortWildcard = entryPort == "*"
    
    if entryPort != "*":
      if "-" in entryPort:
        portComp = entryPort.split("-", 1)
        self.minPort = int(portComp[0])
        self.maxPort = int(portComp[1])
      else:
        self.minPort = int(entryPort)
        self.maxPort = int(entryPort)
    
    # if both the address and port are wildcards then we're effectively the
    # last entry so cut off the remaining chain
    if self.isIpWildcard and self.isPortWildcard:
      self.nextRule = None
  
  def isExitingAllowed(self):
    """
    Provides true if the policy allows exiting whatsoever, false otherwise.
    """
    
    if self.isAccept: return True
    elif self.isIpWildcard and self.isPortWildcard: return False
    elif not self.nextRule: return False # fell off policy (shouldn't happen)
    else: return self.nextRule.isExitingAllowed()
  
  def check(self, ipAddress, port):
    """
    Checks if the rule chain allows exiting to this address, returning true if
    so and false otherwise.
    """
    
    port = int(port)
    
    # does the port check first since comparing ip masks is more work
    isPortMatch = self.isPortWildcard or (port >= self.minPort and port <= self.maxPort)
    
    if isPortMatch:
      isIpMatch = self.isIpWildcard or self.ipAddress == ipAddress
      
      # expands the check to include the mask if it has one
      if not isIpMatch and self.ipMask != 32:
        inputAddressBin = ""
        for octet in ipAddress.split("."):
          inputAddressBin += "".join([str((int(octet) >> y) & 1) for y in range(7, -1, -1)])
        
        isIpMatch = self.ipAddressBin[:self.ipMask] == inputAddressBin[:self.ipMask]
      
      if isIpMatch: return self.isAccept
    
    # our policy doesn't concern this address, move on to the next one
    if self.nextRule: return self.nextRule.check(ipAddress, port)
    else: return True # fell off the chain without a conclusion (shouldn't happen...)
  
  def getSummary(self):
    """
    Provides a summary description of the policy chain similar to the
    consensus. This excludes entries that don't cover all ips, and is either
    a whitelist or blacklist policy based on the final entry. For instance...
    accept 80, 443        # just accepts ports 80/443
    reject 1-1024, 5555   # just accepts non-privilaged ports, excluding 5555
    """
    
    if not self.summaryStr:
      # determines if we're a whitelist or blacklist
      isWhitelist = False # default in case we don't have a catch-all policy at the end
      
      for rule in self:
        if rule.isIpWildcard and rule.isPortWildcard:
          isWhitelist = not rule.isAccept
          break
      
      # Iterates over the rules and adds the the ports we'll return (ie, allows
      # if a whitelist and rejects if a blacklist). Reguardless of a port's
      # allow/reject policy, all further entries with that port are ignored since
      # policies respect the first matching rule.
      
      displayPorts, skipPorts = [], []
      
      for rule in self:
        if not rule.isIpWildcard: continue
        
        if rule.minPort == rule.maxPort:
          portRange = [rule.minPort]
        else:
          portRange = range(rule.minPort, rule.maxPort + 1)
        
        for port in portRange:
          if port in skipPorts: continue
          
          # if accept + whitelist or reject + blacklist then add
          if rule.isAccept == isWhitelist:
            displayPorts.append(port)
          
          # all further entries with this port are to be ignored
          skipPorts.append(port)
      
      # gets a list of the port ranges
      if displayPorts:
        displayRanges, tmpRange = [], []
        displayPorts.sort()
        displayPorts.append(None) # ending item to include last range in loop
        
        for port in displayPorts:
          if not tmpRange or tmpRange[-1] + 1 == port:
            tmpRange.append(port)
          else:
            if len(tmpRange) > 1:
              displayRanges.append("%i-%i" % (tmpRange[0], tmpRange[-1]))
            else:
              displayRanges.append(str(tmpRange[0]))
            
            tmpRange = [port]
      else:
        # everything for the inverse
        isWhitelist = not isWhitelist
        displayRanges = ["1-65535"]
      
      # constructs the summary string
      labelPrefix = "accept " if isWhitelist else "reject "
      
      self.summaryStr = (labelPrefix + ", ".join(displayRanges)).strip()
    
    return self.summaryStr
  
  def __iter__(self):
    return ExitPolicyIterator(self)
  
  def __str__(self):
    # This provides the actual policy rather than the entry used to construct
    # it so the 'private' keyword is expanded.
    
    acceptanceLabel = "accept" if self.isAccept else "reject"
    
    if self.isIpWildcard:
      ipLabel = "*"
    elif self.ipMask != 32:
      ipLabel = "%s/%i" % (self.ipAddress, self.ipMask)
    else: ipLabel = self.ipAddress
    
    if self.isPortWildcard:
      portLabel = "*"
    elif self.minPort != self.maxPort:
      portLabel = "%i-%i" % (self.minPort, self.maxPort)
    else: portLabel = str(self.minPort)
    
    myPolicy = "%s %s:%s" % (acceptanceLabel, ipLabel, portLabel)
    
    if self.nextRule:
      return myPolicy + ", " + str(self.nextRule)
    else: return myPolicy

