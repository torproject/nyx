"""
Panel providing raw control port access with syntax hilighting, usage
information, tab completion, and other usability features.
"""

import curses

from util import panel, textInput, torInterpretor, torTools, uiTools

USAGE_INFO = "to use this panel press enter"
PROMPT_LINE = [torInterpretor.PROMPT, (USAGE_INFO, torInterpretor.USAGE_FORMAT)]

# lazy loaded mapping of interpretor attributes to curses formatting constants
FORMATS = {}

def getFormat(formatAttr):
  """
  Provides the curses drawing attributes for torInterpretor formats.
  
  Arguments:
    formatAttr - list of formatting attributes
  """
  
  # initializes formats if they haven't yet been loaded
  if not FORMATS:
    for colorEnum in torInterpretor.Color.values():
      FORMATS[colorEnum] = uiTools.getColor(colorEnum.lower())
    
    FORMATS[torInterpretor.Attr.BOLD] = curses.A_BOLD
    FORMATS[torInterpretor.Attr.UNDERLINE] = curses.A_UNDERLINE
    FORMATS[torInterpretor.Attr.HILIGHT] = curses.A_STANDOUT
  
  cursesFormatting = curses.A_NORMAL
  
  for attr in formatAttr:
    cursesFormatting |= FORMATS.get(attr, curses.A_NORMAL)
  
  return cursesFormatting

class InterpretorPanel(panel.Panel):
  def __init__(self, stdscr):
    panel.Panel.__init__(self, stdscr, "interpretor", 0)
    self.interpretor = torInterpretor.ControlInterpretor()
    self.inputCompleter = torInterpretor.TorControlCompleter()
    self.isInputMode = False
    self.scroll = 0
  
  def prompt(self):
    """
    Enables the interpretor, prompting for input until the user enters esc or
    a blank line.
    """
    
    self.isInputMode = True
    panel.CURSES_LOCK.acquire()
    
    while self.isInputMode:
      self.redraw(True)
      
      # intercepts input so user can cycle through the history
      validator = textInput.BasicValidator()
      validator = textInput.HistoryValidator(list(reversed(self.interpretor.getBacklog())), validator)
      validator = textInput.TabCompleter(self.inputCompleter.getMatches, validator)
      
      xOffset = len(torInterpretor.PROMPT[0])
      displayLength = len(self.interpretor.getDisplayContents(PROMPT_LINE))
      if displayLength > self.maxY - 1:
        xOffset += 3 # offset for scrollbar
      
      inputLine = min(self.maxY - 1, displayLength)
      inputFormat = getFormat(torInterpretor.INPUT_FORMAT)
      input = self.getstr(inputLine, xOffset, "", inputFormat, validator = validator)
      if input == None: input = ""
      input, isDone = input.strip(), False
      
      if not input:
        # terminate input when we get a blank line
        isDone = True
      else:
        try:
          self.interpretor.handleQuery(input)
        except torInterpretor.InterpretorClosed:
          # Makes our control connection check if its been closed or not
          torTools.getConn().isAlive()
          
          isDone = True
      
      if isDone:
        self.isInputMode = False
        self.redraw(True)
    
    panel.CURSES_LOCK.release()
  
  def handleKey(self, key):
    isKeystrokeConsumed = True
    if uiTools.isSelectionKey(key):
      self.prompt()
    elif uiTools.isScrollKey(key) and not self.isInputMode:
      pageHeight = self.getPreferredSize()[0] - 1
      displayLength = len(self.interpretor.getDisplayContents(PROMPT_LINE))
      newScroll = uiTools.getScrollPosition(key, self.scroll, pageHeight, displayLength)
      
      if self.scroll != newScroll:
        self.scroll = newScroll
        self.redraw(True)
    else: isKeystrokeConsumed = False
    
    return isKeystrokeConsumed
  
  def draw(self, width, height):
    # page title
    usageMsg = " (enter \"/help\" for usage or a blank line to stop)" if self.isInputMode else ""
    self.addstr(0, 0, "Control Interpretor%s:" % usageMsg, curses.A_STANDOUT)
    
    xOffset = 0
    displayContents = self.interpretor.getDisplayContents(PROMPT_LINE)
    if len(displayContents) > height - 1:
      # if we're in input mode then make sure the last line is visible
      if self.isInputMode:
        self.scroll = len(displayContents) - height + 1
      
      xOffset = 3
      self.addScrollBar(self.scroll, self.scroll + height - 1, len(displayContents), 1)
    
    # draws prior commands and output
    drawLine = 1
    for entry in displayContents[self.scroll:]:
      cursor = xOffset
      
      for msg, formatEntry in entry:
        format = getFormat(formatEntry)
        self.addstr(drawLine, cursor, msg, format)
        cursor += len(msg)
      
      drawLine += 1
      if drawLine >= height: break
  
