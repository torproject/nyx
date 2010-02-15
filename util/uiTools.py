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

FORMAT_TAGS = {"<b>": curses.A_BOLD,
               "<u>": curses.A_UNDERLINE,
               "<h>": curses.A_STANDOUT}
for (colorLabel, cursesAttr) in COLOR_LIST: FORMAT_TAGS["<%s>" % colorLabel] = curses.A_NORMAL

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
      
      # maps color tags to initialized attributes
      for colorLabel in COLOR_ATTR.keys(): FORMAT_TAGS["<%s>" % colorLabel] = COLOR_ATTR[colorLabel]

def getColor(color):
  """
  Provides attribute corresponding to a given text color. Supported colors
  include:
  red, green, yellow, blue, cyan, magenta, black, and white
  
  If color support isn't available then this uses the default terminal coloring
  scheme.
  """
  
  return COLOR_ATTR[color]

def getSizeLabel(bytes, decimal = 0):
  """
  Converts byte count into label in its most significant units, for instance
  7500 bytes would return "7 KB".
  """
  
  format = "%%.%if" % decimal
  if bytes >= 1073741824: return (format + " GB") % (bytes / 1073741824.0)
  elif bytes >= 1048576: return (format + " MB") % (bytes / 1048576.0)
  elif bytes >= 1024: return (format + " KB") % (bytes / 1024.0)
  else: return "%i bytes" % bytes

def getTimeLabel(seconds, decimal = 0):
  """
  Concerts seconds into a time label truncated to its most significant units,
  for instance 7500 seconds would return "2h". Units go up through days.
  """
  
  format = "%%.%if" % decimal
  if seconds >= 86400: return (format + "d") % (seconds / 86400.0)
  elif seconds >= 3600: return (format + "h") % (seconds / 3600.0)
  elif seconds >= 60: return (format + "m") % (seconds / 60.0)
  else: return "%is" % seconds

def drawScrollBar(panel, drawTop, drawBottom, top, bottom, size):
  """
  Draws scroll bar reflecting position within a vertical listing. This is
  squared off at the bottom, having a layout like:
   | 
  *|
  *|
  *|
   |
  -+
  """
  
  if panel.maxY < 2: return # not enough room
  
  barTop = (drawBottom - drawTop) * top / size
  barSize = (drawBottom - drawTop) * (bottom - top) / size
  
  # makes sure bar isn't at top or bottom unless really at those extreme bounds
  if top > 0: barTop = max(barTop, 1)
  if bottom != size: barTop = min(barTop, drawBottom - drawTop - barSize - 2)
  
  for i in range(drawBottom - drawTop):
    if i >= barTop and i <= barTop + barSize:
      panel.addstr(i + drawTop, 0, " ", curses.A_STANDOUT)
  
  # draws box around scroll bar
  panel.win.vline(drawTop, 1, curses.ACS_VLINE, panel.maxY - 2)
  panel.win.vline(drawBottom, 1, curses.ACS_LRCORNER, 1)
  panel.win.hline(drawBottom, 0, curses.ACS_HLINE, 1)

