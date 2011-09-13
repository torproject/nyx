"""
Provides an interactive interpretor for working with the Tor control port. This
adds usability features like IRC style interpretor commands and, when ran
directly, history and tab completion.
"""

import re
import readline

import version

from util import connections, enum, hostnames, torTools

INIT_MSG = """Arm %s Control Interpretor
Enter \"/help\" for usage information and \"/quit\" to stop.
""" % version.VERSION

TERM_COLORS = ("BLACK", "RED", "GREEN", "YELLOW", "BLUE", "MAGENTA", "CYAN", "WHITE")

Color = enum.Enum(*TERM_COLORS)
BgColor = enum.Enum(*["BG_" + color for color in TERM_COLORS])
Attr = enum.Enum("BOLD", "UNDERLINE", "HILIGHT")

FG_ENCODING = dict([(Color.values()[i], str(30 + i)) for i in range(8)])
BG_ENCODING = dict([(BgColor.values()[i], str(40 + i)) for i in range(8)])
ATTR_ENCODING = {Attr.BOLD: "1", Attr.UNDERLINE: "4", Attr.HILIGHT: "7"}

PROMPT = (">>> ", (Attr.BOLD, Color.GREEN))
INPUT_FORMAT = (Color.CYAN, )
INPUT_INTERPRETOR_FORMAT = (Attr.BOLD, Color.MAGENTA)
INPUT_CMD_FORMAT = (Attr.BOLD, Color.GREEN)
INPUT_ARG_FORMAT = (Attr.BOLD, Color.CYAN)
OUTPUT_FORMAT = (Color.BLUE, )
USAGE_FORMAT = (Color.CYAN, )
HELP_FORMAT = (Color.MAGENTA, )
ERROR_FORMAT = (Attr.BOLD, Color.RED)

CSI = "\x1B[%sm"
RESET = CSI % "0"

# limits used for cropping
BACKLOG_LIMIT = 100
CONTENT_LIMIT = 20000

class InterpretorClosed(Exception):
  """
  Exception raised when the interpretor should be shut down.
  """
  
  pass

def format(msg, *attr):
  """
  Simple terminal text formatting, using ANSI escape sequences from:
  https://secure.wikimedia.org/wikipedia/en/wiki/ANSI_escape_code#CSI_codes
  
  toolkits providing similar capabilities:
  * django.utils.termcolors
    https://code.djangoproject.com/browser/django/trunk/django/utils/termcolors.py
  
  * termcolor
    http://pypi.python.org/pypi/termcolor
  
  * colorama
    http://pypi.python.org/pypi/colorama
  
  Arguments:
    msg  - string to be formatted
    attr - text attributes, this can be Color, BgColor, or Attr enums and are
           case insensitive (so strings like "red" are fine)
  """
  
  encodings = []
  for textAttr in attr:
    textAttr, encoding = enum.toCamelCase(textAttr), None
    encoding = FG_ENCODING.get(textAttr, encoding)
    encoding = BG_ENCODING.get(textAttr, encoding)
    encoding = ATTR_ENCODING.get(textAttr, encoding)
    if encoding: encodings.append(encoding)
  
  if encodings:
    return (CSI % ";".join(encodings)) + msg + RESET
  else: return msg

