"""
Provides an interactive interpretor for working with the Tor control port. This
adds usability features like IRC style interpretor commands and, when ran
directly, history and tab completion.
"""

import readline # simply importing this provides history to raw_input

from util import enum, torTools

PROMPT = ">>> "
Formats = enum.Enum("PROMPT", "INPUT", "INPUT_INTERPRETOR", "INPUT_CMD", "INPUT_ARG", "OUTPUT", "USAGE", "HELP", "ERROR")

TERM_COLORS = ("BLACK", "RED", "GREEN", "YELLOW", "BLUE", "MAGENTA", "CYAN", "WHITE")

Color = enum.Enum(*TERM_COLORS)
BgColor = enum.Enum(*["BG_" + color for color in TERM_COLORS])
Attr = enum.Enum("BOLD", "UNDERLINE", "HILIGHT")

FG_ENCODING = dict([(Color.values()[i], str(30 + i)) for i in range(8)])
BG_ENCODING = dict([(BgColor.values()[i], str(40 + i)) for i in range(8)])
ATTR_ENCODING = {Attr.BOLD: "1", Attr.UNDERLINE: "4", Attr.HILIGHT: "7"}

CSI = "\x1B[%sm"
RESET = CSI % "0"

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
  else:
    raise IOError("BLARG! %s" % str(attr))
    return msg

def prompt():
  prompt = format(">>> ", Color.GREEN, Attr.BOLD)
  input = ""
  
  formatMap = {} # mapping of Format to Color and Attr enums
  formatMap[Formats.PROMPT] = (Attr.BOLD, Color.GREEN)
  formatMap[Formats.INPUT] = (Color.CYAN, )
  formatMap[Formats.INPUT_INTERPRETOR] = (Attr.BOLD, Color.MAGENTA)
  formatMap[Formats.INPUT_CMD] = (Attr.BOLD, Color.GREEN)
  formatMap[Formats.INPUT_ARG] = (Attr.BOLD, Color.CYAN)
  formatMap[Formats.OUTPUT] = (Color.BLUE, )
  formatMap[Formats.USAGE] = (Color.CYAN, )
  formatMap[Formats.HELP] = (Color.MAGENTA, )
  formatMap[Formats.ERROR] = (Attr.BOLD, Color.RED)
  
  while input != "/quit":
    input = raw_input(prompt)
    
    _, outputEntry = handleQuery(input)
    
    for line in outputEntry:
      outputLine = ""
      
      for msg, msgFormat in line:
        outputLine += format(msg, *formatMap[msgFormat])
      
      print outputLine

def handleQuery(input):
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
  inputEntry, outputEntry = [(PROMPT, Formats.PROMPT)], []
  conn = torTools.getConn()
  
  # input falls into three general categories:
  # - interpretor command which starts with a '/'
  # - controller commands handled by torTools (this allows for caching,
  #   proper handling by the rest of arm, etc)
  # - unrecognized controller command, this has the possability of confusing
  #   arm...
  
  if input.startswith("/"):
    # interpretor command
    inputEntry.append((input, Formats.INPUT_INTERPRETOR))
    outputEntry.append(("Not yet implemented...", Formats.ERROR)) # TODO: implement
    
    # TODO: add /help option
    # TODO: add /write option
  else:
    # controller command
    if " " in input: cmd, arg = input.split(" ", 1)
    else: cmd, arg = input, ""
    
    inputEntry.append((cmd + " ", Formats.INPUT_CMD))
    if arg: inputEntry.append((arg, Formats.INPUT_ARG))
    
    if cmd.upper() == "GETINFO":
      try:
        response = conn.getInfo(arg, suppressExc = False)
        outputEntry.append((response, Formats.OUTPUT))
      except Exception, exc:
        outputEntry.append((str(exc), Formats.ERROR))
    elif cmd.upper() == "SETCONF":
      if "=" in arg:
        param, value = arg.split("=", 1)
        
        try:
          conn.setOption(param.strip(), value.strip())
        except Exception, exc:
          outputEntry.append((str(exc), Formats.ERROR))
      else:
        # TODO: resets the attribute
        outputEntry.append(("Not yet implemented...", Formats.ERROR)) # TODO: implement
    else:
      try:
        response = conn.getTorCtl().sendAndRecv("%s\r\n" % input)
        
        for entry in response:
          # Response entries are tuples with the response code, body, and
          # extra info. For instance:
          # ('250', 'version=0.2.2.23-alpha (git-b85eb949b528f4d7)', None)
          
          if len(entry) == 3:
            outputEntry.append((entry[1], Formats.OUTPUT))
      except Exception, exc:
        outputEntry.append((str(exc), Formats.ERROR))
  
  return (_splitOnNewlines(inputEntry), _splitOnNewlines(outputEntry))

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

