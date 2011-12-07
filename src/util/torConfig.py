"""
Helper functions for working with tor's configuration file.
"""

import os
import time
import socket
import threading

from util import enum, log, sysTools, torTools, uiTools

CONFIG = {"features.torrc.validate": True,
          "config.important": [],
          "torrc.alias": {},
          "torrc.label.size.b": [],
          "torrc.label.size.kb": [],
          "torrc.label.size.mb": [],
          "torrc.label.size.gb": [],
          "torrc.label.size.tb": [],
          "torrc.label.time.sec": [],
          "torrc.label.time.min": [],
          "torrc.label.time.hour": [],
          "torrc.label.time.day": [],
          "torrc.label.time.week": [],
          "log.torrc.readFailed": log.WARN,
          "log.configDescriptions.unrecognizedCategory": log.NOTICE,
          "log.torrc.validation.unnecessaryTorrcEntries": log.NOTICE,
          "log.torrc.validation.torStateDiffers": log.WARN}

# enums and values for numeric torrc entries
ValueType = enum.Enum("UNRECOGNIZED", "SIZE", "TIME")
SIZE_MULT = {"b": 1, "kb": 1024, "mb": 1048576, "gb": 1073741824, "tb": 1099511627776}
TIME_MULT = {"sec": 1, "min": 60, "hour": 3600, "day": 86400, "week": 604800}

# enums for issues found during torrc validation:
# DUPLICATE  - entry is ignored due to being a duplicate
# MISMATCH   - the value doesn't match tor's current state
# MISSING    - value differs from its default but is missing from the torrc
# IS_DEFAULT - the configuration option matches tor's default
ValidationError = enum.Enum("DUPLICATE", "MISMATCH", "MISSING", "IS_DEFAULT")

# descriptions of tor's configuration options fetched from its man page
CONFIG_DESCRIPTIONS_LOCK = threading.RLock()
CONFIG_DESCRIPTIONS = {}

# categories for tor configuration options
Category = enum.Enum("GENERAL", "CLIENT", "RELAY", "DIRECTORY", "AUTHORITY", "HIDDEN_SERVICE", "TESTING", "UNKNOWN")

TORRC = None # singleton torrc instance
MAN_OPT_INDENT = 7 # indentation before options in the man page
MAN_EX_INDENT = 15 # indentation used for man page examples
PERSIST_ENTRY_DIVIDER = "-" * 80 + "\n" # splits config entries when saving to a file
MULTILINE_PARAM = None # cached multiline parameters (lazily loaded)

# torrc options that bind to ports
PORT_OPT = ("SocksPort", "ORPort", "DirPort", "ControlPort", "TransPort")

def loadConfig(config):
  config.update(CONFIG)
  
  # stores lowercase entries to drop case sensitivity
  CONFIG["config.important"] = [entry.lower() for entry in CONFIG["config.important"]]
  
  for configKey in config.getKeys():
    # fetches any config.summary.* values
    if configKey.startswith("config.summary."):
      CONFIG[configKey.lower()] = config.get(configKey)
  
  # all the torrc.label.* values are comma separated lists
  for configKey in CONFIG.keys():
    if configKey.startswith("torrc.label."):
      configValues = config.get(configKey, "").split(",")
      if configValues: CONFIG[configKey] = [val.strip() for val in configValues]

class ManPageEntry:
  """
  Information provided about a tor configuration option in its man page entry.
  """
  
  def __init__(self, option, index, category, argUsage, description):
    self.option = option
    self.index = index
    self.category = category
    self.argUsage = argUsage
    self.description = description

def getTorrc():
  """
  Singleton constructor for a Controller. Be aware that this starts as being
  unloaded, needing the torrc contents to be loaded before being functional.
  """
  
  global TORRC
  if TORRC == None: TORRC = Torrc()
  return TORRC

