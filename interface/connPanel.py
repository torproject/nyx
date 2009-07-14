#!/usr/bin/env python
# connPanel.py -- Lists network connections used by tor.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import os
import curses
from TorCtl import TorCtl

import util
import hostnameResolver

# enums for listing types
LIST_IP, LIST_HOSTNAME, LIST_FINGERPRINT = range(3)
LIST_LABEL = {LIST_IP: "IP", LIST_HOSTNAME: "Hostname", LIST_FINGERPRINT: "Fingerprint"}

# enums for sorting types (note: ordering corresponds to SORT_TYPES for easy lookup)
# TODO: add ORD_BANDWIDTH -> (ORD_BANDWIDTH, "Bandwidth", lambda x, y: ???)
ORD_TYPE, ORD_FOREIGN_LISTING, ORD_SRC_LISTING, ORD_DST_LISTING, ORD_COUNTRY, ORD_FOREIGN_PORT, ORD_SRC_PORT, ORD_DST_PORT = range(8)
SORT_TYPES = [(ORD_TYPE, "Connection Type",
                lambda x, y: TYPE_WEIGHTS[x[CONN_TYPE]] - TYPE_WEIGHTS[y[CONN_TYPE]]),
              (ORD_FOREIGN_LISTING, "* (Foreign)", None),
              (ORD_SRC_LISTING, "* (Source)", None),
              (ORD_DST_LISTING, "* (Dest.)", None),
              (ORD_COUNTRY, "Country Code",
                lambda x, y: cmp(x[CONN_COUNTRY], y[CONN_COUNTRY])),
              (ORD_FOREIGN_PORT, "Port (Foreign)",
                lambda x, y: int(x[CONN_F_PORT]) - int(y[CONN_F_PORT])),
              (ORD_SRC_PORT, "Port (Source)",
                lambda x, y: int(x[CONN_F_PORT] if x[CONN_TYPE] == "inbound" else x[CONN_L_PORT]) - int(y[CONN_F_PORT] if y[CONN_TYPE] == "inbound" else y[CONN_L_PORT])),
              (ORD_DST_PORT, "Port (Dest.)",
                lambda x, y: int(x[CONN_L_PORT] if x[CONN_TYPE] == "inbound" else x[CONN_F_PORT]) - int(y[CONN_L_PORT] if y[CONN_TYPE] == "inbound" else y[CONN_F_PORT]))]

TYPE_COLORS = {"inbound": "green", "outbound": "blue", "control": "red"}
TYPE_WEIGHTS = {"inbound": 0, "outbound": 1, "control": 2}

# enums for indexes of ConnPanel 'connections' fields
CONN_TYPE, CONN_L_IP, CONN_L_PORT, CONN_F_IP, CONN_F_PORT, CONN_COUNTRY = range(6)

# provides bi-directional mapping of sorts with their associated labels (with getSortLabel)
def getSortType(sortLabel):
  """
  Provides sort type associated with a given label. Throws ValueEror if label
  isn't recognized.
  """
  
  for (type, label, func) in SORT_TYPES:
    if sortLabel == label: return type
    elif label.startswith("*"):
      if sortLabel in [label.replace("*", listingType) for listingType in LIST_LABEL.values()]: return type
  raise ValueError(sortLabel)

