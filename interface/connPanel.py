#!/usr/bin/env python
# connPanel.py -- Lists network connections used by tor.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import os
import curses
import socket
from TorCtl import TorCtl

import util

# enums for sorting types (note: ordering corresponds to SORT_TYPES for easy lookup)
# TODO: add ORD_BANDWIDTH -> (ORD_BANDWIDTH, "Bandwidth", lambda x, y: ???)
ORD_TYPE, ORD_FOREIGN_IP, ORD_SRC_IP, ORD_DST_IP, ORD_COUNTRY, ORD_FOREIGN_PORT, ORD_SRC_PORT, ORD_DST_PORT = range(8)
SORT_TYPES = [(ORD_TYPE, "Connection Type",
                lambda x, y: TYPE_WEIGHTS[x[0]] - TYPE_WEIGHTS[y[0]]),
              (ORD_FOREIGN_IP, "IP (Foreign)",
                lambda x, y: cmp(_ipToInt(x[3]), _ipToInt(y[3]))),
              (ORD_SRC_IP, "IP (Source)",
                lambda x, y: cmp(_ipToInt(x[3] if x[0] == "inbound" else x[1]), _ipToInt(y[3] if y[0] == "inbound" else y[1]))),
              (ORD_DST_IP, "IP (Dest.)",
                lambda x, y: cmp(_ipToInt(x[1] if x[0] == "inbound" else x[3]), _ipToInt(y[1] if y[0] == "inbound" else y[3]))),
              (ORD_COUNTRY, "Country Code",
                lambda x, y: cmp(x[5], y[5])),
              (ORD_FOREIGN_PORT, "Port (Foreign)",
                lambda x, y: int(x[4]) - int(y[4])),
              (ORD_SRC_PORT, "Port (Source)",
                lambda x, y: int(x[4] if x[0] == "inbound" else x[2]) - int(y[4] if y[0] == "inbound" else y[2])),
              (ORD_DST_PORT, "Port (Dest.)",
                lambda x, y: int(x[2] if x[0] == "inbound" else x[4]) - int(y[2] if y[0] == "inbound" else y[4]))]

TYPE_COLORS = {"inbound": "green", "outbound": "blue", "control": "red"}
TYPE_WEIGHTS = {"inbound": 0, "outbound": 1, "control": 2}

# provides bi-directional mapping of sorts with their associated labels
def getSortLabel(sortType, withColor = False):
  """
  Provides label associated with a type of sorting. Throws ValueEror if no such
  sort exists. If adding color formatting this wraps with the following mappings:
  Connection Type     red
  IP *                blue
  Port *              green
  Bandwidth           cyan
  Country Code        yellow
  """
  
  for (type, label, func) in SORT_TYPES:
    if sortType == type:
      color = None
      
      if withColor:
        if label == "Connection Type": color = "red"
        elif label.startswith("IP"): color = "blue"
        elif label.startswith("Port"): color = "green"
        elif label == "Bandwidth": color = "cyan"
        elif label == "Country Code": color = "yellow"
      
      if color: return "<%s>%s</%s>" % (color, label, color)
      else: return label
  
  raise ValueError(sortType)

def getSortType(sortLabel):
  """
  Provides sort type associated with a given label. Throws ValueEror if label
  isn't recognized.
  """
  
  for (type, label, func) in SORT_TYPES:
    if sortLabel == label: return type
  raise ValueError(sortLabel)