def loadOptionDescriptions(loadPath = None, checkVersion = True):
  """
  Fetches and parses descriptions for tor's configuration options from its man
  page. This can be a somewhat lengthy call, and raises an IOError if issues
  occure. When successful loading from a file this returns the version for the
  contents loaded.
  
  If available, this can load the configuration descriptions from a file where
  they were previously persisted to cut down on the load time (latency for this
  is around 200ms).
  
  Arguments:
    loadPath     - if set, this attempts to fetch the configuration
                   descriptions from the given path instead of the man page
    checkVersion - discards the results if true and tor's version doens't
                   match the cached descriptors, otherwise accepts anyway
  """
  
  CONFIG_DESCRIPTIONS_LOCK.acquire()
  CONFIG_DESCRIPTIONS.clear()
  
  raisedExc = None
  loadedVersion = ""
  try:
    if loadPath:
      # Input file is expected to be of the form:
      # <option>
      # <arg description>
      # <description, possibly multiple lines>
      # <PERSIST_ENTRY_DIVIDER>
      inputFile = open(loadPath, "r")
      inputFileContents = inputFile.readlines()
      inputFile.close()
      
      try:
        versionLine = inputFileContents.pop(0).rstrip()
        
        if versionLine.startswith("Tor Version "):
          fileVersion = versionLine[12:]
          loadedVersion = fileVersion
          torVersion = torTools.getConn().getInfo("version", "")
          
          if checkVersion and fileVersion != torVersion:
            msg = "wrong version, tor is %s but the file's from %s" % (torVersion, fileVersion)
            raise IOError(msg)
        else:
          raise IOError("unable to parse version")
        
        while inputFileContents:
          # gets category enum, failing if it doesn't exist
          category = inputFileContents.pop(0).rstrip()
          if not category in Category.values():
            baseMsg = "invalid category in input file: '%s'"
            raise IOError(baseMsg % category)
          
          # gets the position in the man page
          indexArg, indexStr = -1, inputFileContents.pop(0).rstrip()
          
          if indexStr.startswith("index: "):
            indexStr = indexStr[7:]
            
            if indexStr.isdigit(): indexArg = int(indexStr)
            else: raise IOError("non-numeric index value: %s" % indexStr)
          else: raise IOError("malformed index argument: %s"% indexStr)
          
          option = inputFileContents.pop(0).rstrip()
          argument = inputFileContents.pop(0).rstrip()
          
          description, loadedLine = "", inputFileContents.pop(0)
          while loadedLine != PERSIST_ENTRY_DIVIDER:
            description += loadedLine
            
            if inputFileContents: loadedLine = inputFileContents.pop(0)
            else: break
          
          CONFIG_DESCRIPTIONS[option.lower()] = ManPageEntry(option, indexArg, category, argument, description.rstrip())
      except IndexError:
        CONFIG_DESCRIPTIONS.clear()
        raise IOError("input file format is invalid")
    else:
      manCallResults = sysTools.call("man tor")
      
      if not manCallResults:
        raise IOError("man page not found")
      
      # Fetches all options available with this tor instance. This isn't
      # vital, and the validOptions are left empty if the call fails.
      conn, validOptions = torTools.getConn(), []
      configOptionQuery = conn.getInfo("config/names")
      if configOptionQuery:
        for line in configOptionQuery.strip().split("\n"):
          validOptions.append(line[:line.find(" ")].lower())
      
      optionCount, lastOption, lastArg = 0, None, None
      lastCategory, lastDescription = Category.GENERAL, ""
      for line in manCallResults:
        line = uiTools.getPrintable(line)
        strippedLine = line.strip()
        
        # we have content, but an indent less than an option (ignore line)
        #if strippedLine and not line.startswith(" " * MAN_OPT_INDENT): continue
        
        # line starts with an indent equivilant to a new config option
        isOptIndent = line.startswith(" " * MAN_OPT_INDENT) and line[MAN_OPT_INDENT] != " "
        
        isCategoryLine = not line.startswith(" ") and "OPTIONS" in line
        
        # if this is a category header or a new option, add an entry using the
        # buffered results
        if isOptIndent or isCategoryLine:
          # Filters the line based on if the option is recognized by tor or
          # not. This isn't necessary for arm, so if unable to make the check
          # then we skip filtering (no loss, the map will just have some extra
          # noise).
          strippedDescription = lastDescription.strip()
          if lastOption and (not validOptions or lastOption.lower() in validOptions):
            CONFIG_DESCRIPTIONS[lastOption.lower()] = ManPageEntry(lastOption, optionCount, lastCategory, lastArg, strippedDescription)
            optionCount += 1
          lastDescription = ""
          
          # parses the option and argument
          line = line.strip()
          divIndex = line.find(" ")
          if divIndex != -1:
            lastOption, lastArg = line[:divIndex], line[divIndex + 1:]
          
          # if this is a category header then switch it
          if isCategoryLine:
            if line.startswith("OPTIONS"): lastCategory = Category.GENERAL
            elif line.startswith("CLIENT"): lastCategory = Category.CLIENT
            elif line.startswith("SERVER"): lastCategory = Category.RELAY
            elif line.startswith("DIRECTORY SERVER"): lastCategory = Category.DIRECTORY
            elif line.startswith("DIRECTORY AUTHORITY SERVER"): lastCategory = Category.AUTHORITY
            elif line.startswith("HIDDEN SERVICE"): lastCategory = Category.HIDDEN_SERVICE
            elif line.startswith("TESTING NETWORK"): lastCategory = Category.TESTING
            else:
              msg = "Unrecognized category in the man page: %s" % line.strip()
              log.log(CONFIG["log.configDescriptions.unrecognizedCategory"], msg)
        else:
          # Appends the text to the running description. Empty lines and lines
          # starting with a specific indentation are used for formatting, for
          # instance the ExitPolicy and TestingTorNetwork entries.
          if lastDescription and lastDescription[-1] != "\n":
            lastDescription += " "
          
          if not strippedLine:
            lastDescription += "\n\n"
          elif line.startswith(" " * MAN_EX_INDENT):
            lastDescription += "    %s\n" % strippedLine
          else: lastDescription += strippedLine
  except IOError, exc:
    raisedExc = exc
  
  CONFIG_DESCRIPTIONS_LOCK.release()
  if raisedExc: raise raisedExc
  else: return loadedVersion

