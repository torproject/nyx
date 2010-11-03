"""
Helper functions for working with tor's configuration file.
"""

import os
import curses
import threading

from util import log, sysTools, torTools, uiTools

CONFIG = {"features.torrc.validate": True,
          "torrc.multiline": [],
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
          "log.configDescriptions.unrecognizedCategory": log.NOTICE}

# enums and values for numeric torrc entries
UNRECOGNIZED, SIZE_VALUE, TIME_VALUE = range(1, 4)
SIZE_MULT = {"b": 1, "kb": 1024, "mb": 1048576, "gb": 1073741824, "tb": 1099511627776}
TIME_MULT = {"sec": 1, "min": 60, "hour": 3600, "day": 86400, "week": 604800}

# enums for issues found during torrc validation:
# VAL_DUPLICATE - entry is ignored due to being a duplicate
# VAL_MISMATCH  - the value doesn't match tor's current state
VAL_DUPLICATE, VAL_MISMATCH = range(1, 3)

# descriptions of tor's configuration options fetched from its man page
CONFIG_DESCRIPTIONS_LOCK = threading.RLock()
CONFIG_DESCRIPTIONS = {}

# categories for tor configuration options
GENERAL, CLIENT, SERVER, DIRECTORY, AUTHORITY, HIDDEN_SERVICE, TESTING, UNKNOWN = range(1, 9)
OPTION_CATEGORY_STR = {GENERAL: "General",     CLIENT: "Client",
                       SERVER: "Relay",        DIRECTORY: "Directory",
                       AUTHORITY: "Authority", HIDDEN_SERVICE: "Hidden Service",
                       TESTING: "Testing",     UNKNOWN: "Unknown"}

TORRC = None # singleton torrc instance
MAN_OPT_INDENT = 7 # indentation before options in the man page
MAN_EX_INDENT = 15 # indentation used for man page examples
PERSIST_ENTRY_DIVIDER = "-" * 80 + "\n" # splits config entries when saving to a file

def loadConfig(config):
  CONFIG["torrc.multiline"] = config.get("torrc.multiline", [])
  CONFIG["torrc.alias"] = config.get("torrc.alias", {})
  
  # all the torrc.label.* values are comma separated lists
  for configKey in CONFIG.keys():
    if configKey.startswith("torrc.label."):
      configValues = config.get(configKey, "").split(",")
      if configValues: CONFIG[configKey] = [val.strip() for val in configValues]

def getTorrc():
  """
  Singleton constructor for a Controller. Be aware that this starts as being
  unloaded, needing the torrc contents to be loaded before being functional.
  """
  
  global TORRC
  if TORRC == None: TORRC = Torrc()
  return TORRC