class ConnPanel(util.Panel):
  """
  Lists netstat provided network data of tor.
  """
  
  def __init__(self, lock, conn, logger):
    util.Panel.__init__(self, lock, -1)
    self.scroll = 0
    self.conn = conn            # tor connection for querrying country codes
    self.logger = logger        # notified in case of problems
    self.sortOrdering = [ORD_TYPE, ORD_SRC_IP, ORD_SRC_PORT]
    
    # gets process id to make sure we get the correct netstat data
    psCall = os.popen('ps -C tor -o pid')
    try: self.pid = psCall.read().strip().split()[1]
    except Exception:
      # ps call failed
      self.logger.monitor_event("ERR", "Unable to resolve tor pid, abandoning connection listing")
      self.pid = -1
    psCall.close()
    
    # uses ports to identify type of connections
    self.orPort = self.conn.get_option("ORPort")[0][1]
    self.dirPort = self.conn.get_option("DirPort")[0][1]
    self.controlPort = self.conn.get_option("ControlPort")[0][1]
    
    # netstat results are tuples of the form:
    # (type, local IP, local port, foreign IP, foreign port, country code)
    self.connections = []
    
    # count of total inbound, outbound, and control connections
    self.connectionCount = [0, 0, 0]
    
    # cache of DNS lookups, IP Address => hostname (None if couldn't be resolved)
    # TODO: implement
    self.hostnameResolution = {}
    
    self.reset()
  
  def reset(self):
    """
    Reloads netstat results.
    """
    
    self.connections = []
    self.connectionCount = [0, 0, 0]
    
    if self.pid == -1: return # initilization had warned of failure - abandon
    
    # looks at netstat for tor with stderr redirected to /dev/null, options are:
    # n = prevents dns lookups, p = include process (say if it's tor), t = tcp only
    netstatCall = os.popen("netstat -npt 2> /dev/null | grep %s/tor" % self.pid)
    try:
      results = netstatCall.readlines()
      
      for line in results:
        if not line.startswith("tcp"): continue
        param = line.split()
        local = param[3]
        foreign = param[4]
        
        localIP = local[:local.find(":")]
        localPort = local[len(localIP) + 1:]
        foreignIP = foreign[:foreign.find(":")]
        foreignPort = foreign[len(foreignIP) + 1:]
        
        if localPort in (self.orPort, self.dirPort):
          type = "inbound"
          self.connectionCount[0] += 1
        elif localPort == self.controlPort:
          type = "control"
          self.connectionCount[2] += 1
        else:
          type = "outbound"
          self.connectionCount[1] += 1
        
        try:
          countryCodeQuery = "ip-to-country/%s" % foreign[:foreign.find(":")]
          countryCode = self.conn.get_info(countryCodeQuery)[countryCodeQuery]
        except socket.error: countryCode = None
        
        self.connections.append((type, localIP, localPort, foreignIP, foreignPort, countryCode))
    except IOError:
      # netstat call failed
      self.logger.monitor_event("WARN", "Unable to query netstat for new connections")
    
    netstatCall.close()
    self.sortConnections()
  
  def handleKey(self, key):
    self._resetBounds()
    pageHeight = self.maxY - 1
    if key == curses.KEY_UP: self.scroll = max(self.scroll - 1, 0)
    elif key == curses.KEY_DOWN: self.scroll = max(0, self.scroll + 1)
    elif key == curses.KEY_PPAGE: self.scroll = max(self.scroll - pageHeight, 0)
    elif key == curses.KEY_NPAGE: self.scroll = max(0, self.scroll + pageHeight)
    self.redraw()
  
  def redraw(self):
    if self.win:
      if not self.lock.acquire(False): return
      try:
        self.clear()
        self.addstr(0, 0, "Connections (%i inbound, %i outbound, %i control):" % tuple(self.connectionCount), util.LABEL_ATTR)
        
        self.scroll = max(min(self.scroll, len(self.connections) - self.maxY + 1), 0)
        lineNum = (-1 * self.scroll) + 1
        for entry in self.connections:
          if lineNum >= 1:
            type = entry[0]
            color = TYPE_COLORS[type]
            src = "%s:%s" % (entry[1], entry[2])
            dst = "%s:%s %s" % (entry[3], entry[4], "" if type == "control" else "(%s)" % entry[5])
            if type == "inbound": src, dst = dst, src
            self.addfstr(lineNum, 0, "<%s>%-30s-->     %-26s(<b>%s</b>)</%s>" % (color, src, dst, type.upper(), color))
          lineNum += 1
        
        self.refresh()
      finally:
        self.lock.release()
  
  def sortConnections(self):
    """
    Sorts connections according to currently set ordering. This takes into
    account secondary and tertiary sub-keys in case of ties.
    """
    
    # Current implementation is very inefficient, but since connection lists
    # are decently small (count get up to arounk 1k) this shouldn't be a big
    # whoop. Suggestions for improvements are welcome!
    self.connections.sort(lambda x, y: _multisort(x, y, self.sortOrdering))

def _multisort(conn1, conn2, sorts):
  # recursively checks primary, secondary, and tertiary sorting parameter in ties
  comp = SORT_TYPES[sorts[0]][2](conn1, conn2)
  if comp or len(sorts) == 1: return comp
  else: return _multisort(conn1, conn2, sorts[1:])

# provides comparison int for sorting IP addresses
def _ipToInt(ipAddr):
  total = 0
  for comp in ipAddr.split("."):
    total *= 255
    total += int(comp)
  return total