def saveOptionDescriptions(path):
  """
  Preserves the current configuration descriptors to the given path. This
  raises an IOError or OSError if unable to do so.
  
  Arguments:
    path - location to persist configuration descriptors
  """
  
  # make dir if the path doesn't already exist
  baseDir = os.path.dirname(path)
  if not os.path.exists(baseDir): os.makedirs(baseDir)
  outputFile = open(path, "w")
  
  CONFIG_DESCRIPTIONS_LOCK.acquire()
  sortedOptions = CONFIG_DESCRIPTIONS.keys()
  sortedOptions.sort()
  
  torVersion = torTools.getConn().getInfo("version", "")
  outputFile.write("Tor Version %s\n" % torVersion)
  for i in range(len(sortedOptions)):
    manEntry = getConfigDescription(sortedOptions[i])
    outputFile.write("%s\nindex: %i\n%s\n%s\n%s\n" % (manEntry.category, manEntry.index, manEntry.option, manEntry.argUsage, manEntry.description))
    if i != len(sortedOptions) - 1: outputFile.write(PERSIST_ENTRY_DIVIDER)
  
  outputFile.close()
  CONFIG_DESCRIPTIONS_LOCK.release()

def getConfigSummary(option):
  """
  Provides a short summary description of the configuration option. If none is
  known then this proivdes None.
  
  Arguments:
    option - tor config option
  """
  
  return CONFIG.get("config.summary.%s" % option.lower())

def isImportant(option):
  """
  Provides True if the option has the 'important' flag in the configuration,
  False otherwise.
  
  Arguments:
    option - tor config option
  """
  
  return option.lower() in CONFIG["config.important"]

def getConfigDescription(option):
  """
  Provides ManPageEntry instances populated with information fetched from the
  tor man page. This provides None if no such option has been loaded. If the
  man page is in the process of being loaded then this call blocks until it
  finishes.
  
  Arguments:
    option - tor config option
  """
  
  CONFIG_DESCRIPTIONS_LOCK.acquire()
  
  if option.lower() in CONFIG_DESCRIPTIONS:
    returnVal = CONFIG_DESCRIPTIONS[option.lower()]
  else: returnVal = None
  
  CONFIG_DESCRIPTIONS_LOCK.release()
  return returnVal

def getConfigOptions():
  """
  Provides the configuration options from the loaded man page. This is an empty
  list if no man page has been loaded.
  """
  
  CONFIG_DESCRIPTIONS_LOCK.acquire()
  
  returnVal = [CONFIG_DESCRIPTIONS[opt].option for opt in CONFIG_DESCRIPTIONS]
  
  CONFIG_DESCRIPTIONS_LOCK.release()
  return returnVal

def getConfigLocation():
  """
  Provides the location of the torrc, raising an IOError with the reason if the
  path can't be determined.
  """
  
  conn = torTools.getConn()
  configLocation = conn.getInfo("config-file")
  torPid, torPrefix = conn.getMyPid(), conn.getPathPrefix()
  if not configLocation: raise IOError("unable to query the torrc location")
  
  try:
    return torPrefix + sysTools.expandRelativePath(configLocation, torPid)
  except IOError, exc:
    raise IOError("querying tor's pwd failed because %s" % exc)

