"""
Panel providing raw control port access with syntax hilighting, usage
information, tab completion, and other usability features.
"""

import curses

from util import enum, panel, textInput, torTools, uiTools

from TorCtl import TorCtl

Formats = enum.Enum("PROMPT", "INPUT", "INPUT_INTERPRETOR", "INPUT_CMD", "INPUT_ARG", "OUTPUT", "USAGE", "HELP", "ERROR")

PROMPT = ">>> "
USAGE_INFO = "to use this panel press enter"

# limits used for cropping
COMMAND_BACKLOG = 100
LINES_BACKLOG = 2000

class InterpretorPanel(panel.Panel):
  def __init__(self, stdscr):
    panel.Panel.__init__(self, stdscr, "interpretor", 0)
    self.isInputMode = False
    self.scroll = 0
    self.formats = {}           # lazy loaded curses formatting constants
    self.previousCommands = []  # user input, newest to oldest
    
    # contents of the panel (oldest to newest), each line is a list of (msg,
    # format enum) tuples
    
    self.contents = [[(PROMPT, Formats.PROMPT), (USAGE_INFO, Formats.USAGE)]]
  
  def prompt(self):
    """
    Enables the interpretor, prompting for input until the user enters esc or
    a blank line.
    """
    
    if not self.formats: self._initFormats()
    self.isInputMode = True
    
    panel.CURSES_LOCK.acquire()
    
    while self.isInputMode:
      self.redraw(True)
      
      # intercepts input so user can cycle through the history
      validator = textInput.BasicValidator()
      validator = textInput.HistoryValidator(self.previousCommands, validator)
      
      xOffset = len(PROMPT)
      if len(self.contents) > self.maxY - 1:
        xOffset += 3 # offset for scrollbar
      
      input = self.getstr(min(self.maxY - 1, len(self.contents)), xOffset, "", self.formats[Formats.INPUT], validator = validator)
      
      isDone = self.handleQuery(input)
      
      if isDone:
        self.isInputMode = False
        self.redraw(True)
    
    panel.CURSES_LOCK.release()
  
  def handleQuery(self, input):
    """
    Processes the given input. Requests starting with a '/' are special
    commands to the interpretor, and anything else is sent to the control port.
    This returns a boolean to indicate if the interpretor should terminate or
    not.
    
    Arguments:
      input - user input to be processed
    """
    
    if not input or not input.strip(): return True
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
    
    self.previousCommands.insert(0, input)
    self.previousCommands = self.previousCommands[:COMMAND_BACKLOG]
    
    promptEntry = self.contents.pop() # removes old prompt entry
    self.contents += _splitOnNewlines(inputEntry)
    self.contents += _splitOnNewlines(outputEntry)
    self.contents.append(promptEntry)
    
    # if too long then crop lines
    cropLines = len(self.contents) - LINES_BACKLOG
    if cropLines > 0: self.contents = self.contents[cropLines:]
    
    return False
  
  def handleKey(self, key):
    # TODO: allow contents to be searched (with hilighting?)
    
    isKeystrokeConsumed = True
    if uiTools.isSelectionKey(key):
      self.prompt()
    elif uiTools.isScrollKey(key) and not self.isInputMode:
      pageHeight = self.getPreferredSize()[0] - 1
      newScroll = uiTools.getScrollPosition(key, self.scroll, pageHeight, len(self.contents))
      
      if self.scroll != newScroll:
        self.scroll = newScroll
        self.redraw(True)
    else: isKeystrokeConsumed = False
    
    return isKeystrokeConsumed
  
  def draw(self, width, height):
    if not self.formats: self._initFormats()
    
    # page title
    usageMsg = " (enter \"/help\" for usage or a blank line to stop)" if self.isInputMode else ""
    self.addstr(0, 0, "Control Interpretor%s:" % usageMsg, curses.A_STANDOUT)
    
    xOffset = 0
    if len(self.contents) > height - 1:
      # if we're in input mode then make sure the last line is visible
      if self.isInputMode:
        self.scroll = len(self.contents) - height + 1
      
      xOffset = 3
      self.addScrollBar(self.scroll, self.scroll + height - 1, len(self.contents), 1)
    
    # draws prior commands and output
    drawLine = 1
    for entry in self.contents[self.scroll:]:
      cursor = xOffset
      
      for msg, formatEntry in entry:
        format = self.formats.get(formatEntry, curses.A_NORMAL)
        self.addstr(drawLine, cursor, msg, format)
        cursor += len(msg)
      
      drawLine += 1
      if drawLine >= height: break
  
  def _initFormats(self):
    self.formats[Formats.PROMPT] = curses.A_BOLD | uiTools.getColor("green")
    self.formats[Formats.INPUT] = uiTools.getColor("cyan")
    self.formats[Formats.INPUT_INTERPRETOR] = curses.A_BOLD | uiTools.getColor("magenta")
    self.formats[Formats.INPUT_CMD] = curses.A_BOLD | uiTools.getColor("green")
    self.formats[Formats.INPUT_ARG] = curses.A_BOLD | uiTools.getColor("cyan")
    self.formats[Formats.OUTPUT] = uiTools.getColor("blue")
    self.formats[Formats.USAGE] = uiTools.getColor("cyan")
    self.formats[Formats.HELP] = uiTools.getColor("magenta")
    self.formats[Formats.ERROR] = curses.A_BOLD | uiTools.getColor("red")

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