def loadOptionDescriptions(loadPath = None):
  """
  Fetches and parses descriptions for tor's configuration options from its man
  page. This can be a somewhat lengthy call, and raises an IOError if issues
  occure.
  
  If available, this can load the configuration descriptions from a file where
  they were previously persisted to cut down on the load time (latency for this
  is around 200ms).
  
  Arguments:
    loadPath - if set, this attempts to fetch the configuration descriptions
               from the given path instead of the man page
  """
  
  CONFIG_DESCRIPTIONS_LOCK.acquire()
  CONFIG_DESCRIPTIONS.clear()
  
  raisedExc = None
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
      
      # constructs a reverse mapping for categories
      strToCat = dict([(OPTION_CATEGORY_STR[cat], cat) for cat in OPTION_CATEGORY_STR])
      
      try:
        while inputFileContents:
          # gets category enum, failing if it doesn't exist
          categoryStr = inputFileContents.pop(0).rstrip()
          if categoryStr in strToCat:
            category = strToCat[categoryStr]
          else:
            baseMsg = "invalid category in input file: '%s'"
            raise IOError(baseMsg % categoryStr)
          
          option = inputFileContents.pop(0).rstrip()
          argument = inputFileContents.pop(0).rstrip()
          
          description, loadedLine = "", inputFileContents.pop(0)
          while loadedLine != PERSIST_ENTRY_DIVIDER:
            description += loadedLine
            
            if inputFileContents: loadedLine = inputFileContents.pop(0)
            else: break
          
          CONFIG_DESCRIPTIONS[option.lower()] = (category, argument, description.rstrip())
      except IndexError:
        CONFIG_DESCRIPTIONS.clear()
        raise IOError("input file format is invalid")
    else:
      manCallResults = sysTools.call("man tor")
      
      # Fetches all options available with this tor instance. This isn't
      # vital, and the validOptions are left empty if the call fails.
      conn, validOptions = torTools.getConn(), []
      configOptionQuery = conn.getInfo("config/names").strip().split("\n")
      if configOptionQuery:
        validOptions = [line[:line.find(" ")].lower() for line in configOptionQuery]
      
      lastOption, lastArg = None, None
      lastCategory, lastDescription = GENERAL, ""
      for line in manCallResults:
        strippedLine = line.strip()
        
        # checks if this is a category header
        if not line.startswith(" ") and "OPTIONS" in line:
          if line.startswith("CLIENT"): lastCategory = CLIENT
          elif line.startswith("SERVER"): lastCategory = SERVER
          elif line.startswith("DIRECTORY SERVER"): lastCategory = DIRECTORY
          elif line.startswith("DIRECTORY AUTHORITY SERVER"): lastCategory = AUTHORITY
          elif line.startswith("HIDDEN SERVICE"): lastCategory = HIDDEN_SERVICE
          elif line.startswith("TESTING NETWORK"): lastCategory = TESTING
          else:
            msg = "Unrecognized category in the man page: %s" % line.strip()
            log.log(CONFIG["log.configDescriptions.unrecognizedCategory"], msg)
        
        # we have content, but an indent less than an option (ignore line)
        if strippedLine and not line.startswith(" " * MAN_OPT_INDENT): continue
        
        # line starts with an indent equivilant to a new config option
        isOptIndent = line.startswith(" " * MAN_OPT_INDENT) and line[MAN_OPT_INDENT] != " "
        
        if isOptIndent:
          # Filters the line based on if the option is recognized by tor or
          # not. This isn't necessary for arm, so if unable to make the check
          # then we skip filtering (no loss, the map will just have some extra
          # noise).
          strippedDescription = lastDescription.strip()
          if lastOption and (not validOptions or lastOption.lower() in validOptions):
            CONFIG_DESCRIPTIONS[lastOption.lower()] = (lastCategory, lastArg, strippedDescription)
          lastDescription = ""
          
          # parses the option and argument
          line = line.strip()
          divIndex = line.find(" ")
          if divIndex != -1:
            lastOption, lastArg = line[:divIndex], line[divIndex + 1:]
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

