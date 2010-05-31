"""
Helper for working with an active tor process. This both provides a wrapper for
accessing TorCtl and notifications of state changes to subscribers. To quickly
fetch a TorCtl instance to experiment with use the following:

>>> import util.torTools
>>> conn = util.torTools.connect()
>>> conn.get_info("version")["version"]
'0.2.1.24'
"""

import os
import time
import socket
import getpass
import thread
import threading

from TorCtl import TorCtl

import log
import sysTools

# enums for tor's controller state:
# TOR_INIT - attached to a new controller
# TOR_RESET - restart or sighup signal received by tor (resetting internal state)
# TOR_CLOSED - control port closed
TOR_INIT, TOR_RESET, TOR_CLOSED = range(1, 4)

# Message logged by default when a controller event type can't be set (message
# has the event type inserted into it). This skips logging entirely if None.
DEFAULT_FAILED_EVENT_ENTRY = (log.WARN, "Unsupported event type: %s")

# TODO: check version when reattaching to controller and if version changes, flush?
# Skips attempting to set events we've failed to set before. This avoids
# logging duplicate warnings but can be problematic if controllers belonging
# to multiple versions of tor are attached, making this unreflective of the
# controller's capabilites. However, this is a pretty bizarre edge case.
DROP_FAILED_EVENTS = True
FAILED_EVENTS = set()

CONTROLLER = None # singleton Controller instance
INCORRECT_PASSWORD_MSG = "Provided passphrase was incorrect"

def makeCtlConn(controlAddr="127.0.0.1", controlPort=9051):
  """
  Opens a socket to the tor controller and queries its authentication type,
  raising an IOError if problems occure. The result of this function is a tuple
  of the TorCtl connection and the authentication type, where the later is one
  of the following:
  "NONE"          - no authentication required
  "PASSWORD"      - requires authentication via a hashed password
  "COOKIE=<FILE>" - requires the specified authentication cookie
  
  Arguments:
    controlAddr - ip address belonging to the controller
    controlPort - port belonging to the controller
  """
  
  try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((controlAddr, controlPort))
    conn = TorCtl.Connection(s)
  except socket.error, exc:
    if "Connection refused" in exc.args:
      # most common case - tor control port isn't available
      raise IOError("Connection refused. Is the ControlPort enabled?")
    else: raise IOError("Failed to establish socket: %s" % exc)
  
  # check PROTOCOLINFO for authentication type
  try:
    authInfo = conn.sendAndRecv("PROTOCOLINFO\r\n")[1][1]
  except TorCtl.ErrorReply, exc:
    raise IOError("Unable to query PROTOCOLINFO for authentication type: %s" % exc)
  
  if authInfo.startswith("AUTH METHODS=NULL"):
    # no authentication required
    return (conn, "NONE")
  elif authInfo.startswith("AUTH METHODS=HASHEDPASSWORD"):
    # password authentication
    return (conn, "PASSWORD")
  elif authInfo.startswith("AUTH METHODS=COOKIE"):
    # cookie authtication, parses authentication cookie path
    start = authInfo.find("COOKIEFILE=\"") + 12
    end = authInfo.find("\"", start)
    return (conn, "COOKIE=%s" % authInfo[start:end])

def initCtlConn(conn, authType="NONE", authVal=None):
  """
  Authenticates to a tor connection. The authentication type can be any of the
  following strings:
  NONE, PASSWORD, COOKIE
  
  if the authentication type is anything other than NONE then either a
  passphrase or path to an authentication cookie is expected. If an issue
  arises this raises either of the following:
    - IOError for failures in reading an authentication cookie
    - TorCtl.ErrorReply for authentication failures
  
  Argument:
    conn     - unauthenticated TorCtl connection
    authType - type of authentication method to use
    authVal  - passphrase or path to authentication cookie
  """
  
  # validates input
  if authType not in ("NONE", "PASSWORD", "COOKIE"):
    # authentication type unrecognized (possibly a new addition to the controlSpec?)
    raise TorCtl.ErrorReply("Unrecognized authentication type: %s" % authType)
  elif authType != "NONE" and authVal == None:
    typeLabel = "passphrase" if authType == "PASSWORD" else "cookie"
    raise TorCtl.ErrorReply("Unable to authenticate: no %s provided" % typeLabel)
  
  authCookie = None
  try:
    if authType == "NONE": conn.authenticate("")
    elif authType == "PASSWORD": conn.authenticate(authVal)
    else:
      authCookie = open(authVal, "r")
      conn.authenticate_cookie(authCookie)
      authCookie.close()
  except TorCtl.ErrorReply, exc:
    if authCookie: authCookie.close()
    issue = str(exc)
    
    # simplifies message if the wrong credentials were provided (common mistake)
    if issue.startswith("515 Authentication failed: "):
      if issue[27:].startswith("Password did not match"):
        issue = "password incorrect"
      elif issue[27:] == "Wrong length on authentication cookie.":
        issue = "cookie value incorrect"
    
    raise TorCtl.ErrorReply("Unable to authenticate: %s" % issue)
  except IOError, exc:
    if authCookie: authCookie.close()
    issue = None
    
    # cleaner message for common errors
    if str(exc).startswith("[Errno 13] Permission denied"): issue = "permission denied"
    elif str(exc).startswith("[Errno 2] No such file or directory"): issue = "file doesn't exist"
    
    # if problem's recognized give concise message, otherwise print exception string
    if issue: raise IOError("Failed to read authentication cookie (%s): %s" % (issue, authVal))
    else: raise IOError("Failed to read authentication cookie: %s" % exc)

