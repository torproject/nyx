#!/usr/bin/env python
# arm.py -- Terminal status monitor for Tor relays.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

"""
Command line application for monitoring Tor relays, providing real time status
information. This is the starter for the application, handling and validating
command line parameters.
"""

import sys
import os
import socket
import getpass
import binascii

try:
  from TorCtl import TorCtl
except ImportError:
  print "Unable to load TorCtl (see readme for instructions)"
  sys.exit()

import armInterface

DEFAULT_CONTROL_ADDR = "127.0.0.1"
DEFAULT_CONTROL_PORT = 9051
DEFAULT_AUTH_COOKIE = os.path.expanduser("~/.tor/control_auth_cookie") # TODO: Check if this is valid for macs
DEFAULT_LOGGED_EVENTS = "nwe" # NOTICE, WARN, ERR

NO_AUTH, COOKIE_AUTH, PASSWORD_AUTH = range(3) # enums for authentication type
EVENT_TYPES = {
  "d": "DEBUG",   "a": "ADDRMAP",     "l": "NEWDESC",   "u": "AUTHDIR_NEWDESCS",
  "i": "INFO",    "b": "BW",          "m": "NS",        "v": "CLIENTS_SEEN",
  "n": "NOTICE",  "c": "CIRC",        "o": "ORCONN",    "x": "STATUS_GENERAL",
  "w": "WARN",    "f": "DESCCHANGED", "s": "STREAM",    "y": "STATUS_CLIENT",
  "e": "ERR",     "g": "GUARD",       "t": "STREAM_BW", "z": "STATUS_SERVER"}

HELP_TEXT = """Usage arm [OPTION]
Terminal Tor relay status monitor.

  -i, --interface [ADDRESS:]PORT  change control interface from %s:%i
  -c, --cookie[=PATH]             authenticates using cookie, PATH defaults to
                                    '%s'
  -p, --password[=PASSWORD]       authenticates using password, prompting
                                    without terminal echo if not provided
  -e, --event=[EVENT FLAGS]       event types in message log  (default: %s)
        d DEBUG     a ADDRMAP       l NEWDESC         u AUTHDIR_NEWDESCS
        i INFO      b BW            m NS              v CLIENTS_SEEN
        n NOTICE    c CIRC          o ORCONN          x STATUS_GENERAL
        w WARN      f DESCCHANGED   s STREAM          y STATUS_CLIENT
        e ERR       g GUARD         t STREAM_BW       z STATUS_SERVER
        Aliases:    A All Events    U Unknown Events  R Runlevels (dinwe)
  -h, --help                      presents this help

Example:
arm -c                  authenticate using the default cookie
arm -i 1643 -p          prompt for password using control port 1643
arm -e=we -p=nemesis    use password 'nemesis' with 'WARN'/'ERR' events
""" % (DEFAULT_CONTROL_ADDR, DEFAULT_CONTROL_PORT, DEFAULT_AUTH_COOKIE, DEFAULT_LOGGED_EVENTS)

