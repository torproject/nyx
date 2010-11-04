"""
This provides handlers for specially formatted configuration files. Entries are
expected to consist of simple key/value pairs, and anything after "#" is
stripped as a comment. Excess whitespace is trimmed and empty lines are
ignored. For instance:
# This is my sample config

user.name Galen
user.password yabba1234 # here's an inline comment
user.notes takes a fancy to pepperjack chese
blankEntry.example

would be loaded as four entries (the last one's value being an empty string).
If a key's defined multiple times then the last instance of it is used.
"""

import os
import threading

from util import log

CONFS = {}  # mapping of identifier to singleton instances of configs
CONFIG = {"log.configEntryNotFound": None,
          "log.configEntryTypeError": log.NOTICE}

def loadConfig(config):
  config.update(CONFIG)

def getConfig(handle):
  """
  Singleton constructor for configuration file instances. If a configuration
  already exists for the handle then it's returned. Otherwise a fresh instance
  is constructed.
  
  Arguments:
    handle - unique identifier used to access this config instance
  """
  
  if not handle in CONFS: CONFS[handle] = Config()
  return CONFS[handle]

class Config():
  """
  Handler for easily working with custom configurations, providing persistence
  to and from files. All operations are thread safe.
  
  Parameters:
    path        - location from which configurations are saved and loaded
    contents    - mapping of current key/value pairs
    rawContents - last read/written config (initialized to an empty string)
  """
  
  def __init__(self):
    """
    Creates a new configuration instance.
    """
    
    self.path = None        # location last loaded from
    self.contents = {}      # configuration key/value pairs
    self.contentsLock = threading.RLock()
    self.requestedKeys = set()
    self.rawContents = []   # raw contents read from configuration file
  
  def getValue(self, key, default=None, multiple=False):
    """
    This provides the currently value associated with a given key. If no such
    key exists then this provides the default.
    
    Arguments:
      key      - config setting to be fetched
      default  - value provided if no such key exists
      multiple - provides back a list of all values if true, otherwise this
                 returns the last loaded configuration value
    """
    
    self.contentsLock.acquire()
    
    if key in self.contents:
      val = self.contents[key]
      if not multiple: val = val[-1]
      self.requestedKeys.add(key)
    else:
      msg = "config entry '%s' not found, defaulting to '%s'" % (key, str(default))
      log.log(CONFIG["log.configEntryNotFound"], msg)
      val = default
    
    self.contentsLock.release()
    
    return val
  
  def get(self, key, default=None):
    """
    Fetches the given configuration, using the key and default value to hint
    the type it should be. Recognized types are:
    - logging runlevel if key starts with "log."
    - boolean if default is a boolean (valid values are 'true' and 'false',
      anything else provides the default)
    - integer or float if default is a number (provides default if fails to
      cast)
    - list of all defined values default is a list
    - mapping of all defined values (key/value split via "=>") if the default
      is a dict
    
    Arguments:
      key      - config setting to be fetched
      default  - value provided if no such key exists
    """
    
    callDefault = log.runlevelToStr(default) if key.startswith("log.") else default
    isMultivalue = isinstance(default, list) or isinstance(default, dict)
    val = self.getValue(key, callDefault, isMultivalue)
    if val == default: return val
    
    if key.startswith("log."):
      if val.lower() in ("none", "debug", "info", "notice", "warn", "err"):
        val = log.strToRunlevel(val)
      else:
        msg = "config entry '%s' is expected to be a runlevel, defaulting to '%s'" % (key, callDefault)
        log.log(CONFIG["log.configEntryTypeError"], msg)
        val = default
    elif isinstance(default, bool):
      if val.lower() == "true": val = True
      elif val.lower() == "false": val = False
      else:
        msg = "config entry '%s' is expected to be a boolean, defaulting to '%s'" % (key, str(default))
        log.log(CONFIG["log.configEntryTypeError"], msg)
        val = default
    elif isinstance(default, int):
      try: val = int(val)
      except ValueError:
        msg = "config entry '%s' is expected to be an integer, defaulting to '%i'" % (key, default)
        log.log(CONFIG["log.configEntryTypeError"], msg)
        val = default
    elif isinstance(default, float):
      try: val = float(val)
      except ValueError:
        msg = "config entry '%s' is expected to be a float, defaulting to '%f'" % (key, default)
        log.log(CONFIG["log.configEntryTypeError"], msg)
        val = default
    elif isinstance(default, list):
      pass # nothing special to do (already a list)
    elif isinstance(default, dict):
      valMap = {}
      for entry in val:
        if "=>" in entry:
          entryKey, entryVal = entry.split("=>", 1)
          valMap[entryKey.strip()] = entryVal.strip()
        else:
          msg = "ignoring invalid %s config entry (expected a mapping, but \"%s\" was missing \"=>\")" % (key, entry)
          log.log(CONFIG["log.configEntryTypeError"], msg)
      val = valMap
    
    return val
  
  def update(self, confMappings, limits = {}):
    """
    Revises a set of key/value mappings to reflect the current configuration.
    Undefined values are left with their current values.
    
    Arguments:
      confMappings - configuration key/value mappings to be revised
      limits       - mappings of limits on numeric values, expected to be of
                     the form "configKey -> min" or "configKey -> (min, max)"
    """
    
    for entry in confMappings.keys():
      val = self.get(entry, confMappings[entry])
      
      if entry in limits and (isinstance(val, int) or isinstance(val, float)):
        if isinstance(limits[entry], tuple):
          val = max(val, limits[entry][0])
          val = min(val, limits[entry][1])
        else: val = max(val, limits[entry])
      
      confMappings[entry] = val
  
  def getKeys(self):
    """
    Provides all keys in the currently loaded configuration.
    """
    
    return self.contents.keys()
  
  def getUnusedKeys(self):
    """
    Provides the set of keys that have never been requested.
    """
    
    return set(self.getKeys()).difference(self.requestedKeys)
  
  def set(self, key, value):
    """
    Stores the given configuration value.
    
    Arguments:
      key   - config key to be set
      value - config value to be set
    """
    
    self.contentsLock.acquire()
    self.contents[key] = value
    self.contentsLock.release()
  
  def clear(self):
    """
    Drops all current key/value mappings.
    """
    
    self.contentsLock.acquire()
    self.contents.clear()
    self.contentsLock.release()
  
  def load(self, path):
    """
    Reads in the contents of the given path, adding its configuration values
    and overwriting any that already exist. If the file's empty then this
    doesn't do anything. Other issues (like having insufficient permissions or
    if the file doesn't exist) result in an IOError.
    
    Arguments:
      path - file path to be loaded
    """
    
    configFile = open(path, "r")
    self.rawContents = configFile.readlines()
    configFile.close()
    
    self.contentsLock.acquire()
    
    for line in self.rawContents:
      # strips any commenting or excess whitespace
      commentStart = line.find("#")
      if commentStart != -1: line = line[:commentStart]
      line = line.strip()
      
      # parse the key/value pair
      if line and " " in line:
        key, value = line.split(" ", 1)
        value = value.strip()
        
        if key in self.contents: self.contents[key].append(value)
        else: self.contents[key] = [value]
    
    self.path = path
    self.contentsLock.release()
  
  def save(self, saveBackup=True):
    """
    Writes the contents of the current configuration. If a configuration file
    already exists then merges as follows:
    - comments and file contents not in this config are left unchanged
    - lines with duplicate keys are stripped (first instance is kept)
    - existing entries are overwritten with their new values, preserving the
      positioning of in-line comments if able
    - config entries not in the file are appended to the end in alphabetical
      order
    
    If problems arise in writing (such as an unset path or insufficient
    permissions) result in an IOError.
    
    Arguments:
      saveBackup - if true and a file already exists then it's saved (with
                   '.backup' appended to its filename)
    """
    
    pass # TODO: implement when persistence is needed

