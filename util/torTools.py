"""
Helper for working with an active tor process. This both provides a wrapper for
accessing TorCtl and notifications of state changes to subscribers.
"""

import socket
import getpass

from TorCtl import TorCtl

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
    if issue: raise IOError("Failed to read authentication cookie (%s): %s" % (issue, authCookiePath))
    else: raise IOError("Failed to read authentication cookie: %s" % exc)

def getConn(controlAddr="127.0.0.1", controlPort=9051, passphrase=None):
  """
  Convenience method for quickly getting a TorCtl connection. This is very
  handy for debugging or CLI setup, handling setup and prompting for a password
  if necessary. If any issues arise this prints a description of the problem
  and returns None.
  
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
    print exc
    return None