class TorControlCompleter:
  """
  Command autocompleter, fetching the valid options from the attached Tor
  instance.
  """
  
  def __init__(self):
    self.commands = []
    conn = torTools.getConn()
    
    # adds all of the valid GETINFO options
    infoOptions = conn.getInfo("info/names")
    if infoOptions:
      for line in infoOptions.split("\n"):
        if " " in line:
          # skipping non-existant options mentioned in:
          # https://trac.torproject.org/projects/tor/ticket/3844
          
          if line.startswith("config/*") or line.startswith("dir-usage"):
            continue
          
          infoOpt = line.split(" ", 1)[0]
          
          # strips off the ending asterisk if it accepts a value
          if infoOpt.endswith("*"): infoOpt = infoOpt[:-1]
          
          self.commands.append("GETINFO %s" % infoOpt)
    else: self.commands.append("GETINFO ")
    
    # adds all of the valid GETCONF / SETCONF / RESETCONF options
    confOptions = conn.getInfo("config/names")
    if confOptions:
      # individual options are '<name> <type>' pairs
      confEntries = [opt.split(" ", 1)[0] for opt in confOptions.split("\n")]
      self.commands += ["GETCONF %s" % conf for conf in confEntries]
      self.commands += ["SETCONF %s " % conf for conf in confEntries]
      self.commands += ["RESETCONF %s" % conf for conf in confEntries]
    else:
      self.commands.append("GETCONF ")
      self.commands.append("SETCONF ")
      self.commands.append("RESETCONF ")
    
    # adds all of the valid SETEVENT options
    eventOptions = conn.getInfo("events/names")
    if eventOptions:
      self.commands += ["SETEVENT %s" % event for event in eventOptions.split(" ")]
    else: self.commands.append("SETEVENT ")
    
    # adds all of the valid USEFEATURE options
    featureOptions = conn.getInfo("features/names")
    if featureOptions:
      self.commands += ["USEFEATURE %s" % feature for feature in featureOptions.split(" ")]
    else: self.commands.append("USEFEATURE ")
    
    # adds all of the valid SIGNAL options
    # this can't yet be fetched dynamically, as per:
    # https://trac.torproject.org/projects/tor/ticket/3842
    
    signals = ("RELOAD", "SHUTDOWN", "DUMP", "DEBUG", "HALT", "HUP", "INT",
               "USR1", "USR2", "TERM", "NEWNYM", "CLEARDNSCACHE")
    self.commands += ["SIGNAL %s" % sig for sig in signals]
    
    # shouldn't use AUTHENTICATE since we only provide the prompt with an
    # authenticated controller connection
    #self.commands.append("AUTHENTICATE")
    
    # other options
    self.commands.append("SAVECONF")
    self.commands.append("MAPADDRESS ")
    self.commands.append("EXTENDCIRCUIT ")
    self.commands.append("SETCIRCUITPURPOSE ")
    self.commands.append("SETROUTERPURPOSE ")
    self.commands.append("ATTACHSTREAM ")
    self.commands.append("+POSTDESCRIPTOR ") # TODO: needs to support multiline options for this (ugg)
    self.commands.append("REDIRECTSTREAM ")
    self.commands.append("CLOSESTREAM ")
    self.commands.append("CLOSECIRCUIT ")
    self.commands.append("RESOLVE ")
    self.commands.append("PROTOCOLINFO ")
    self.commands.append("+LOADCONF") # TODO: another multiline...
    self.commands.append("TAKEOWNERSHIP")
    self.commands.append("QUIT") # TODO: give a confirmation when the user does this?
  
  def getMatches(self, text):
    """
    Provides all options that match the given input. This is case insensetive.
    
    Arguments:
      text - user input text to be matched against
    """
    
    return [cmd for cmd in self.commands if cmd.lower().startswith(text.lower())]
  
  def complete(self, text, state):
    """
    Provides case insensetive autocompletion options, acting as a functor for
    the readlines set_completer function.
    """
    
    for cmd in self.getMatches(text):
      if not state: return cmd
      else: state -= 1

