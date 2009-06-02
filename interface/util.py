#!/usr/bin/env python
# util.py -- support functions common for arm user interface.
# Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

import curses

LABEL_ATTR = curses.A_STANDOUT          # default formatting constant

# colors curses can handle
COLOR_LIST = (("red", curses.COLOR_RED),
             ("green", curses.COLOR_GREEN),
             ("yellow", curses.COLOR_YELLOW),
             ("blue", curses.COLOR_BLUE),
             ("cyan", curses.COLOR_CYAN),
             ("magenta", curses.COLOR_MAGENTA),
             ("black", curses.COLOR_BLACK),
             ("white", curses.COLOR_WHITE))

# foreground color mappings (starts uninitialized - all colors associated with default white fg / black bg)
COLOR_ATTR_INITIALIZED = False
COLOR_ATTR = dict([(color[0], 0) for color in COLOR_LIST])

def initColors():
  """
  Initializes color mappings for the current curses. This needs to be called
  after curses.initscr().
  """
  
  global COLOR_ATTR_INITIALIZED
  if not COLOR_ATTR_INITIALIZED:
    COLOR_ATTR_INITIALIZED = True
    
    # if color support is available initializes color mappings
    if curses.has_colors():
      colorpair = 0
      
      for name, fgColor in COLOR_LIST:
        colorpair += 1
        curses.init_pair(colorpair, fgColor, -1) # -1 allows for default (possibly transparent) background
        COLOR_ATTR[name] = curses.color_pair(colorpair)

def getColor(color):
  """
  Provides attribute corresponding to a given text color. Supported colors
  include:
  red, green, yellow, blue, cyan, magenta, black, and white
  
  If color support isn't available then this uses the default terminal coloring
  scheme.
  """
  
  return COLOR_ATTR[color]

def getSizeLabel(bytes):
  """
  Converts byte count into label in its most significant units, for instance
  7500 bytes would return "7 KB".
  """
  
  if bytes >= 1073741824: return "%i GB" % (bytes / 1073741824)
  elif bytes >= 1048576: return "%i MB" % (bytes / 1048576)
  elif bytes >= 1024: return "%i KB" % (bytes / 1024)
  else: return "%i bytes" % bytes

class TermSubwindow():
  """
  Wrapper for curses subwindows. This provides safe proxies to common methods.
  """
  
  def __init__(self, win, lock, startY):
    self.win = win          # associated curses subwindow
    self.lock = lock        # global curses lock
    self.startY = startY    # y-coordinate where made
    self.disable = False    # set if we detect being displaced
    self._resetBounds()     # sets last known dimensions of win
  
  def clear(self):
    """
    Erases window and resets bounds used in writting to it.
    """
    
    self.disable = self.startY > self.win.getparyx()[0]
    if not self.disable: self.win.erase()
    self._resetBounds()
  
  def refresh(self):
    """
    Proxy for window refresh.
    """
    
    if not self.disable: self.win.refresh()
  
  def addstr(self, y, x, msg, attr=curses.A_NORMAL):
    """
    Writes string to subwindow if able. This takes into account screen bounds
    to avoid making curses upset.
    """
    
    # subwindows need a character buffer (either in the x or y direction) from
    # actual content to prevent crash when shrank
    if self.maxX > x and self.maxY > y:
      if not self.disable: self.win.addstr(y, x, msg[:self.maxX - x - 1], attr)
  
  def _resetBounds(self):
    self.maxY, self.maxX = self.win.getmaxyx()