def connect(controlAddr="127.0.0.1", controlPort=9051, passphrase=None):
  """
  Convenience method for quickly getting a TorCtl connection. This is very
  handy for debugging or CLI setup, handling setup and prompting for a password
  if necessary (if either none is provided as input or it fails). If any issues
  arise this prints a description of the problem and returns None.
  
  Arguments:
    controlAddr - ip address belonging to the controller
    controlPort - port belonging to the controller
    passphrase  - authentication passphrase (if defined this is used rather
                  than prompting the user)
  """
  
  try:
    conn, authType = makeCtlConn(controlAddr, controlPort)
    authValue = None
    
    if authType == "PASSWORD":
      # password authentication, promting for the password if it wasn't provided
      if passphrase: authValue = passphrase
      else:
        try: authValue = getpass.getpass()
        except KeyboardInterrupt: return None
    elif authType.startswith("COOKIE"):
      authType, authValue = authType.split("=", 1)
    
    initCtlConn(conn, authType, authValue)
    return conn
  except Exception, exc:
    if passphrase and str(exc) == "Unable to authenticate: password incorrect":
      # provide a warining that the provided password didn't work, then try
      # again prompting for the user to enter it
      print INCORRECT_PASSWORD_MSG
      return makeConn(controlAddr, controlPort)
    else:
      print exc
      return None

def getPid(controlPort=9051):
  """
  Attempts to determine the process id for a running tor process, using the
  following:
  1. "pidof tor"
  2. "netstat -npl | grep 127.0.0.1:%s" % <tor control port>
  3. "ps -o pid -C tor"
  
  If pidof or ps promide multiple tor instances then their results are discared
  (since only netstat can differentiate using the control port). This provdes
  None if either no running process exists or it can't be determined.
  
  Arguments:
    controlPort - control port of the tor process if multiple exist
  """
  
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
  
  return None

def getConn():
  """
  Singleton constructor for a Controller. Be aware that this start
  uninitialized, needing a TorCtl instance before it's fully functional.
  """
  
  global CONTROLLER
  if CONTROLLER == None: CONTROLLER = Controller()
  return CONTROLLER