class ConnPanel(TorCtl.PostEventListener, util.Panel):
  """
  Lists netstat provided network data of tor.
  """
  
  def __init__(self, lock, conn, logger):
    TorCtl.PostEventListener.__init__(self)
    util.Panel.__init__(self, lock, -1)
    self.scroll = 0
    self.conn = conn                # tor connection for querrying country codes
    self.logger = logger            # notified in case of problems
    self.listingType = LIST_IP      # information used in listing entries
    self.allowDNS = True            # permits hostname resolutions if true
    self.sortOrdering = [ORD_TYPE, ORD_FOREIGN_LISTING, ORD_FOREIGN_PORT]
    self.isPaused = False
    self.resolver = hostnameResolver.HostnameResolver()
    self.fingerprintLookupCache = {}                              # chache of (ip, port) -> fingerprint
    self.fingerprintMappings = _getFingerprintMappings(self.conn) # mappings of ip -> [(port, OR identity), ...]
    
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
    
    self.reset()
  
  # when consensus changes update fingerprint mappings
  def new_consensus_event(self, n):
    self.fingerprintLookupCache = {}
    self.fingerprintMappings = _getFingerprintMappings(self.conn)
  
  def new_desc_event(self, d):
    self.fingerprintLookupCache = {}
    self.fingerprintMappings = _getFingerprintMappings(self.conn)
  
  def reset(self):
    """
    Reloads netstat results.
    """
    
    if self.isPaused or self.pid == -1: return
    self.connections = []
    self.connectionCount = [0, 0, 0]
    
    # looks at netstat for tor with stderr redirected to /dev/null, options are:
    # n = prevents dns lookups, p = include process (say if it's tor), t = tcp only
    netstatCall = os.popen("netstat -npt 2> /dev/null | grep %s/tor" % self.pid)
    try:
      results = netstatCall.readlines()
      
      for line in results:
        if not line.startswith("tcp"): continue
        param = line.split()
        local, foreign = param[3], param[4]
        localIP, foreignIP = local[:local.find(":")], foreign[:foreign.find(":")]
        localPort, foreignPort = local[len(localIP) + 1:], foreign[len(foreignIP) + 1:]
        
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
        except socket.error: countryCode = "??"
        
        self.connections.append((type, localIP, localPort, foreignIP, foreignPort, countryCode))
    except IOError:
      # netstat call failed
      self.logger.monitor_event("WARN", "Unable to query netstat for new connections")
    
    netstatCall.close()
    
    # hostnames are sorted at redraw - otherwise now's a good time
    if self.listingType != LIST_HOSTNAME: self.sortConnections()
  
  def handleKey(self, key):
    self._resetBounds()
    pageHeight = self.maxY - 1
    if key == curses.KEY_UP: self.scroll = max(self.scroll - 1, 0)
    elif key == curses.KEY_DOWN: self.scroll = max(0, self.scroll + 1)
    elif key == curses.KEY_PPAGE: self.scroll = max(self.scroll - pageHeight, 0)
    elif key == curses.KEY_NPAGE: self.scroll = max(0, self.scroll + pageHeight)
    elif key == ord('r') or key == ord('R'):
      self.allowDNS = not self.allowDNS
      if not self.allowDNS: self.resolver.setPaused(True)
      elif self.listingType == LIST_HOSTNAME: self.resolver.setPaused(False)
    else: return # skip following redraw
    self.redraw()
  
  def redraw(self):
    if self.win:
      if not self.lock.acquire(False): return
      try:
        # hostnames frequently get updated so frequent sorting needed
        if self.listingType == LIST_HOSTNAME: self.sortConnections()
        
        self.clear()
        self.addstr(0, 0, "Connections (%i inbound, %i outbound, %i control):" % tuple(self.connectionCount), util.LABEL_ATTR)
        
        self.scroll = max(min(self.scroll, len(self.connections) - self.maxY + 1), 0)
        lineNum = (-1 * self.scroll) + 1
        for entry in self.connections:
          if lineNum >= 1:
            type = entry[CONN_TYPE]
            color = TYPE_COLORS[type]
            
            if self.listingType == LIST_IP:
              src = "%s:%s" % (entry[CONN_L_IP], entry[CONN_L_PORT])
              dst = "%s:%s %s" % (entry[CONN_F_IP], entry[CONN_F_PORT], "" if type == "control" else "(%s)" % entry[CONN_COUNTRY])
              src, dst = "%-26s" % src, "%-26s" % dst
            elif self.listingType == LIST_HOSTNAME:
              src = "localhost:%-5s" % entry[CONN_L_PORT]
              hostname = self.resolver.resolve(entry[CONN_F_IP])
              
              # truncates long hostnames
              portDigits = len(str(entry[CONN_F_PORT]))
              if hostname and (len(hostname) + portDigits) > 36: hostname = hostname[:(33 - portDigits)] + "..."
              
              dst = "%s:%s" % (hostname if hostname else entry[CONN_F_IP], entry[CONN_F_PORT])
              dst = "%-37s" % dst
            else:
              src = "localhost  "
              if entry[CONN_TYPE] == "control": dst = "localhost"
              else: dst = self.getFingerprint(entry[CONN_F_IP], entry[CONN_F_PORT])
              dst = "%-41s" % dst
            
            if type == "inbound": src, dst = dst, src
            self.addfstr(lineNum, 0, "<%s>%s -->   %s   (<b>%s</b>)</%s>" % (color, src, dst, type.upper(), color))
          lineNum += 1
        
        self.refresh()
      finally:
        self.lock.release()
  
  def getFingerprint(self, ipAddr, port):
    """
    Makes an effort to match connection to fingerprint - if there's multiple
    potential matches or the IP address isn't found in the discriptor then
    returns "UNKNOWN".
    """
    
    if (ipAddr, port) in self.fingerprintLookupCache:
      return self.fingerprintLookupCache[(ipAddr, port)]
    else:
      match = "UNKNOWN"
      
      if ipAddr in self.fingerprintMappings.keys():
        potentialMatches = self.fingerprintMappings[ipAddr]
        
        if len(potentialMatches) == 1: match = potentialMatches[0][1]
        else:
          for (entryPort, entryFingerprint) in potentialMatches:
            if entryPort == port: match = entryFingerprint
      
      self.fingerprintLookupCache[(ipAddr, port)] = match
      return match
  
  def setPaused(self, isPause):
    """
    If true, prevents connection listing from being updated.
    """
    
    self.isPaused = isPause
  
  def getSortLabel(self, sortType, withColor = False):
    """
    Provides label associated with a type of sorting. Throws ValueEror if no such
    sort exists. If adding color formatting this wraps with the following mappings:
    Connection Type     red
    [Listing] *         blue
    Port *              green
    Bandwidth           cyan
    Country Code        yellow
    """
    
    for (type, label, func) in SORT_TYPES:
      if sortType == type:
        color = None
        
        if withColor:
          if label == "Connection Type": color = "red"
          elif label.startswith("*"): color = "blue"
          elif label.startswith("Port"): color = "green"
          elif label == "Bandwidth": color = "cyan"
          elif label == "Country Code": color = "yellow"
        
        if label.startswith("*"): label = label.replace("*", LIST_LABEL[self.listingType])
        if color: return "<%s>%s</%s>" % (color, label, color)
        else: return label
    
    raise ValueError(sortType)
  
  def sortConnections(self):
    """
    Sorts connections according to currently set ordering. This takes into
    account secondary and tertiary sub-keys in case of ties.
    """
    
    # Current implementation is very inefficient, but since connection lists
    # are decently small (count get up to arounk 1k) this shouldn't be a big
    # whoop. Suggestions for improvements are welcome!
    
    sorts = []
    
    # wrapper function for using current listed data (for 'LISTING' sorts)
    if self.listingType == LIST_IP:
      listingWrapper = lambda ip, port: _ipToInt(ip)
    elif self.listingType == LIST_HOSTNAME:
      # alphanumeric hostnames followed by unresolved IP addresses
      listingWrapper = lambda ip, port: self.resolver.resolve(ip).upper() if self.resolver.resolve(ip) else "ZZZZZ%099i" % _ipToInt(ip)
    elif self.listingType == LIST_FINGERPRINT:
      # alphanumeric fingerprints followed by UNKNOWN entries
      listingWrapper = lambda ip, port: self.getFingerprint(ip, port) if self.getFingerprint(ip, port) != "UNKNOWN" else "ZZZZZ%099i" % _ipToInt(ip)
    
    for entry in self.sortOrdering:
      if entry == ORD_FOREIGN_LISTING:
        sorts.append(lambda x, y: cmp(listingWrapper(x[CONN_F_IP], x[CONN_F_PORT]), listingWrapper(y[CONN_F_IP], y[CONN_F_PORT])))
      elif entry == ORD_SRC_LISTING:
        sorts.append(lambda x, y: cmp(listingWrapper(x[CONN_F_IP] if x[CONN_TYPE] == "inbound" else x[CONN_L_IP], x[CONN_F_PORT]), listingWrapper(y[CONN_F_IP] if y[CONN_TYPE] == "inbound" else y[CONN_L_IP], y[CONN_F_PORT])))
      elif entry == ORD_DST_LISTING:
        sorts.append(lambda x, y: cmp(listingWrapper(x[CONN_L_IP] if x[CONN_TYPE] == "inbound" else x[CONN_F_IP], x[CONN_F_PORT]), listingWrapper(y[CONN_L_IP] if y[CONN_TYPE] == "inbound" else y[CONN_F_IP], y[CONN_F_PORT])))
      else: sorts.append(SORT_TYPES[entry][2])
    
    self.connections.sort(lambda x, y: _multisort(x, y, sorts))

# recursively checks primary, secondary, and tertiary sorting parameter in ties
def _multisort(conn1, conn2, sorts):
  comp = sorts[0](conn1, conn2)
  if comp or len(sorts) == 1: return comp
  else: return _multisort(conn1, conn2, sorts[1:])

# provides comparison int for sorting IP addresses
def _ipToInt(ipAddr):
  total = 0
  for comp in ipAddr.split("."):
    total *= 255
    total += int(comp)
  return total

# uses consensus data to map IP addresses to port / fingerprint combinations
def _getFingerprintMappings(conn):
  ipToFingerprint = {}
  
  try:
    lastIp, lastPort = None, None
    for line in conn.get_info("desc/all-recent")["desc/all-recent"].split("\n"):
      if line.startswith("router "): lastIp, lastPort = line.split()[2], line.split()[3]
      elif line.startswith("opt fingerprint "):
        fingerprint = "".join(line.split()[2:])
        if lastIp in ipToFingerprint.keys(): ipToFingerprint[lastIp].append((lastPort, fingerprint))
        else: ipToFingerprint[lastIp] = [(lastPort, fingerprint)]
  except TorCtl.TorCtlClosed: pass
  
  return ipToFingerprint