class ControlInterpretor:
  """
  Interpretor that handles queries to the control port, providing usability
  imporvements like irc style help optoins. This tracks input and responses.
  """
  
  def __init__(self):
    self.backlog = []   # prior requests the user has made
    self.contents = []  # (msg, format list) tuples for what's been displayed
    self.writePath = "/tmp/torInterpretor_output" # last location we've saved to
  
  def getBacklog(self):
    """
    Provides the backlog of prior user input.
    """
    
    return self.backlog
  
  def getDisplayContents(self, appendPrompt = None):
    """
    Provides a list of lines to be displayed, each being a list of (msg,
    format) tuples for the content to be displayed. This is ordered as the
    oldest to newest.
    
    Arguments:
      appendPrompt - adds the given line to the end
    """
    
    if appendPrompt:
      return self.contents + [appendPrompt]
    else: return self.contents
  
  def doWrite(self, arg, outputEntry):
    """
    Performs the '/write' operation, which attempts to save the backlog to a
    given path, defaulting to the last location we write to.
    """
    
    if arg: self.writePath = arg
    outputLines = []
    
    for line in self.contents:
      outputLines.append("".join([msg for msg, _ in line]))
    
    try:
      outputFile = open(self.writePath, "w")
      outputFile.write("\n".join(outputLines))
      outputFile.close()
      outputEntry.append(("Interpretor backlog written to: %s" % self.writePath, OUTPUT_FORMAT))
    except IOError, exc:
      outputEntry.append(("Unable to write to '%s': %s" % (self.writePath, exc), ERROR_FORMAT))
  
  def doFind(self, arg, outputEntry):
    """
    Performs the '/find' operation, which lists output from the backlog which
    matches the given regex. Results are deduplicated and matches are bolded.
    """
    
    argMatcher = None
    
    if not arg:
      outputEntry.append(("Nothing to match against", ERROR_FORMAT))
    else:
      try: argMatcher = re.compile("(%s)" % arg)
      except: outputEntry.append(("Unable to compile regex '%s'" % arg, ERROR_FORMAT))
    
    if argMatcher:
      printedLines = []
      
      for line in self.contents:
        lineText = "".join([msg for msg, _ in line])
        
        # skip if this was user input or a duplicate
        if lineText.startswith(PROMPT[0]) or lineText in printedLines:
          continue
        
        match = argMatcher.search(lineText)
        if match:
          # outputs the matching line, with the match itself bolded
          outputEntry.append((lineText[:match.start()], OUTPUT_FORMAT))
          outputEntry.append((match.group(), (OUTPUT_FORMAT + (Attr.BOLD, ))))
          outputEntry.append((lineText[match.end():] + "\n", OUTPUT_FORMAT))
          printedLines.append(lineText)
  
  def doInfo(self, arg, outputEntry):
    """
    Performs the '/info' operation, looking up a relay by fingerprint, IP
    address, or nickname and printing its descriptor and consensus entries in a
    pretty fashion.
    """
    
    fingerprint, conn = None, torTools.getConn()
    
    # TODO: also recognize <ip>:<port> entries?
    
    # determines the fingerprint, leaving it unset and adding an error message
    # if unsuccessful
    if not arg:
      outputEntry.append(("No fingerprint or nickname to look up", ERROR_FORMAT))
    elif len(arg) == 40 and re.match("^[0-9a-fA-F]+$", arg):
      # we got a fingerprint (fourty character hex string)
      fingerprint = arg
    elif connections.isValidIpAddress(arg):
      # we got an ip address, look up the fingerprint
      fpMatches = conn.getRelayFingerprint(arg, getAllMatches = True)
      
      if len(fpMatches) == 0:
        outputEntry.append(("No relays found at %s" % arg, ERROR_FORMAT))
      elif len(fpMatches) == 1:
        fingerprint = fpMatches[0][1]
      else:
        outputEntry.append(("Multiple relays at %s, specify which by giving a port" % arg, ERROR_FORMAT))
        
        for i in range(len(fpMatches)):
          relayEntry = outputEntry[i]
          outputEntry.append(("  %i. or port: %-5s fingerprint: %s" % (i + 1, relayEntry[0], relayEntry[1]), ERROR_FORMAT))
    else:
      # we got something else, treat it as a nickname
      fingerprint = conn.getNicknameFingerprint(arg)
      
      if not fingerprint:
        outputEntry.append(("No relay with the nickname of '%s' found" % arg, ERROR_FORMAT))
    
    if fingerprint:
      consensusEntry = conn.getConsensusEntry(fingerprint)
      
      # The nickname, address, and port lookups are all based on the consensus
      # entry so if this succeeds we should be pretty confident that those
      # queries will work too.
      
      if not consensusEntry:
        outputEntry.append(("Unable to find consensus information for %s" % fingerprint, ERROR_FORMAT))
        return
      
      address, port = conn.getRelayAddress(fingerprint, (None, None))
      
      # ... but not sure enough that we won't check
      if not address or not port: return
      
      locale = conn.getInfo("ip-to-country/%s" % address, "??")
      hostname = hostnames.resolve(address, 10)
      
      # TODO: Most of the following is copied from the _getDetailContent method
      # of cli/connections/connEntry.py - useful bits should be refactored.
      consensusLines = consensusEntry.split("\n")
      
      firstLineComp = consensusLines[0].split(" ")
      if len(firstLineComp) >= 9:
        _, nickname, _, _, pubDate, pubTime, _, orPort, _ = firstLineComp[:9]
      else: nickname, pubDate, pubTime, orPort = "", "", "", ""
      
      flags = "unknown"
      if len(consensusLines) >= 2 and consensusLines[1].startswith("s "):
        flags = consensusLines[1][2:]
      
      exitPolicy = conn.getRelayExitPolicy(fingerprint)
      
      if exitPolicy: policyLabel = exitPolicy.getSummary()
      else: policyLabel = "unknown"
      
      # fetches information from the descriptor if it's available
      torVersion, platform, contact = "", "", ""
      descriptorEntry = conn.getDescriptorEntry(fingerprint)
      
      if descriptorEntry:
        for descLine in descriptorEntry.split("\n"):
          if descLine.startswith("platform"):
            # has the tor version and platform, ex:
            # platform Tor 0.2.1.29 (r318f470bc5f2ad43) on Linux x86_64
     
            torVersion = descLine[13:descLine.find(" ", 13)]
            platform = descLine[descLine.rfind(" on ") + 4:] 
          elif descLine.startswith("contact"):
            contact = descLine[8:]
     
            # clears up some highly common obscuring
            for alias in (" at ", " AT "): contact = contact.replace(alias, "@")
            for alias in (" dot ", " DOT "): contact = contact.replace(alias, ".")
     
            break # contact lines come after the platform
      
      headingAttr, infoAttr = (Attr.BOLD, Color.BLUE), ()
      
      outputEntry.append(("%s (%s)\n" % (nickname, fingerprint), infoAttr))
      
      outputEntry.append(("address: ", headingAttr))
      outputEntry.append(("%s:%s (%s, %s)\n" % (address, port, locale, hostname), infoAttr))
      
      outputEntry.append(("published: ", headingAttr))
      outputEntry.append(("%s %s" % (pubTime, pubDate) + "\n", infoAttr))
      
      if torVersion and platform:
        outputEntry.append(("os: ", headingAttr))
        outputEntry.append((platform + "\n", infoAttr))
        
        outputEntry.append(("version: ", headingAttr))
        outputEntry.append((torVersion + "\n", infoAttr))
      
      outputEntry.append(("flags: ", headingAttr))
      outputEntry.append((flags.replace(" ", ", ") + "\n", infoAttr))
      
      outputEntry.append(("exit policy: ", headingAttr))
      outputEntry.append((policyLabel + "\n", infoAttr))
      
      if contact:
        outputEntry.append(("contact: ", headingAttr))
        outputEntry.append((contact + "\n", infoAttr))
  
  def handleQuery(self, input):
    """
    Processes the given input. Requests starting with a '/' are special
    commands to the interpretor, and anything else is sent to the control port.
    This returns an input/output tuple, each entry being a list of lines, each
    line having a list of (msg, format) tuples for the content to be displayed.
    This raises a InterpretorClosed if the interpretor should be shut down.
    
    Arguments:
      input - user input to be processed
    """
    
    input = input.strip()
    
    # appends new input, cropping if too long
    self.backlog.append(input)
    backlogCrop = len(self.backlog) - BACKLOG_LIMIT
    if backlogCrop > 0: self.backlog = self.backlog[backlogCrop:]
    
    inputEntry, outputEntry = [PROMPT], []
    conn = torTools.getConn()
    
    # input falls into three general categories:
    # - interpretor command which starts with a '/'
    # - controller commands handled by torTools (this allows for caching,
    #   proper handling by the rest of arm, etc)
    # - unrecognized controller command, this has the possability of confusing
    #   arm...
    
    if " " in input: cmd, arg = input.split(" ", 1)
    else: cmd, arg = input, ""
    
    if cmd.startswith("/"):
      # interpretor command
      inputEntry.append((input, INPUT_INTERPRETOR_FORMAT))
      
      if cmd == "/quit": raise InterpretorClosed()
      elif cmd == "/write": self.doWrite(arg, outputEntry)
      elif cmd == "/find": self.doFind(arg, outputEntry)
      elif cmd == "/info": self.doInfo(arg, outputEntry)
      else:
        outputEntry.append(("Not yet implemented...", ERROR_FORMAT)) # TODO: implement
      
      # appends a newline so all interpretor commands have a blank before the prompt
      if outputEntry:
        lastEntry = outputEntry[-1]
        outputEntry[-1] = (lastEntry[0].rstrip() + "\n", lastEntry[1])
      
      # TODO: add /help option
    else:
      # controller command
      cmd = cmd.upper() # makes commands uppercase to match the spec
      
      inputEntry.append((cmd + " ", INPUT_CMD_FORMAT))
      if arg: inputEntry.append((arg, INPUT_ARG_FORMAT))
      
      if cmd == "GETINFO":
        try:
          response = conn.getInfo(arg, suppressExc = False)
          outputEntry.append((response, OUTPUT_FORMAT))
        except Exception, exc:
          outputEntry.append((str(exc), ERROR_FORMAT))
      elif cmd == "SETCONF":
        if "=" in arg:
          param, value = arg.split("=", 1)
          
          try:
            conn.setOption(param.strip(), value.strip())
          except Exception, exc:
            outputEntry.append((str(exc), ERROR_FORMAT))
        else:
          # TODO: resets the attribute
          outputEntry.append(("Not yet implemented...", ERROR_FORMAT)) # TODO: implement
      else:
        try:
          response = conn.getTorCtl().sendAndRecv("%s\r\n" % input)
          
          for entry in response:
            # Response entries are tuples with the response code, body, and
            # extra info. For instance:
            # ('250', 'version=0.2.2.23-alpha (git-b85eb949b528f4d7)', None)
            
            if len(entry) == 3:
              outputEntry.append((entry[1], OUTPUT_FORMAT))
        except Exception, exc:
          outputEntry.append((str(exc), ERROR_FORMAT))
    
    # converts to lists split on newlines
    inputLines = _splitOnNewlines(inputEntry)
    outputLines = _splitOnNewlines(outputEntry)
    
    # appends new contents, cropping if too long
    self.contents += inputLines + outputLines
    cropLines = len(self.contents) - CONTENT_LIMIT
    if cropLines > 0: self.contents = self.contents[cropLines:]
    
    return (inputLines, outputLines)

