"""
Provides input validators that provide text input with various capabilities.
These can be chained together with the first matching validator taking
precidence.
"""

import os
import curses

PASS = -1

class TextInputValidator:
  """
  Basic interface for validators. Implementations should override the handleKey
  method.
  """
  
  def __init__(self, nextValidator = None):
    self.nextValidator = nextValidator
  
  def validate(self, key, textbox):
    """
    Processes the given key input for the textbox. This may modify the
    textbox's content, cursor position, etc depending on the functionality
    of the validator. This returns the key that the textbox should interpret,
    PASS if this validator doesn't want to take any action.
    
    Arguments:
      key     - key code input from the user
      textbox - curses Textbox instance the input came from
    """
    
    result = self.handleKey(key, textbox)
    
    if result != PASS:
      return result
    elif self.nextValidator:
      return self.nextValidator.validate(key, textbox)
    else: return key
  
  def handleKey(self, key, textbox):
    """
    Process the given keycode with this validator, returning the keycode for
    the textbox to process, and PASS if this doesn't want to modify it.
    
    Arguments:
      key     - key code input from the user
      textbox - curses Textbox instance the input came from
    """
    
    return PASS

class BasicValidator(TextInputValidator):
  """
  Interceptor for keystrokes given to a textbox, doing the following:
  - quits by setting the input to curses.ascii.BEL when escape is pressed
  - stops the cursor at the end of the box's content when pressing the right
    arrow
  - home and end keys move to the start/end of the line
  """
  
  def handleKey(self, key, textbox):
    y, x = textbox.win.getyx()
    
    if curses.ascii.isprint(key) and x < textbox.maxx:
      # Shifts the existing text forward so input is an insert method rather
      # than replacement. The curses.textpad accepts an insert mode flag but
      # this has a couple issues...
      # - The flag is only available for Python 2.6+, before that the
      #   constructor only accepted a subwindow argument as per:
      #   https://trac.torproject.org/projects/tor/ticket/2354
      # - The textpad doesn't shift text that has text attributes. This is
      #   because keycodes read by textbox.win.inch() includes formatting,
      #   causing the curses.ascii.isprint() check it does to fail.
      
      currentInput = textbox.gather()
      textbox.win.addstr(y, x + 1, currentInput[x:textbox.maxx - 1])
      textbox.win.move(y, x) # reverts cursor movement during gather call
    elif key == 27:
      # curses.ascii.BEL is a character codes that causes textpad to terminate
      return curses.ascii.BEL
    elif key == curses.KEY_HOME:
      textbox.win.move(y, 0)
      return None
    elif key in (curses.KEY_END, curses.KEY_RIGHT):
      msgLen = len(textbox.gather())
      textbox.win.move(y, x) # reverts cursor movement during gather call
      
      if key == curses.KEY_END and msgLen > 0 and x < msgLen - 1:
        # if we're in the content then move to the end
        textbox.win.move(y, msgLen - 1)
        return None
      elif key == curses.KEY_RIGHT and x >= msgLen - 1:
        # don't move the cursor if there's no content after it
        return None
    elif key == 410:
      # if we're resizing the display during text entry then cancel it
      # (otherwise the input field is filled with nonprintable characters)
      return curses.ascii.BEL
    
    return PASS

class HistoryValidator(TextInputValidator):
  """
  This intercepts the up and down arrow keys to scroll through a backlog of
  previous commands.
  """
  
  def __init__(self, commandBacklog = [], nextValidator = None):
    TextInputValidator.__init__(self, nextValidator)
    
    # contents that can be scrolled back through, newest to oldest
    self.commandBacklog = commandBacklog
    
    # selected item from the backlog, -1 if we're not on a backlog item
    self.selectionIndex = -1
    
    # the fields input prior to selecting a backlog item
    self.customInput = ""
  
  def handleKey(self, key, textbox):
    if key in (curses.KEY_UP, curses.KEY_DOWN):
      offset = 1 if key == curses.KEY_UP else -1
      newSelection = self.selectionIndex + offset
      
      # constrains the new selection to valid bounds
      newSelection = max(-1, newSelection)
      newSelection = min(len(self.commandBacklog) - 1, newSelection)
      
      # skips if this is a no-op
      if self.selectionIndex == newSelection:
        return None
      
      # saves the previous input if we weren't on the backlog
      if self.selectionIndex == -1:
        self.customInput = textbox.gather().strip()
      
      if newSelection == -1: newInput = self.customInput
      else: newInput = self.commandBacklog[newSelection]
      
      y, _ = textbox.win.getyx()
      _, maxX = textbox.win.getmaxyx()
      textbox.win.clear()
      textbox.win.addstr(y, 0, newInput[:maxX - 1])
      textbox.win.move(y, min(len(newInput), maxX - 1))
      
      self.selectionIndex = newSelection
      return None
    
    return PASS

class TabCompleter(TextInputValidator):
  """
  Provides tab completion based on the current input, finishing if there's only
  a single match. This expects a functor that accepts the current input and
  provides matches.
  """
  
  def __init__(self, completer, nextValidator = None):
    TextInputValidator.__init__(self, nextValidator)
    
    # functor that accepts a string and gives a list of matches
    self.completer = completer
  
  def handleKey(self, key, textbox):
    # Matches against the tab key. The ord('\t') is nine, though strangely none
    # of the curses.KEY_*TAB constants match this...
    if key == 9:
      currentContents = textbox.gather().strip()
      matches = self.completer(currentContents)
      newInput = None
      
      if len(matches) == 1:
        # only a single match, fill it in
        newInput = matches[0]
      elif len(matches) > 1:
        # looks for a common prefix we can complete
        commonPrefix = os.path.commonprefix(matches) # weird that this comes from path...
        
        if commonPrefix != currentContents:
          newInput = commonPrefix
        
        # TODO: somehow display matches... this is not gonna be fun
      
      if newInput:
        y, _ = textbox.win.getyx()
        _, maxX = textbox.win.getmaxyx()
        textbox.win.clear()
        textbox.win.addstr(y, 0, newInput[:maxX - 1])
        textbox.win.move(y, min(len(newInput), maxX - 1))
      
      return None
    
    return PASS

