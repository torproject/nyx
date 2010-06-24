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

import log

CONFS = {}  # mapping of identifier to singleton instances of configs
CONFIG = {"log.configEntryNotFound": None, "log.configEntryTypeError": log.INFO}

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
  Handler for easily working with custom configurations, providing persistance
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
    
    self.path = None        # path to the associated configuation file
    self.contents = {}      # configuration key/value pairs
    self.contentsLock = threading.RLock()
    self.requestedKeys = set()
    self.rawContents = []   # raw contents read from configuration file
  
  def getStr(self, key, default=None):
    """
    This provides the currently value associated with a given key. If no such
    key exists then this provides the default.
    
    Arguments:
      key     - config setting to be fetched
      default - value provided if no such key exists
    """
    
    self.contentsLock.acquire()
    
    if key in self.contents:
      val = self.contents[key]
      self.requestedKeys.add(key)
    else:
      msg = "config entry '%s' not found, defaulting to '%s'" % (key, str(default))
      log.log(CONFIG["log.configEntryNotFound"], msg)
      val = default
    
    self.contentsLock.release()
    
    return val
  
  def get(self, key, default=None, minValue=0, maxValue=None):
    """
    Fetches the given configuration, using the key and default value to hint
    the type it should be. Recognized types are:
    - boolean if default is a boolean (valid values are 'true' and 'false',
      anything else provides the default)
    - integer or float if default is a number (provides default if fails to
      cast)
    - logging runlevel if key starts with "log."
    
    Arguments:
      key      - config setting to be fetched
      default  - value provided if no such key exists
      minValue - if set and default value is numeric then uses this constraint
      maxValue - if set and default value is numeric then uses this constraint
    """
    
    callDefault = log.runlevelToStr(default) if key.startswith("log.") else default
    val = self.getStr(key, callDefault)
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
      try:
        val = int(val)
        if minValue: val = max(val, minValue)
        if maxValue: val = min(val, maxValue)
      except ValueError:
        msg = "config entry '%s' is expected to be an integer, defaulting to '%i'" % (key, default)
        log.log(CONFIG["log.configEntryTypeError"], msg)
        val = default
    elif isinstance(default, float):
      try:
        val = float(val)
        if minValue: val = max(val, minValue)
        if maxValue: val = min(val, maxValue)
      except ValueError:
        msg = "config entry '%s' is expected to be a float, defaulting to '%f'" % (key, default)
        log.log(CONFIG["log.configEntryTypeError"], msg)
        val = default
    
    return val
  
  def update(self, confMappings):
    """
    Revises a set of key/value mappings to reflect the current configuration.
    Undefined values are left with their current values.
    
    Arguments:
      confMappings - configuration key/value mappints to be revised
    """
    
    for entry in confMappings.keys():
      confMappings[entry] = self.get(entry, confMappings[entry])
  
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
  
  def load(self):
    """
    Reads in the contents of the currently set configuration file (appending
    any results to the current configuration). If the file's empty or doesn't
    exist then this doesn't do anything.
    
    Other issues (like having an unset path or insufficient permissions) result
    in an IOError.
    """
    
    if not self.path: raise IOError("unable to load (config path undefined)")
    
    if os.path.exists(self.path):
      configFile = open(self.path, "r")
      self.rawContents = configFile.readlines()
      configFile.close()
      
      self.contentsLock.acquire()
      
      for line in self.rawContents:
        # strips any commenting or excess whitespace
        commentStart = line.find("#")
        if commentStart != -1: line = line[:commentStart]
        line = line.strip()
        
        # parse the key/value pair
        if line:
          if " " in line:
            key, value = line.split(" ", 1)
            self.contents[key] = value
          else:
            self.contents[line] = "" # no value was provided
      
      self.contentsLock.release()
  
  def save(self, saveBackup=True):
    """
    Writes the contents of the current configuration. If a configuration file
    already exists then merges as follows:
    - comments and file contents not in this config are left unchanged
    - lines with duplicate keys are stripped (first instance is kept)
    - existing enries are overwritten with their new values, preserving the
      positioning of inline comments if able
    - config entries not in the file are appended to the end in alphabetical
      order
    
    If problems arise in writting (such as an unset path or insufficient
    permissions) result in an IOError.
    
    Arguments:
      saveBackup - if true and a file already exists then it's saved (with
                   '.backup' appended to its filename)
    """
    
    pass # TODO: implement when persistence is needed