def prompt():
  # Cycling history via the readline module with up/down is buggy with a color
  # prompt. For more information see:
  # http://bugs.python.org/issue12972
  #
  # To work around this while keeping a color prompt I'm padding the prompt
  # with extra reset encodings so its length is non-rendered higher (around
  # sixty characters). There's two ways that this can go wrong...
  # - if the user uses up/down to display input longer than this non-rendered
  #   length then the original bug will manifest (screwed up prompt)
  # - if the terminal's width is smaller than the non-rendered prompt length
  #   then the cursor and some movement will be displaced
  
  prompt = format(">>> ", Color.GREEN, Attr.BOLD)
  prompt += "\x1b[0m" * 10
  
  input = ""
  
  # sets up tab autocompetion
  torCommands = TorControlCompleter()
  readline.parse_and_bind("tab: complete")
  readline.set_completer(torCommands.complete)
  
  # Essentially disables autocompletion by word delimiters. This is because
  # autocompletion options are full commands (ex. "GETINFO version") so we want
  # "GETINFO" to match to all the options rather than be treated as a complete
  # command by itself.
  
  readline.set_completer_delims("\n")
  interpretor = ControlInterpretor()
  
  print INIT_MSG
  
  while True:
    try:
      input = raw_input(prompt)
      _, outputEntry = interpretor.handleQuery(input)
    except:
      # moves cursor to the next line and terminates (most commonly
      # KeyboardInterrupt and EOFErro)
      print
      
      # stop daemons
      hostnames.stop()
      
      break
    
    for line in outputEntry:
      outputLine = ""
      
      for msg, msgFormat in line:
        outputLine += format(msg, *msgFormat)
      
      print outputLine

def _splitOnNewlines(entry):
  """
  Splits a list of (msg, format) tuples on newlines into a list of lines.
  
  Arguments:
    entry - list of display tuples
  """
  
  results, tmpLine = [], []
  entry = list(entry) # shallow copy
  
  while entry:
    msg, format = entry.pop(0)
    
    if "\n" in msg:
      msg, remainder = msg.split("\n", 1)
      entry.insert(0, (remainder, format))
      
      tmpLine.append((msg, format))
      results.append(tmpLine)
      tmpLine = []
    else:
      tmpLine.append((msg, format))
  
  if tmpLine: results.append(tmpLine)
  return results

