"""
Helper functions for querying process and system information from the /proc
contents. Fetching information this way provides huge performance benefits
over lookups via system utilities (ps, netstat, etc). For instance, resolving
connections this way cuts the runtime by around 90% verses the alternatives.
These functions may not work on all platforms (only Linux?).

All functions raise IOErrors if unable to read their respective proc files.

The method for reading these files (and some of the code) are borrowed from
psutil:
https://code.google.com/p/psutil/
which was written by Jay Loden, Dave Daeschler, Giampaolo Rodola' and is under
the BSD license.
"""

import os
import sys
import time
import socket
import base64

from util import enum, log

# cached system values
SYS_START_TIME, SYS_PHYSICAL_MEMORY = None, None
CLOCK_TICKS = os.sysconf(os.sysconf_names["SC_CLK_TCK"])
Stat = enum.Enum("COMMAND", "CPU_UTIME", "CPU_STIME", "START_TIME")

CONFIG = {"queries.useProc": True,
          "log.procCallMade": log.DEBUG}

def loadConfig(config):
  config.update(CONFIG)

def isProcAvailable():
  """
  Provides true if configured to use proc resolution and it's available on the
  platform, false otherwise.
  """
  
  return CONFIG["queries.useProc"] and os.uname()[0] == "Linux"

def getSystemStartTime():
  """
  Provides the unix time (seconds since epoch) when the system started.
  """
  
  global SYS_START_TIME
  if not SYS_START_TIME:
    startTime = time.time()
    statFile = open('/proc/stat')
    statLines = statFile.readlines()
    statFile.close()
    
    for line in statLines:
      if line.startswith('btime'):
        SYS_START_TIME = float(line.strip().split()[1])
        break
    
    _logProcRuntime("system start time", "/proc/stat[btime]", startTime)
  
  return SYS_START_TIME

def getPhysicalMemory():
  """
  Provides the total physical memory on the system in bytes.
  """
  
  global SYS_PHYSICAL_MEMORY
  if not SYS_PHYSICAL_MEMORY:
    startTime = time.time()
    memFile = open('/proc/meminfo')
    memLines = memFile.readlines()
    memFile.close()
    
    for line in memLines:
      if line.startswith('MemTotal:'):
        SYS_PHYSICAL_MEMORY = int(line.split()[1]) * 1024
    
    _logProcRuntime("system physical memory", "/proc/meminfo[MemTotal]", startTime)
  
  return SYS_PHYSICAL_MEMORY

def getPwd(pid):
  """
  Provides the current working directory for the given process.
  
  Arguments:
    pid - queried process
  """
  
  startTime = time.time()
  if pid == 0: cwd = ""
  else: cwd = os.readlink("/proc/%s/cwd" % pid)
  _logProcRuntime("cwd", "/proc/%s/cwd" % pid, startTime)
  return cwd

def getUid(pid):
  """
  Provides the user ID the given process is running under. This is None if it
  can't be determined.
  
  Arguments:
    pid - queried process
  """
  
  startTime = time.time()
  statusFile = open("/proc/%s/status" % pid)
  statusFileLines = statusFile.readlines()
  statusFile.close()
  
  result = None
  for line in statusFileLines:
    if line.startswith("Uid:"):
      lineComp = line.split()
      
      if len(lineComp) >= 2 and lineComp[1].isdigit():
        result = lineComp[1]
  
  _logProcRuntime("uid", "/proc/%s/status[Uid]" % pid, startTime)
  return result

def getMemoryUsage(pid):
  """
  Provides the memory usage in bytes for the given process of the form:
  (residentSize, virtualSize)
  
  Arguments:
    pid - queried process
  """
  
  # checks if this is the kernel process
  if pid == 0: return (0, 0)
  
  startTime = time.time()
  statusFile = open("/proc/%s/status" % pid)
  statusFileLines = statusFile.readlines()
  statusFile.close()
  
  residentSize, virtualSize = None, None
  for line in statusFileLines:
    if line.startswith("VmRSS"):
      residentSize = int(line.split()[1]) * 1024
      if virtualSize != None: break
    elif line.startswith("VmSize:"):
      virtualSize = int(line.split()[1]) * 1024
      if residentSize != None: break
  
  _logProcRuntime("memory usage", "/proc/%s/status[VmRSS|VmSize]" % pid, startTime)
  return (residentSize, virtualSize)