def getMultilineParameters():
  """
  Provides parameters that can be defined multiple times in the torrc without
  overwriting the value.
  """
  
  # fetches config options with the LINELIST (aka 'LineList'), LINELIST_S (aka
  # 'Dependent'), and LINELIST_V (aka 'Virtual') types
  global MULTILINE_PARAM
  if MULTILINE_PARAM == None:
    conn, multilineEntries = torTools.getConn(), []
    
    configOptionQuery = conn.getInfo("config/names")
    if configOptionQuery:
      for line in configOptionQuery.strip().split("\n"):
        confOption, confType = line.strip().split(" ", 1)
        if confType in ("LineList", "Dependant", "Virtual"):
          multilineEntries.append(confOption)
    else:
      # unable to query tor connection, so not caching results
      return ()
    
    MULTILINE_PARAM = multilineEntries
  
  return tuple(MULTILINE_PARAM)

def getCustomOptions(includeValue = False):
  """
  Provides the torrc parameters that differ from their defaults.
  
  Arguments:
    includeValue - provides the current value with results if true, otherwise
                   this just contains the options
  """
  
  configText = torTools.getConn().getInfo("config-text", "").strip()
  configLines = configText.split("\n")
  
  # removes any duplicates
  configLines = list(set(configLines))
  
  # The "GETINFO config-text" query only provides options that differ
  # from Tor's defaults with the exception of its Log and Nickname entries
  # which, even if undefined, returns "Log notice stdout" as per:
  # https://trac.torproject.org/projects/tor/ticket/2362
  #
  # If this is from the deb then it will be "Log notice file /var/log/tor/log"
  # due to special patching applied to it, as per:
  # https://trac.torproject.org/projects/tor/ticket/4602
  
  try: configLines.remove("Log notice stdout")
  except ValueError: pass
  
  try: configLines.remove("Log notice file /var/log/tor/log")
  except ValueError: pass
  
  try: configLines.remove("Nickname %s" % socket.gethostname())
  except ValueError: pass
  
  if includeValue: return configLines
  else: return [line[:line.find(" ")] for line in configLines]

def saveConf(destination = None, contents = None):
  """
  Saves the configuration to the given path. If this is equivilant to
  issuing a SAVECONF (the contents and destination match what tor's using)
  then that's done. Otherwise, this writes the contents directly. This raises
  an IOError if unsuccessful.
  
  Arguments:
    destination - path to be saved to, the current config location if None
    contents    - configuration to be saved, the current config if None
  """
  
  if destination:
    destination = os.path.abspath(destination)
  
  # fills default config values, and sets isSaveconf to false if they differ
  # from the arguments
  isSaveconf, startTime = True, time.time()
  
  currentConfig = getCustomOptions(True)
  if not contents: contents = currentConfig
  else: isSaveconf &= contents == currentConfig
  
  # The "GETINFO config-text" option was introduced in Tor version 0.2.2.7. If
  # we're writing custom contents then this is fine, but if we're trying to
  # save the current configuration then we need to fail if it's unavailable.
  # Otherwise we'd write a blank torrc as per...
  # https://trac.torproject.org/projects/tor/ticket/3614
  
  if contents == ['']:
    # double check that "GETINFO config-text" is unavailable rather than just
    # giving an empty result
    
    if torTools.getConn().getInfo("config-text") == None:
      raise IOError("determining the torrc requires Tor version 0.2.2.7")
  
  currentLocation = None
  try:
    currentLocation = getConfigLocation()
    if not destination: destination = currentLocation
    else: isSaveconf &= destination == currentLocation
  except IOError: pass
  
  if not destination: raise IOError("unable to determine the torrc's path")
  logMsg = "Saved config by %%s to %s (runtime: %%0.4f)" % destination
  
  # attempts SAVECONF if we're updating our torrc with the current state
  if isSaveconf:
    try:
      torTools.getConn().getTorCtl().save_conf()
      
      try: getTorrc().load()
      except IOError: pass
      
      log.log(log.DEBUG, logMsg % ("SAVECONF", time.time() - startTime))
      return # if successful then we're done
    except:
      # example error:
      # TorCtl.TorCtl.ErrorReply: 551 Unable to write configuration to disk.
      pass
  
  # if the SAVECONF fails or this is a custom save then write contents directly
  try:
    # make dir if the path doesn't already exist
    baseDir = os.path.dirname(destination)
    if not os.path.exists(baseDir): os.makedirs(baseDir)
    
    # saves the configuration to the file
    configFile = open(destination, "w")
    configFile.write("\n".join(contents))
    configFile.close()
  except (IOError, OSError), exc:
    raise IOError(exc)
  
  # reloads the cached torrc if overwriting it
  if destination == currentLocation:
    try: getTorrc().load()
    except IOError: pass
  
  log.log(log.DEBUG, logMsg % ("directly writing", time.time() - startTime))