def saveOptionDescriptions(path):
  """
  Preserves the current configuration descriptors to the given path. This
  raises an IOError if unable to do so.
  
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
  
  for i in range(len(sortedOptions)):
    option = sortedOptions[i]
    category, argument, description = getConfigDescription(option)
    outputFile.write("%s\n%s\n%s\n%s\n" % (OPTION_CATEGORY_STR[category], option, argument, description))
    if i != len(sortedOptions) - 1: outputFile.write(PERSIST_ENTRY_DIVIDER)
  
  outputFile.close()
  CONFIG_DESCRIPTIONS_LOCK.release()

def getConfigDescription(option):
  """
  Provides a tuple of the form:
  (category, argument usage, description)
  
  with information for the tor tor configuration option fetched from its man
  page. This provides a type of UKNOWN if no such option has been loaded. If
  the man page is in the process of being loaded then this call blocks until
  it finishes.
  
  Arguments:
    option - tor config option
  """
  
  CONFIG_DESCRIPTIONS_LOCK.acquire()
  
  if option.lower() in CONFIG_DESCRIPTIONS:
    returnVal = CONFIG_DESCRIPTIONS[option.lower()]
  else: returnVal = (UNKNOWN, "", "")
  
  CONFIG_DESCRIPTIONS_LOCK.release()
  return returnVal

def getConfigLocation():
  """
  Provides the location of the torrc, raising an IOError with the reason if the
  path can't be determined.
  """
  
  conn = torTools.getConn()
  configLocation = conn.getInfo("config-file")
  if not configLocation: raise IOError("unable to query the torrc location")
  
  # checks if this is a relative path, needing the tor pwd to be appended
  if configLocation[0] != "/":
    torPid = conn.getMyPid()
    failureMsg = "querying tor's pwd failed because %s"
    if not torPid: raise IOError(failureMsg % "we couldn't get the pid")
    
    try:
      # pwdx results are of the form:
      # 3799: /home/atagar
      # 5839: No such process
      results = sysTools.call("pwdx %s" % torPid)
      if not results:
        raise IOError(failureMsg % "pwdx didn't return any results")
      elif results[0].endswith("No such process"):
        raise IOError(failureMsg % ("pwdx reported no process for pid " + torPid))
      elif len(results) != 1 or results.count(" ") != 1:
        raise IOError(failureMsg % "we got unexpected output from pwdx")
      else:
        pwdPath = results[0][results[0].find(" ") + 1:]
        configLocation = "%s/%s" % (pwdPath, configLocation)
    except IOError, exc:
      raise IOError(failureMsg % ("the pwdx call failed: " + str(exc)))
  
  return torTools.getPathPrefix() + configLocation

def validate(contents = None):
  """
  Performs validation on the given torrc contents, providing back a mapping of
  line numbers to tuples of the (issue, msg) found on them.
  
  Arguments:
    contents - torrc contents
  """
  
  conn = torTools.getConn()
  contents = _stripComments(contents)
  issuesFound, seenOptions = {}, []
  for lineNumber in range(len(contents) - 1, -1, -1):
    lineText = contents[lineNumber]
    if not lineText: continue
    
    lineComp = lineText.split(None, 1)
    if len(lineComp) == 2: option, value = lineComp
    else: option, value = lineText, ""
    
    # most parameters are overwritten if defined multiple times
    if option in seenOptions and not option in CONFIG["torrc.multiline"]:
      issuesFound[lineNumber] = (VAL_DUPLICATE, "")
      continue
    else: seenOptions.append(option)
    
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
    if option in CONFIG["torrc.multiline"]:
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
        if valueType == SIZE_VALUE:
          displayValues = [uiTools.getSizeLabel(int(val)) for val in torValues]
        elif valueType == TIME_VALUE:
          displayValues = [uiTools.getTimeLabel(int(val)) for val in torValues]
        
        issuesFound[lineNumber] = (VAL_MISMATCH, ", ".join(displayValues))
  
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
    if not val.isdigit(): return confArg, UNRECOGNIZED
    mult, multType = _getUnitType(unit)
    
    if mult != None:
      return str(int(val) * mult), multType
  
  return confArg, UNRECOGNIZED

def _getUnitType(unit):
  """
  Provides the type and multiplier for an argument's unit. The multiplier is
  None if the unit isn't recognized.
  
  Arguments:
    unit - string representation of a unit
  """
  
  for label in SIZE_MULT:
    if unit in CONFIG["torrc.label.size." + label]:
      return SIZE_MULT[label], SIZE_VALUE
  
  for label in TIME_MULT:
    if unit in CONFIG["torrc.label.time." + label]:
      return TIME_MULT[label], TIME_VALUE
  
  return None, UNRECOGNIZED

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
  
  def load(self):
    """
    Loads or reloads the torrc contents, raising an IOError if there's a
    problem.
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
          lineText = "".join([char for char in lineText if curses.ascii.isprint(char)])
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
    
    if not self.isLoaded(): returnVal = None
    elif not CONFIG["features.torrc.validate"]: returnVal = {}
    else:
      if self.corrections == None:
        self.corrections = validate(self.contents)
      
      returnVal = dict(self.corrections)
    
    self.valsLock.release()
    return returnVal
  
  def getLock(self):
    """
    Provides the lock governing concurrent access to the contents.
    """
    
    return self.valsLock

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

