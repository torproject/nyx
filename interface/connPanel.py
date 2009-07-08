#!/usr/bin/env python
# connPanel.py -- Lists network connections used by tor.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import os
import curses
import socket
from TorCtl import TorCtl

import util

# enums for sorting types
ORD_TYPE, ORD_FOREIGN_IP, ORD_SRC_IP, ORD_DST_IP, ORD_ALPHANUMERIC, ORD_FOREIGN_PORT, ORD_SRC_PORT, ORD_DST_PORT, ORD_COUNTRY = range(9)
SORT_TYPES = [(ORD_TYPE, "Connection Type"), (ORD_FOREIGN_IP, "IP (Foreign)"), (ORD_SRC_IP, "IP (Source)"), (ORD_DST_IP, "IP (Dest.)"), (ORD_ALPHANUMERIC, "Alphanumeric"), (ORD_FOREIGN_PORT, "Port (Foreign)"), (ORD_SRC_PORT, "Port (Source)"), (ORD_DST_PORT, "Port (Dest.)"), (ORD_COUNTRY, "Country Code")]

# provides bi-directional mapping of sorts with their associated labels
def getSortLabel(sortType, withColor = False):
  """
  Provides label associated with a type of sorting. Throws ValueEror if no such
  sort exists. If adding color formatting this wraps with the following mappings:
  Connection Type     red
  IP *                blue
  Port *              green
  Alphanumeric        cyan
  Country Code        yellow
  """
  
  for (type, label) in SORT_TYPES:
    if sortType == type:
      color = None
      
      if withColor:
        if label == "Connection Type": color = "red"
        elif label.startswith("IP"): color = "blue"
        elif label.startswith("Port"): color = "green"
        elif label == "Alphanumeric": color = "cyan"
        elif label == "Country Code": color = "yellow"
      
      if color: return "<%s>%s</%s>" % (color, label, color)
      else: return label
  
  raise ValueError(sortType)

def getSortType(sortLabel):
  """
  Provides sort type associated with a given label. Throws ValueEror if label
  isn't recognized.
  """
  
  for (type, label) in SORT_TYPES:
    if sortLabel == label: return type
  raise ValueError(sortLabel)

# TODO: order by bandwidth
# TODO: primary/secondary sort parameters

class ConnPanel(util.Panel):
  """
  Lists netstat provided network data of tor.
  """
  
  def __init__(self, lock, conn):
    util.Panel.__init__(self, lock, -1)
    self.scroll = 0
    logger = None
    self.conn = conn            # tor connection for querrying country codes
    self.logger = logger        # notified in case of problems
    self.sortOrdering = [ORD_TYPE, ORD_SRC_IP, ORD_SRC_PORT]
    
    # gets process id to make sure we get the correct netstat data
    psCall = os.popen('ps -C tor -o pid')
    try: self.pid = psCall.read().strip().split()[1]
    except IOError:
      self.logger.monitor_event("ERR", "Unable to resolve tor pid, abandoning connection listing")
      self.pid = -1 # ps call failed
    psCall.close()
    
    self.orPort = self.conn.get_option("ORPort")[0][1]
    self.dirPort = self.conn.get_option("DirPort")[0][1]
    self.controlPort = self.conn.get_option("ControlPort")[0][1]
    
    # tuples of last netstat results with (source, destination)
    # addresses could be resolved and foreign locations followed by country code
    self.inboundConn = []
    self.outboundConn = []
    self.controlConn = []
    
    # alternative conn: (source IP, source port destination IP, destination port, country code, type)
    self.connections = []
    
    # cache of DNS lookups, IP Address => hostname (None if couldn't be resolved)
    self.hostnameResolution = {}
    
    self.reset()
  
  def reset(self):
    """
    Reloads netstat results.
    """
    
    self.inboundConn = []
    self.outboundConn = []
    self.controlConn = []
    
    self.connections = []
    
    # TODO: provide special message if there's no connections
    if self.pid == -1: return # TODO: how should this be handled?
    
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
        
        sourcePort = local[local.find(":") + 1:]
        if sourcePort == self.controlPort: self.controlConn.append((local, foreign))
        else:
          # include country code for foreign address
          try:
            countryCodeCommand = "ip-to-country/%s" % foreign[:foreign.find(":")]
            countryCode = self.conn.get_info(countryCodeCommand)[countryCodeCommand]
            foreign = "%s (%s)" % (foreign, countryCode)
          except socket.error: pass 
          
          if sourcePort == self.orPort or sourcePort == self.dirPort: self.inboundConn.append((foreign, local))
          else: self.outboundConn.append((local, foreign))
    except IOError:
      # TODO: provide warning of failure
      pass # netstat call failed
    netstatCall.close()
    
    # sort by local ip address
    # TODO: implement
  
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
        self.addstr(0, 0, "Connections (%i inbound, %i outbound, %i control):" % (len(self.inboundConn), len(self.outboundConn), len(self.controlConn)), util.LABEL_ATTR)
        
        self.scroll = min(self.scroll, len(self.inboundConn) + len(self.outboundConn) + len(self.controlConn) - self.maxY + 1)
        skipEntries = self.scroll
        lineNum = 1
        connSets = [(self.inboundConn, "INBOUND", "green"),
            (self.outboundConn, "OUTBOUND", "blue"),
            (self.controlConn, "CONTROL", "red")]
        
        for connSet in connSets:
          for (source, dest) in connSet[0]:
            if skipEntries > 0:
              skipEntries = skipEntries - 1
            else:
              self.addfstr(lineNum, 0, "<%s>%-30s-->     %-26s(<b>%s</b>)</%s>" % (connSet[2], source, dest, connSet[1], connSet[2]))
              lineNum = lineNum + 1
        self.refresh()
      finally:
        self.lock.release()