class Input:
  "Collection of the user's command line input"
  
  def __init__(self, args):
    self.controlAddr = DEFAULT_CONTROL_ADDR     # controller interface IP address
    self.controlPort = DEFAULT_CONTROL_PORT     # controller interface port
    self.authType = NO_AUTH                     # type of authentication used
    self.authCookieLoc = DEFAULT_AUTH_COOKIE    # location of authentication cookie
    self.authPassword = ""                      # authentication password
    self.loggedEvents = DEFAULT_LOGGED_EVENTS   # flags for event types in message log
    self.isValid = True                         # determines if the program should run
    self.printHelp = False                      # prints help then quits
    self._parseArgs(args)
  
  def _parseArgs(self, args):
    """
    Recursively parses arguments, populating parameters and checking input 
    validity. This does not check if options are defined multiple times.
    """
    
    if len(args) == 0: return
    elif args[0] == "-i" or args[0] == "--interface":
      # defines control interface address/port
      if len(args) >= 2:
        interfaceArg = args[1]
        
        try:
          divIndex = interfaceArg.find(":")
          
          if divIndex == -1:
            self.controlAddr = DEFAULT_CONTROL_ADDR
            self.controlPort = int(interfaceArg)
          else:
            self.controlAddr = interfaceArg[0:divIndex]
            if not isValidIpAddr(self.controlAddr): raise AssertionError()
            self.controlPort = int(interfaceArg[divIndex + 1:])
          self._parseArgs(args[2:])
        except ValueError:
          print "'%s' isn't a valid interface" % interfaceArg
          self.isValid = False
        except AssertionError:
          print "'%s' isn't a valid IP address" % self.controlAddr
          self.isValid = False
      else:
        print "%s argument provided without defining an interface" % args[0]
        self.isValid = False
        
    elif args[0] == "-c" or args[0].startswith("-c=") or args[0] == "--cookie" or args[0].startswith("--cookie="):
      # set to use cookie authentication (and possibly define location)
      self.authType = COOKIE_AUTH
      
      # sets authentication path if provided
      if args[0].startswith("-c="):
        self.authCookieLoc = args[0][3:]
      elif args[0].startswith("--cookie="):
        self.authCookieLoc = args[0][9:]
      
      self._parseArgs(args[1:])
    elif args[0] == "-p" or args[0].startswith("-p=") or args[0] == "--password" or args[0].startswith("--password="):
      # set to use password authentication
      self.authType = PASSWORD_AUTH
      
      # sets authentication password if provided
      if args[0].startswith("-p="):
        self.authPassword = args[0][3:]
      elif args[0].startswith("--password="):
        self.authPassword = args[0][11:]
      
      self._parseArgs(args[1:])
    elif args[0].startswith("-e=") or args[0].startswith("--event="):
      # set event flags
      if args[0].startswith("-e="): self.loggedEvents = args[0][3:]
      else: self.loggedEvents = args[0][8:]
      self._parseArgs(args[1:])
    elif args[0] == "-h" or args[0] == "--help":
      self.printHelp = True
      self._parseArgs(args[1:])
    else:
      print "Unrecognized command: " + args[0]
      self.isValid = False

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
  # parses user input, quitting if there's a problem
  input = Input(sys.argv[1:])
  if not input.isValid: sys.exit()
  
  # if help flag's set then prints help and quits
  if input.printHelp:
    print HELP_TEXT
    sys.exit()
  
  # validates that cookie authentication path exists
  if input.authType == COOKIE_AUTH and not os.path.exists(input.authCookieLoc):
    print "Authentication cookie doesn't exist: %s" % input.authCookieLoc
    sys.exit()
  
  # promts for password if not provided
  if input.authType == PASSWORD_AUTH and input.authPassword == "":
    input.authPassword = getpass.getpass()
  
  # validates and expands logged event flags
  expandedEvents = set()
  isValid = True
  for flag in input.loggedEvents:
    if flag == "A":
      expandedEvents = set(EVENT_TYPES.values())
      break
    elif flag == "U":
      expandedEvents.add("UNKNOWN")
    elif flag == "R":
      expandedEvents = expandedEvents.union(set(["DEBUG", "INFO", "NOTICE", "WARN", "ERR"]))
    elif flag in EVENT_TYPES:
      expandedEvents.add(EVENT_TYPES[flag])
    else:
      print "Unrecognized event flag: %s" % flag
      isValid = False
  if not isValid: sys.exit()
  
  # attempts to open a socket to the tor server
  s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
  try:
    s.connect((input.controlAddr, input.controlPort))
    conn = TorCtl.Connection(s)
    
    # provides authentication credentials to the control port
    if input.authType == NO_AUTH:
      conn.authenticate("")
    elif input.authType == COOKIE_AUTH:
      # BUG: about a quarter of the time authentication fails with "Wrong 
      # length on authentication cookie." or "Invalid quoted string.  You 
      # need to put the password in double quotes." - this is possibly a TorCtl
      # issue, but after sinking dozens of hours into this intermittent problem 
      # I'm throwing in the towl for now...
      
      authCookie = open(input.authCookieLoc)
      #conn.authenticate(authCookie.read(-1))
      
      # experimenting with an alternative to see if it works better - so far so good...
      conn.sendAndRecv("AUTHENTICATE %s\r\n" % binascii.b2a_hex(authCookie.read()))
      
      authCookie.close()
    else:
      assert input.authType == PASSWORD_AUTH, "Invalid value in input.authType enum: " + str(input.authType)
      conn.authenticate(input.authPassword)
  except socket.error, exc:
    print "Is the ControlPort enabled? Connection failed: %s" % exc
    sys.exit()
  except TorCtl.ErrorReply, exc:
    print "Connection failed: %s" % exc
    sys.exit()
  
  armInterface.startTorMonitor(conn, expandedEvents)
  conn.close()