# TODO: sighup notification (and replacement in controller!)
class Controller (TorCtl.PostEventListener):
  """
  TorCtl wrapper providing convenience functions, listener functionality for
  tor's state, and the capability for controller connections to be restarted
  if closed.
  """
  
  def __init__(self):
    TorCtl.PostEventListener.__init__(self)
    self.conn = None                    # None if uninitialized or controller's been closed
    self.connLock = threading.RLock()
    self.listeners = []                 # callback functions for tor's state changes
    self.controllerEvents = {}          # mapping of successfully set controller events to their failure level/msg
    self._isReset = False               # internal flag for tracking resets
    
    # cached information static for a connection (None if unset, "UNKNOWN" if
    # unable to be determined)
    self.pid = None
  
  def init(self, conn):
    """
    Uses the given TorCtl instance for future operations, notifying listeners
    about the change.
    
    Arguments:
      conn - TorCtl instance to be used
    """
    
    if conn.is_live() and conn != self.conn:
      self.connLock.acquire()
      
      if self.conn: self.close() # shut down current connection
      self.conn = conn
      self.conn.add_event_listener(self)
      
      # sets the events listened for by the new controller (incompatable events
      # are dropped with a logged warning)
      self.setControllerEvents(self.controllerEvents)
      
      self.connLock.release()
      
      # notifies listeners that a new controller is available
      thread.start_new_thread(self._notifyStatusListeners, (TOR_INIT,))
  
  def close(self):
    """
    Closes the current TorCtl instance and notifies listeners.
    """
    
    self.connLock.acquire()
    if self.conn:
      self.conn.close()
      self.conn = None
      self.pid = None
      self.connLock.release()
      
      # notifies listeners that the controller's been shut down
      thread.start_new_thread(self._notifyStatusListeners, (TOR_CLOSED,))
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
    default if the response fails for any reason (error response, control port
    closed, ininitiated, etc).
    
    Arguments:
      param       - GETINFO option to be queried
      default     - result if the query fails and exception's suppressed
      suppressExc - suppresses lookup errors (returning the default) if true,
                    otherwises this raises the original exception
    """
    
    self.connLock.acquire()
    
    result, raisedExc = default, None
    if self.isAlive():
      try:
        result = self.conn.get_info(param)[param]
      except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed), exc:
        if type(exc) == TorCtl.TorCtlClosed: self.close()
        raisedExc = exc
    
    self.connLock.release()
    
    if not suppressExc and raisedExc: raise raisedExc
    else: return result
  
  def getOption(self, param, default = None, multiple = False, suppressExc = True):
    """
    Queries the control port for the given configuration option, providing the
    default if the response fails for any reason. If multiple values exist then
    this arbitrarily returns the first unless the multiple flag is set.
    
    Arguments:
      param       - configuration option to be queried
      default     - result if the query fails and exception's suppressed
      multiple    - provides a list of results if true, otherwise this just
                    returns the first value
      suppressExc - suppresses lookup errors (returning the default) if true,
                    otherwises this raises the original exception
    """
    
    self.connLock.acquire()
    
    result, raisedExc = default, None
    if self.isAlive():
      try:
        if multiple: result = self.conn.get_option(param)
        else: result = self.conn.get_option(param)[0][1]
      except (socket.error, TorCtl.ErrorReply, TorCtl.TorCtlClosed), exc:
        if type(exc) == TorCtl.TorCtlClosed: self.close()
        raisedExc = exc
    
    self.connLock.release()
    
    if not suppressExc and raisedExc: raise raisedExc
    else: return result
  
  def getPid(self):
    """
    Provides the pid of the attached tor process (None if no controller exists
    or this can't be determined).
    """
    
    self.connLock.acquire()
    
    if self.pid == "UNKNOWN" or not self.isAlive(): result = None
    elif self.pid: result = self.pid
    else:
      result = getPid(int(self.getOption("ControlPort", 9051)))
      if result: self.pid = result
      else: self.pid = "UNKNOWN"
    
    self.connLock.release()
    
    return result
  
  def addStatusListener(self, callback):
    """
    Directs further events related to tor's controller status to the callback
    function.
    
    Arguments:
      callback - functor that'll accept the events, expected to be of the form:
                 myFunction(controller, eventType)
    """
    
    self.listeners.append(callback)
  
  def removeStatusListener(self, callback):
    """
    Stops listener from being notified of further events. This returns true if a
    listener's removed, false otherwise.
    
    Arguments:
      callback - functor to be removed
    """
    
    if callback in self.listeners:
      self.listeners.remove(callback)
      return True
    else: return False
  
  def setControllerEvents(self, eventsToMsg):
    """
    Sets the events being provided via any associated tor controller, logging
    messages for event types that aren't supported (possibly due to version
    issues). This remembers the successfully set events and tries to apply them
    to any controllers attached later too (again logging and dropping
    unsuccessful event types). This returns the listing of event types that
    were successfully set. If no controller is available or events can't be set
    then this is a no-op.
    
    Arguments:
      eventsToMsg - mapping of event types to a tuple of the (runlevel, msg) it
                    should log in case of failure (uses DEFAULT_FAILED_EVENT_ENTRY
                    if mapped to None)
    """
    
    self.connLock.acquire()
    
    returnVal = []
    if self.isAlive():
      events = set(eventsToMsg.keys())
      unavailableEvents = set()
      
      # removes anything we've already failed to set
      if DROP_FAILED_EVENTS:
        unavailableEvents.update(events.intersection(FAILED_EVENTS))
        events.difference_update(FAILED_EVENTS)
      
      # inital check for event availability
      validEvents = self.getInfo("events/names")
      
      if validEvents:
        validEvents = set(validEvents.split())
        unavailableEvents.update(events.difference(validEvents))
        events.intersection_update(validEvents)
      
      # attempt to set events
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
        # removes failed events and logs warnings
        for eventType in unavailableEvents:
          if eventsToMsg[eventType]:
            lvl, msg = eventsToMsg[eventType]
            log.log(lvl, msg)
          elif DEFAULT_FAILED_EVENT_ENTRY:
            lvl, msg = DEFAULT_FAILED_EVENT_ENTRY
            log.log(lvl, msg % eventType)
          
          del eventsToMsg[eventType]
        
        self.controllerEvents = eventsToMsg
        returnVal = eventsToMsg.keys()
    
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
            errorLine, torPid = "", self.getPid()
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
  
  def msg_event(self, event):
    """
    Listens for reload signal (hup), which is either produced by:
    causing the torrc and internal state to be reset.
    """
    
    if event.level == "NOTICE" and event.msg.startswith("Received reload signal (hup)"):
      self._isReset = True
      thread.start_new_thread(self._notifyStatusListeners, (TOR_RESET,))
  
  def _notifyStatusListeners(self, eventType):
    """
    Sends a notice to all current listeners that a given change in tor's
    controller status has occured.
    
    Arguments:
      eventType - enum representing tor's new status
    """
    
    for callback in self.listeners:
      callback(self, eventType)