def validate(contents = None):
  """
  Performs validation on the given torrc contents, providing back a listing of
  (line number, issue, msg) tuples for issues found. If the issue occures on a
  multiline torrc entry then the line number is for the last line of the entry.
  
  Arguments:
    contents - torrc contents
  """
  
  conn = torTools.getConn()
  customOptions = getCustomOptions()
  issuesFound, seenOptions = [], []
  
  # Strips comments and collapses multiline multi-line entries, for more
  # information see:
  # https://trac.torproject.org/projects/tor/ticket/1929
  strippedContents, multilineBuffer = [], ""
  for line in _stripComments(contents):
    if not line: strippedContents.append("")
    else:
      line = multilineBuffer + line
      multilineBuffer = ""
      
      if line.endswith("\\"):
        multilineBuffer = line[:-1]
        strippedContents.append("")
      else:
        strippedContents.append(line.strip())
  
  for lineNumber in range(len(strippedContents) - 1, -1, -1):
    lineText = strippedContents[lineNumber]
    if not lineText: continue
    
    lineComp = lineText.split(None, 1)
    if len(lineComp) == 2: option, value = lineComp
    else: option, value = lineText, ""
    
    # Tor is case insensetive when parsing its torrc. This poses a bit of an
    # issue for us because we want all of our checks to be case insensetive
    # too but also want messages to match the normal camel-case conventions.
    #
    # Using the customOptions to account for this. It contains the tor reported
    # options (camel case) and is either a matching set or the following defaut
    # value check will fail. Hence using that hash to correct the case.
    #
    # TODO: when refactoring for stem make this less confusing...
    
    for customOpt in customOptions:
      if customOpt.lower() == option.lower():
        option = customOpt
        break
    
    # if an aliased option then use its real name
    if option in CONFIG["torrc.alias"]:
      option = CONFIG["torrc.alias"][option]
    
    # most parameters are overwritten if defined multiple times
    if option in seenOptions and not option in getMultilineParameters():
      issuesFound.append((lineNumber, ValidationError.DUPLICATE, option))
      continue
    else: seenOptions.append(option)
    
    # checks if the value isn't necessary due to matching the defaults
    if not option in customOptions:
      issuesFound.append((lineNumber, ValidationError.IS_DEFAULT, option))
    
    # replace aliases with their recognized representation
    if option in CONFIG["torrc.alias"]:
      option = CONFIG["torrc.alias"][option]
    
    # tor appears to replace tabs with a space, for instance:
    # "accept\t*:563" is read back as "accept *:563"
    value = value.replace("\t", " ")
    
    # parse value if it's a size or time, expanding the units
    value, valueType = _parseConfValue(value)
    
    # issues GETCONF to get the values tor's currently configured to use
    torValues = conn.getOption(option, [], True)
    
    # multiline entries can be comma separated values (for both tor and conf)
    valueList = [value]
    if option in getMultilineParameters():
      valueList = [val.strip() for val in value.split(",")]
      
      fetchedValues, torValues = torValues, []
      for fetchedValue in fetchedValues:
        for fetchedEntry in fetchedValue.split(","):
          fetchedEntry = fetchedEntry.strip()
          if not fetchedEntry in torValues:
            torValues.append(fetchedEntry)
    
    for val in valueList:
      # checks if both the argument and tor's value are empty
      isBlankMatch = not val and not torValues
      
      if not isBlankMatch and not val in torValues:
        # converts corrections to reader friedly size values
        displayValues = torValues
        if valueType == ValueType.SIZE:
          displayValues = [uiTools.getSizeLabel(int(val)) for val in torValues]
        elif valueType == ValueType.TIME:
          displayValues = [uiTools.getTimeLabel(int(val)) for val in torValues]
        
        issuesFound.append((lineNumber, ValidationError.MISMATCH, ", ".join(displayValues)))
  
  # checks if any custom options are missing from the torrc
  for option in customOptions:
    # In new versions the 'DirReqStatistics' option is true by default and
    # disabled on startup if geoip lookups are unavailable. If this option is
    # missing then that's most likely the reason.
    #
    # https://trac.torproject.org/projects/tor/ticket/4237
    
    if option == "DirReqStatistics": continue
    
    if not option in seenOptions:
      issuesFound.append((None, ValidationError.MISSING, option))
  
  return issuesFound

