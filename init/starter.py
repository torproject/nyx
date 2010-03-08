#!/usr/bin/env python

"""
Command line application for monitoring Tor relays, providing real time status
information. This is the starter for the application, handling and validating
command line parameters.
"""

import sys
import socket
import getopt
import getpass

# includes parent directory rather than init in path (so sibling modules are included)
sys.path[0] = sys.path[0][:-5]

from TorCtl import TorCtl, TorUtil
from interface import controller, logPanel

VERSION = "1.3.4"
LAST_MODIFIED = "Mar 7, 2010"

DEFAULT_CONTROL_ADDR = "127.0.0.1"
DEFAULT_CONTROL_PORT = 9051
DEFAULT_LOGGED_EVENTS = "N3" # tor and arm NOTICE, WARN, and ERR events

OPT = "i:p:be:vh"
OPT_EXPANDED = ["interface=", "password=", "blind", "event=", "version", "help"]
HELP_MSG = """Usage arm [OPTION]
Terminal status monitor for Tor relays.

  -i, --interface [ADDRESS:]PORT  change control interface from %s:%i
  -p, --password PASSWORD         authenticate using password (skip prompt)
  -b, --blind                     disable connection lookups
  -e, --event EVENT_FLAGS         event types in message log  (default: %s)
%s
  -v, --version                   provides version information
  -h, --help                      presents this help

Example:
arm -b -i 1643          hide connection data, attaching to control port 1643
arm -e=we -p=nemesis    use password 'nemesis' with 'WARN'/'ERR' events
""" % (DEFAULT_CONTROL_ADDR, DEFAULT_CONTROL_PORT, DEFAULT_LOGGED_EVENTS, logPanel.EVENT_LISTING)

def isValidIpAddr(ipStr):
  """
  Returns true if input is a valid IPv4 address, false otherwise.
  """
  
  for i in range(4):
    if i < 3:
      divIndex = ipStr.find(".")
      if divIndex == -1: return False # expected a period to be valid
      octetStr = ipStr[:divIndex]
      ipStr = ipStr[divIndex + 1:]
    else:
      octetStr = ipStr
    
    try:
      octet = int(octetStr)
      if not octet >= 0 or not octet <= 255: return False
    except ValueError:
      # address value isn't an integer
      return False
  
  return True

if __name__ == '__main__':
  controlAddr = DEFAULT_CONTROL_ADDR     # controller interface IP address
  controlPort = DEFAULT_CONTROL_PORT     # controller interface port
  authPassword = ""                      # authentication password (prompts if unset and needed)
  isBlindMode = False                    # allows connection lookups to be disabled
  loggedEvents = DEFAULT_LOGGED_EVENTS   # flags for event types in message log
  
  # parses user input, noting any issues
  try:
    opts, args = getopt.getopt(sys.argv[1:], OPT, OPT_EXPANDED)
  except getopt.GetoptError, exc:
    print str(exc) + " (for usage provide --help)"
    sys.exit()
  
  for opt, arg in opts:
    if opt in ("-i", "--interface"):
      # defines control interface address/port
      try:
        divIndex = arg.find(":")
        
        if divIndex == -1:
          controlPort = int(arg)
        else:
          controlAddr = arg[0:divIndex]
          controlPort = int(arg[divIndex + 1:])
        
        # validates that input is a valid ip address and port
        if divIndex != -1 and not isValidIpAddr(controlAddr):
          raise AssertionError("'%s' isn't a valid IP address" % controlAddr)
        elif controlPort < 0 or controlPort > 65535:
          raise AssertionError("'%s' isn't a valid port number (ports range 0-65535)" % controlPort)
      except ValueError:
        print "'%s' isn't a valid port number" % arg
        sys.exit()
      except AssertionError, exc:
        print exc
        sys.exit()
    elif opt in ("-p", "--password"): authPassword = arg    # sets authentication password
    elif opt in ("-b", "--blind"): isBlindMode = True       # prevents connection lookups
    elif opt in ("-e", "--event"): loggedEvents = arg       # set event flags
    elif opt in ("-v", "--version"):
      print "arm version %s (released %s)\n" % (VERSION, LAST_MODIFIED)
      sys.exit()
    elif opt in ("-h", "--help"):
      print HELP_MSG
      sys.exit()
  
  # validates and expands log event flags
  try:
    expandedEvents = logPanel.expandEvents(loggedEvents)
  except ValueError, exc:
    for flag in str(exc):
      print "Unrecognized event flag: %s" % flag
    sys.exit()
  
  # temporarily disables TorCtl logging to prevent issues from going to stdout while starting
  TorUtil.loglevel = "NONE"
  
  # attempts to open a socket to the tor server
  try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((controlAddr, controlPort))
    conn = TorCtl.Connection(s)
  except socket.error, exc:
    if str(exc) == "[Errno 111] Connection refused":
      # most common case - tor control port isn't available
      print "Connection refused. Is the ControlPort enabled?"
    else:
      # less common issue - provide exc message
      print "Failed to establish socket: %s" % exc
    
    sys.exit()
  
  # check PROTOCOLINFO for authentication type
  try:
    authInfo = conn.sendAndRecv("PROTOCOLINFO\r\n")[1][1]
  except TorCtl.ErrorReply, exc:
    print "Unable to query PROTOCOLINFO for authentication type: %s" % exc
    sys.exit()
  
  try:
    if authInfo.startswith("AUTH METHODS=NULL"):
      # no authentication required
      conn.authenticate("")
    elif authInfo.startswith("AUTH METHODS=HASHEDPASSWORD"):
      # password authentication, promts for password if it wasn't provided
      try:
        if not authPassword: authPassword = getpass.getpass()
      except KeyboardInterrupt:
        sys.exit()
      
      conn.authenticate(authPassword)
    elif authInfo.startswith("AUTH METHODS=COOKIE"):
      # cookie authtication, parses path to authentication cookie
      start = authInfo.find("COOKIEFILE=\"") + 12
      end = authInfo[start:].find("\"")
      authCookiePath = authInfo[start:start + end]
      
      try:
        authCookie = open(authCookiePath, "r")
        conn.authenticate_cookie(authCookie)
        authCookie.close()
      except IOError, exc:
        # cleaner message for common errors
        issue = None
        if str(exc).startswith("[Errno 13] Permission denied"): issue = "permission denied"
        elif str(exc).startswith("[Errno 2] No such file or directory"): issue = "file doesn't exist"
        
        # if problem's recognized give concise message, otherwise print exception string
        if issue: print "Failed to read authentication cookie (%s): %s" % (issue, authCookiePath)
        else: print "Failed to read authentication cookie: %s" % exc
        
        sys.exit()
    else:
      # authentication type unrecognized (probably a new addition to the controlSpec)
      print "Unrecognized authentication type: %s" % authInfo
      sys.exit()
  except TorCtl.ErrorReply, exc:
    # authentication failed
    issue = str(exc)
    if str(exc).startswith("515 Authentication failed: Password did not match"): issue = "password incorrect"
    if str(exc) == "515 Authentication failed: Wrong length on authentication cookie.": issue = "cookie value incorrect"
    
    print "Unable to authenticate: %s" % issue
    sys.exit()
  
  controller.startTorMonitor(conn, expandedEvents, isBlindMode)
  conn.close()