def getStats(pid, *statTypes):
  """
  Provides process specific information. Options are:
  Stat.COMMAND      command name under which the process is running
  Stat.CPU_UTIME    total user time spent on the process
  Stat.CPU_STIME    total system time spent on the process
  Stat.START_TIME   when this process began, in unix time
  
  Arguments:
    pid       - queried process
    statTypes - information to be provided back
  """
  
  startTime = time.time()
  statFilePath = "/proc/%s/stat" % pid
  statFile = open(statFilePath)
  statContents = statFile.read().strip()
  statFile.close()
  
  # contents are of the form:
  # 8438 (tor) S 8407 8438 8407 34818 8438 4202496...
  statComp = []
  cmdStart, cmdEnd = statContents.find("("), statContents.find(")")
  
  if cmdStart != -1 and cmdEnd != -1:
    statComp.append(statContents[:cmdStart])
    statComp.append(statContents[cmdStart + 1:cmdEnd])
    statComp += statContents[cmdEnd + 1:].split()
  
  if len(statComp) != 44:
    raise IOError("stat file had an unexpected format: %s" % statFilePath)
  
  results, queriedStats = [], []
  for statType in statTypes:
    if statType == Stat.COMMAND:
      queriedStats.append("command")
      if pid == 0: results.append("sched")
      else: results.append(statComp[1])
    elif statType == Stat.CPU_UTIME:
      queriedStats.append("utime")
      if pid == 0: results.append("0")
      else: results.append(str(float(statComp[13]) / CLOCK_TICKS))
    elif statType == Stat.CPU_STIME:
      queriedStats.append("stime")
      if pid == 0: results.append("0")
      else: results.append(str(float(statComp[14]) / CLOCK_TICKS))
    elif statType == Stat.START_TIME:
      queriedStats.append("start time")
      if pid == 0: return getSystemStartTime()
      else:
        # According to documentation, starttime is in field 21 and the unit is
        # jiffies (clock ticks). We divide it for clock ticks, then add the
        # uptime to get the seconds since the epoch.
        pStartTime = float(statComp[21]) / CLOCK_TICKS
        results.append(str(pStartTime + getSystemStartTime()))
  
  _logProcRuntime("process %s" % ", ".join(queriedStats), "/proc/%s/stat" % pid, startTime)
  return results

def getConnections(pid):
  """
  Provides a listing of connection tuples of the form:
  [(local_ipAddr1, local_port1, foreign_ipAddr1, foreign_port1), ...]
  
  If the information about a connection can't be queried (often due to
  permission issues) then it's excluded from the listing.
  
  Arguments:
    pid - ID of the process to be resolved
  """
  
  if pid == "0": return []
  
  # fetches the inode numbers for socket file descriptors
  startTime = time.time()
  inodes = []
  for fd in os.listdir("/proc/%s/fd" % pid):
    try:
      # File descriptor link, such as 'socket:[30899]'
      fdName = os.readlink("/proc/%s/fd/%s" % (pid, fd))
      
      if fdName.startswith('socket:['):
        inodes.append(fdName[8:-1])
    except OSError:
      pass # most likely couldn't be read due to permissions
  
  if not inodes:
    # unable to fetch any connections for this process
    return []
  
  # check for the connection information from the /proc/net contents
  conn = []
  for procFilePath in ("/proc/net/tcp", "/proc/net/udp"):
    procFile = open(procFilePath)
    procFile.readline() # skip the first line
    
    for line in procFile:
      _, lAddr, fAddr, status, _, _, _, _, _, inode = line.split()[:10]
      
      if inode in inodes:
        # if a tcp connection, skip if it isn't yet established
        if procFilePath.endswith("/tcp") and status != "01":
          continue
        
        localIp, localPort = _decodeProcAddressEncoding(lAddr)
        foreignIp, foreignPort = _decodeProcAddressEncoding(fAddr)
        conn.append((localIp, localPort, foreignIp, foreignPort))
    
    procFile.close()
  
  _logProcRuntime("process connections", "/proc/net/[tcp|udp]", startTime)
  
  return conn

def _decodeProcAddressEncoding(addr):
  """
  Translates an address entry in the /proc/net/* contents to a human readable
  form, for instance:
  "0500000A:0016" -> ("10.0.0.5", "22")
  
  Reference:
  http://linuxdevcenter.com/pub/a/linux/2000/11/16/LinuxAdmin.html
  
  Arguments:
    addr - proc address entry to be decoded
  """
  
  ip, port = addr.split(':')
  
  # the port is represented as a two-byte hexadecimal number
  port = str(int(port, 16))
  
  if sys.version_info >= (3,):
    ip = ip.encode('ascii')
  
  # The IPv4 address portion is a little-endian four-byte hexadecimal number.
  # That is, the least significant byte is listed first, so we need to reverse
  # the order of the bytes to convert it to an IP address.
  #
  # This needs to account for the endian ordering as per...
  # http://code.google.com/p/psutil/issues/detail?id=201
  # https://trac.torproject.org/projects/tor/ticket/4777
  
  if sys.byteorder == 'little':
    ip = socket.inet_ntop(socket.AF_INET, base64.b16decode(ip)[::-1])
  else:
    ip = socket.inet_ntop(socket.AF_INET, base64.b16decode(ip))
  
  return (ip, port)

def _logProcRuntime(parameter, procLocation, startTime):
  msg = "proc call (%s): %s (runtime: %0.4f)" % (parameter, procLocation, time.time() - startTime)
  log.log(CONFIG["log.procCallMade"], msg)