def _parseConfValue(confArg):
  """
  Converts size or time values to their lowest units (bytes or seconds) which
  is what GETCONF calls provide. The returned is a tuple of the value and unit
  type.
  
  Arguments:
    confArg - torrc argument
  """
  
  if confArg.count(" ") == 1:
    val, unit = confArg.lower().split(" ", 1)
    if not val.isdigit(): return confArg, ValueType.UNRECOGNIZED
    mult, multType = _getUnitType(unit)
    
    if mult != None:
      return str(int(val) * mult), multType
  
  return confArg, ValueType.UNRECOGNIZED

def _getUnitType(unit):
  """
  Provides the type and multiplier for an argument's unit. The multiplier is
  None if the unit isn't recognized.
  
  Arguments:
    unit - string representation of a unit
  """
  
  for label in SIZE_MULT:
    if unit in CONFIG["torrc.label.size." + label]:
      return SIZE_MULT[label], ValueType.SIZE
  
  for label in TIME_MULT:
    if unit in CONFIG["torrc.label.time." + label]:
      return TIME_MULT[label], ValueType.TIME
  
  return None, ValueType.UNRECOGNIZED

def _stripComments(contents):
  """
  Removes comments and extra whitespace from the given torrc contents.
  
  Arguments:
    contents - torrc contents
  """
  
  strippedContents = []
  for line in contents:
    if line and "#" in line: line = line[:line.find("#")]
    strippedContents.append(line.strip())
  return strippedContents

