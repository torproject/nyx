"""
Wrapper for safely working with curses subwindows.
"""

import curses
from sys import maxint
from threading import RLock

import uiTools

# TODO: external usage of clear and refresh are only used by popup (remove when alternative is used)

# global ui lock governing all panel instances (curses isn't thread save and 
# concurrency bugs produce especially sinister glitches)
CURSES_LOCK = RLock()

class Panel():
  """
  Wrapper for curses subwindows. This hides most of the ugliness in common
  curses operations including:
    - locking when concurrently drawing to multiple windows
    - gracefully handle terminal resizing
    - clip text that falls outside the panel
    - convenience methods for word wrap, inline formatting, etc
  
  This can't be used until it has a subwindow instance, which is done via the 
  recreate() function. Until this is done the top, maxX, and maxY parameters 
  are defaulted to -1.
  
  Parameters:
  win - current curses subwindow
  height - preferred (max) height of panel, -1 if infinite
  top - upper Y-coordinate within parent window
  maxX, maxY - cached bounds of subwindow
  """
  
  def __init__(self, height):
    self.win = None
    self.height = height
    self.top = -1
    self.maxY, self.maxX = -1, -1
    
    # when the terminal is shrank then expanded curses attempts to draw 
    # displaced windows in the wrong location - this results in graphical 
    # glitches if we let the panel be redrawn
    self.isDisplaced = True
  
  def draw(self):
    """
    Draws display's content. This is meant to be overwriten by 
    impelementations.
    """
    
    pass
  
  def redraw(self, block=False):
    """
    Clears display and redraws.
    """
    
    if self.win:
      if not CURSES_LOCK.acquire(block): return
      try:
        self.clear()
        self.draw()
        self.refresh()
      finally:
        CURSES_LOCK.release()
  
  def recreate(self, stdscr, newWidth=-1, newTop=None):
    """
    Creates a new subwindow for the panel if:
    - panel currently doesn't have a subwindow
    - the panel is being moved (top is different from newTop)
    - there's room for the panel to grow
    
    Returns True if subwindow's created, False otherwise.
    """
    
    if newTop == None: newTop = self.top
    
    # I'm not sure if recreating subwindows is some sort of memory leak but the
    # Python curses bindings seem to lack all of the following:
    # - subwindow deletion (to tell curses to free the memory)
    # - subwindow moving/resizing (to restore the displaced windows)
    # so this is the only option (besides removing subwindows entirly which 
    # would mean more complicated code and no more selective refreshing)
    
    y, x = stdscr.getmaxyx()
    self._resetBounds()
    
    if self.win and newTop > y:
      return False # trying to make panel out of bounds
    
    newHeight = max(0, y - newTop)
    if self.height != -1: newHeight = min(newHeight, self.height)
    
    recreate = False
    recreate |= self.top != newTop      # position has shifted
    recreate |= newHeight != self.maxY  # subwindow can grow (vertically)
    recreate |= self.isDisplaced        # resizing has bumped subwindow out of position
    recreate |= self.maxX != newWidth and newWidth != -1    # set to use a new width
    
    if recreate:
      if newWidth == -1: newWidth = x
      else: newWidth = min(newWidth, x)
      
      self.top = newTop
      newTop = min(newTop, y - 1) # better create a displaced window than leave it as None
      
      self.win = stdscr.subwin(newHeight, newWidth, newTop, 0)
      return True
    else: return False
  
  # TODO: merge into repaint when no longer needed
  def clear(self):
    """
    Erases window and resets bounds used in writting to it.
    """
    
    if self.win:
      self.isDisplaced = self.top > self.win.getparyx()[0]
      if not self.isDisplaced: self.win.erase()
      self._resetBounds()
  
  # TODO: merge into repaint when no longer needed
  def refresh(self):
    """
    Proxy for window refresh.
    """
    
    if self.win and not self.isDisplaced: self.win.refresh()
  
  def addstr(self, y, x, msg, attr=curses.A_NORMAL):
    """
    Writes string to subwindow if able. This takes into account screen bounds
    to avoid making curses upset.
    """
    
    # subwindows need a single character buffer (either in the x or y 
    # direction) from actual content to prevent crash when shrank
    if self.win and self.maxX > x and self.maxY > y and not self.isDisplaced:
      self.win.addstr(y, x, msg[:self.maxX - x - 1], attr)
  
  def addfstr(self, y, x, msg):
    """
    Writes string to subwindow. The message can contain xhtml-style tags for
    formatting, including:
    <b>text</b>               bold
    <u>text</u>               underline
    <h>text</h>               highlight
    <[color]>text</[color]>   use color (see COLOR_LIST for constants)
    
    Tag nexting is supported and tag closing is not strictly enforced. This 
    does not valididate input and unrecognized tags are treated as normal text.
    Currently this funtion has the following restrictions:
    - Duplicate tags nested (such as "<b><b>foo</b></b>") is invalid and may
    throw an error.
    - Color tags shouldn't be nested in each other (results are undefined).
    """
    
    if self.win and self.maxY > y and not self.isDisplaced:
      formatting = [curses.A_NORMAL]
      expectedCloseTags = []
      
      while self.maxX > x and len(msg) > 0:
        # finds next consumeable tag
        nextTag, nextTagIndex = None, maxint
        
        for tag in uiTools.FORMAT_TAGS.keys() + expectedCloseTags:
          tagLoc = msg.find(tag)
          if tagLoc != -1 and tagLoc < nextTagIndex:
            nextTag, nextTagIndex = tag, tagLoc
        
        # splits into text before and after tag
        if nextTag:
          msgSegment = msg[:nextTagIndex]
          msg = msg[nextTagIndex + len(nextTag):]
        else:
          msgSegment = msg
          msg = ""
        
        # adds text before tag with current formatting
        attr = 0
        for format in formatting: attr |= format
        self.win.addstr(y, x, msgSegment[:self.maxX - x - 1], attr)
        
        # applies tag attributes for future text
        if nextTag:
          if not nextTag.startswith("</"):
            # open tag - add formatting
            expectedCloseTags.append("</" + nextTag[1:])
            formatting.append(uiTools.FORMAT_TAGS[nextTag])
          else:
            # close tag - remove formatting
            expectedCloseTags.remove(nextTag)
            formatting.remove(uiTools.FORMAT_TAGS["<" + nextTag[2:]])
        
        x += len(msgSegment)
  
  def addstr_wrap(self, y, x, text, formatting, startX = 0, endX = -1, maxY = -1):
    """
    Writes text with word wrapping, returning the ending y/x coordinate.
    y: starting write line
    x: column offset from startX
    text / formatting: content to be written
    startX / endX: column bounds in which text may be written
    """
    
    if not text: return (y, x)          # nothing to write
    if endX == -1: endX = self.maxX     # defaults to writing to end of panel
    if maxY == -1: maxY = self.maxY + 1 # defaults to writing to bottom of panel
    lineWidth = endX - startX           # room for text
    while True:
      if len(text) > lineWidth - x - 1:
        chunkSize = text.rfind(" ", 0, lineWidth - x)
        writeText = text[:chunkSize]
        text = text[chunkSize:].strip()
        
        self.addstr(y, x + startX, writeText, formatting)
        y, x = y + 1, 0
        if y >= maxY: return (y, x)
      else:
        self.addstr(y, x + startX, text, formatting)
        return (y, x + len(text))
  
  def _resetBounds(self):
    if self.win: self.maxY, self.maxX = self.win.getmaxyx()
    else: self.maxY, self.maxX = -1, -1

