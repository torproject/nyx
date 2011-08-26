"""
Panel providing raw control port access with syntax hilighting, usage
information, tab completion, and other usability features.
"""

import curses

from util import enum, panel, textInput, torInterpretor, torTools, uiTools

from TorCtl import TorCtl

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
    
    self.contents = [[(torInterpretor.PROMPT, torInterpretor.Formats.PROMPT), (USAGE_INFO, torInterpretor.Formats.USAGE)]]
  
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
      
      xOffset = len(torInterpretor.PROMPT)
      if len(self.contents) > self.maxY - 1:
        xOffset += 3 # offset for scrollbar
      
      input = self.getstr(min(self.maxY - 1, len(self.contents)), xOffset, "", self.formats[torInterpretor.Formats.INPUT], validator = validator)
      input, isDone = input.strip(), False
      
      if not input:
        isDone = True
      else:
        self.previousCommands.insert(0, input)
        self.previousCommands = self.previousCommands[:COMMAND_BACKLOG]
        
        try:
          inputEntry, outputEntry = torInterpretor.handleQuery(input)
        except torInterpretor.InterpretorClosed:
          isDone = True
        
        promptEntry = self.contents.pop() # removes old prompt entry
        self.contents += inputEntry
        self.contents += outputEntry
        self.contents.append(promptEntry)
        
        # if too long then crop lines
        cropLines = len(self.contents) - LINES_BACKLOG
        if cropLines > 0: self.contents = self.contents[cropLines:]
      
      if isDone:
        self.isInputMode = False
        self.redraw(True)
    
    panel.CURSES_LOCK.release()
  
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
    self.formats[torInterpretor.Formats.PROMPT] = curses.A_BOLD | uiTools.getColor("green")
    self.formats[torInterpretor.Formats.INPUT] = uiTools.getColor("cyan")
    self.formats[torInterpretor.Formats.INPUT_INTERPRETOR] = curses.A_BOLD | uiTools.getColor("magenta")
    self.formats[torInterpretor.Formats.INPUT_CMD] = curses.A_BOLD | uiTools.getColor("green")
    self.formats[torInterpretor.Formats.INPUT_ARG] = curses.A_BOLD | uiTools.getColor("cyan")
    self.formats[torInterpretor.Formats.OUTPUT] = uiTools.getColor("blue")
    self.formats[torInterpretor.Formats.USAGE] = uiTools.getColor("cyan")
    self.formats[torInterpretor.Formats.HELP] = uiTools.getColor("magenta")
    self.formats[torInterpretor.Formats.ERROR] = curses.A_BOLD | uiTools.getColor("red")