class Torrc():
  """
  Wrapper for the torrc. All getters provide None if the contents are unloaded.
  """
  
  def __init__(self):
    self.contents = None
    self.configLocation = None
    self.valsLock = threading.RLock()
    
    # cached results for the current contents
    self.displayableContents = None
    self.strippedContents = None
    self.corrections = None
    
    # flag to indicate if we've given a load failure warning before
    self.isLoadFailWarned = False
  
  def load(self, logFailure = False):
    """
    Loads or reloads the torrc contents, raising an IOError if there's a
    problem.
    
    Arguments:
      logFailure - if the torrc fails to load and we've never provided a
                   warning for this before then logs a warning
    """
    
    self.valsLock.acquire()
    
    # clears contents and caches
    self.contents, self.configLocation = None, None
    self.displayableContents = None
    self.strippedContents = None
    self.corrections = None
    
    try:
      self.configLocation = getConfigLocation()
      configFile = open(self.configLocation, "r")
      self.contents = configFile.readlines()
      configFile.close()
    except IOError, exc:
      if logFailure and not self.isLoadFailWarned:
        msg = "Unable to load torrc (%s)" % sysTools.getFileErrorMsg(exc)
        log.log(CONFIG["log.torrc.readFailed"], msg)
        self.isLoadFailWarned = True
      
      self.valsLock.release()
      raise exc
    
    self.valsLock.release()
  
  def isLoaded(self):
    """
    Provides true if there's loaded contents, false otherwise.
    """
    
    return self.contents != None
  
  def getConfigLocation(self):
    """
    Provides the location of the loaded configuration contents. This may be
    available, even if the torrc failed to be loaded.
    """
    
    return self.configLocation
  
  def getContents(self):
    """
    Provides the contents of the configuration file.
    """
    
    self.valsLock.acquire()
    returnVal = list(self.contents) if self.contents else None
    self.valsLock.release()
    return returnVal
  
  def getDisplayContents(self, strip = False):
    """
    Provides the contents of the configuration file, formatted in a rendering
    frindly fashion:
    - Tabs print as three spaces. Keeping them as tabs is problematic for
      layouts since it's counted as a single character, but occupies several
      cells.
    - Strips control and unprintable characters.
    
    Arguments:
      strip - removes comments and extra whitespace if true
    """
    
    self.valsLock.acquire()
    
    if not self.isLoaded(): returnVal = None
    else:
      if self.displayableContents == None:
        # restricts contents to displayable characters
        self.displayableContents = []
        
        for lineNum in range(len(self.contents)):
          lineText = self.contents[lineNum]
          lineText = lineText.replace("\t", "   ")
          lineText = uiTools.getPrintable(lineText)
          self.displayableContents.append(lineText)
      
      if strip:
        if self.strippedContents == None:
          self.strippedContents = _stripComments(self.displayableContents)
        
        returnVal = list(self.strippedContents)
      else: returnVal = list(self.displayableContents)
    
    self.valsLock.release()
    return returnVal
  
  def getCorrections(self):
    """
    Performs validation on the loaded contents and provides back the
    corrections. If validation is disabled then this won't provide any
    results.
    """
    
    self.valsLock.acquire()
    
    # The torrc validation relies on 'GETINFO config-text' which was
    # introduced in tor 0.2.2.7-alpha so if we're using an earlier version
    # (or configured to skip torrc validation) then this is a no-op. For more
    # information see:
    # https://trac.torproject.org/projects/tor/ticket/2501
    
    if not self.isLoaded(): returnVal = None
    else:
      skipValidation = not CONFIG["features.torrc.validate"]
      skipValidation |= not torTools.getConn().isVersion("0.2.2.7-alpha")
      
      if skipValidation:
        log.log(log.INFO, "Skipping torrc validation (requires tor 0.2.2.7-alpha)")
        returnVal = {}
      else:
        if self.corrections == None:
          self.corrections = validate(self.contents)
        
        returnVal = list(self.corrections)
    
    self.valsLock.release()
    return returnVal
  
  def getLock(self):
    """
    Provides the lock governing concurrent access to the contents.
    """
    
    return self.valsLock
  
  def logValidationIssues(self):
    """
    Performs validation on the loaded contents, and logs warnings for issues
    that are found.
    """
    
    corrections = self.getCorrections()
    
    if corrections:
      duplicateOptions, defaultOptions, mismatchLines, missingOptions = [], [], [], []
      
      for lineNum, issue, msg in corrections:
        if issue == ValidationError.DUPLICATE:
          duplicateOptions.append("%s (line %i)" % (msg, lineNum + 1))
        elif issue == ValidationError.IS_DEFAULT:
          defaultOptions.append("%s (line %i)" % (msg, lineNum + 1))
        elif issue == ValidationError.MISMATCH: mismatchLines.append(lineNum + 1)
        elif issue == ValidationError.MISSING: missingOptions.append(msg)
      
      if duplicateOptions or defaultOptions:
        msg = "Unneeded torrc entries found. They've been highlighted in blue on the torrc page."
        
        if duplicateOptions:
          if len(duplicateOptions) > 1:
            msg += "\n- entries ignored due to having duplicates: "
          else:
            msg += "\n- entry ignored due to having a duplicate: "
          
          duplicateOptions.sort()
          msg += ", ".join(duplicateOptions)
        
        if defaultOptions:
          if len(defaultOptions) > 1:
            msg += "\n- entries match their default values: "
          else:
            msg += "\n- entry matches its default value: "
          
          defaultOptions.sort()
          msg += ", ".join(defaultOptions)
        
        log.log(CONFIG["log.torrc.validation.unnecessaryTorrcEntries"], msg)
      
      if mismatchLines or missingOptions:
        msg = "The torrc differs from what tor's using. You can issue a sighup to reload the torrc values by pressing x."
        
        if mismatchLines:
          if len(mismatchLines) > 1:
            msg += "\n- torrc values differ on lines: "
          else:
            msg += "\n- torrc value differs on line: "
          
          mismatchLines.sort()
          msg += ", ".join([str(val + 1) for val in mismatchLines])
          
        if missingOptions:
          if len(missingOptions) > 1:
            msg += "\n- configuration values are missing from the torrc: "
          else:
            msg += "\n- configuration value is missing from the torrc: "
          
          missingOptions.sort()
          msg += ", ".join(missingOptions)
        
        log.log(CONFIG["log.torrc.validation.torStateDiffers"], msg)

def _testConfigDescriptions():
  """
  Tester for the loadOptionDescriptions function, fetching the man page
  contents and dumping its parsed results.
  """
  
  loadOptionDescriptions()
  sortedOptions = CONFIG_DESCRIPTIONS.keys()
  sortedOptions.sort()
  
  for i in range(len(sortedOptions)):
    option = sortedOptions[i]
    argument, description = getConfigDescription(option)
    optLabel = "OPTION: \"%s\"" % option
    argLabel = "ARGUMENT: \"%s\"" % argument
    
    print "     %-45s %s" % (optLabel, argLabel)
    print "\"%s\"" % description
    if i != len(sortedOptions) - 1: print "-" * 80

def isRootNeeded(torrcPath):
  """
  Returns True if the given torrc needs root permissions to be ran, False
  otherwise. This raises an IOError if the torrc can't be read.
  
  Arguments:
    torrcPath - torrc to be checked
  """
  
  try:
    torrcFile = open(torrcPath, "r")
    torrcLines = torrcFile.readlines()
    torrcFile.close()
    
    for line in torrcLines:
      line = line.strip()
      
      isPortOpt = False
      for opt in PORT_OPT:
        if line.startswith(opt):
          isPortOpt = True
          break
      
      if isPortOpt and " " in line:
        arg = line.split(" ")[1]
        
        if arg.isdigit() and int(arg) <= 1024 and int(arg) != 0:
          return True
    
    return False
  except Exception, exc:
    raise IOError(exc)

def renderTorrc(template, options, commentIndent = 30):
  """
  Uses the given template to generate a nicely formatted torrc with the given
  options. The tempating language this recognizes is a simple one, recognizing
  the following options:
    [IF <option>]         # if <option> maps to true or a non-empty string
    [IF NOT <option>]     # logical inverse
    [IF <opt1> | <opt2>]  # logical or of the options
    [ELSE]          # if the prior conditional evaluated to false
    [END IF]        # ends the control block
    
    [<option>]      # inputs the option value, omitting the line if it maps
                    # to a boolean or empty string
    [NEWLINE]       # empty line, otherwise templating white space is ignored
  
  Arguments:
    template      - torrc template lines used to generate the results
    options       - mapping of keywords to their given values, with values
                    being booleans or strings (possibly multi-line)
    commentIndent - minimum column that comments align on
  """
  
  results = []
  templateIter = iter(template)
  commentLineFormat = "%%-%is%%s" % commentIndent
  
  try:
    while True:
      line = templateIter.next().strip()
      
      if line.startswith("[IF ") and line.endswith("]"):
        # checks if any of the conditional options are true or a non-empty string
        evaluatesTrue = False
        for cond in line[4:-1].split("|"):
          isInverse = False
          if cond.startswith("NOT "):
            isInverse = True
            cond = cond[4:]
          
          if isInverse != bool(options.get(cond.strip())):
            evaluatesTrue = True
            break
        
        if evaluatesTrue:
          continue
        else:
          # skips lines until we come to an else or the end of the block
          depth = 0
          
          while depth != -1:
            line = templateIter.next().strip()
            
            if line.startswith("[IF ") and line.endswith("]"): depth += 1
            elif line == "[END IF]": depth -= 1
            elif depth == 0 and line == "[ELSE]": depth -= 1
      elif line == "[ELSE]":
        # an else block we aren't using - skip to the end of it
        depth = 0
        
        while depth != -1:
          line = templateIter.next().strip()
          
          if line.startswith("[IF "): depth += 1
          elif line == "[END IF]": depth -= 1
      elif line == "[NEWLINE]":
        # explicit newline
        results.append("")
      elif line.startswith("#"):
        # comment only
        results.append(line)
      elif line.startswith("[") and line.endswith("]"):
        # completely dynamic entry
        optValue = options.get(line[1:-1])
        if optValue: results.append(optValue)
      else:
        # torrc option line
        option, arg, comment = "", "", ""
        parsedLine = line
        
        if "#" in parsedLine:
          parsedLine, comment = parsedLine.split("#", 1)
          parsedLine = parsedLine.strip()
          comment = "# %s" % comment.strip()
        
        # parses the argument from the option
        if " " in parsedLine.strip():
          option, arg = parsedLine.split(" ", 1)
          option = option.strip()
        else:
          log.log(log.INFO, "torrc template option lacks an argument: '%s'" % line)
          continue
        
        # inputs dynamic arguments
        if arg.startswith("[") and arg.endswith("]"):
          arg = options.get(arg[1:-1])
        
        # skips argument if it's false or an empty string
        if not arg: continue
        
        torrcEntry = "%s %s" % (option, arg)
        if comment: results.append(commentLineFormat % (torrcEntry + " ", comment))
        else: results.append(torrcEntry)
  except StopIteration: pass
  
  return "\n".join(results)

