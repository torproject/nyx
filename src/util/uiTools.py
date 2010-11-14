"""
Toolkit for common ui tasks when working with curses. This provides a quick and
easy method of providing the following interface components:
- preinitialized curses color attributes
- unit conversion for labels
"""

import sys
import curses

from curses.ascii import isprint
from util import log

# colors curses can handle
COLOR_LIST = {"red": curses.COLOR_RED,        "green": curses.COLOR_GREEN,
              "yellow": curses.COLOR_YELLOW,  "blue": curses.COLOR_BLUE,
              "cyan": curses.COLOR_CYAN,      "magenta": curses.COLOR_MAGENTA,
              "black": curses.COLOR_BLACK,    "white": curses.COLOR_WHITE}

# mappings for getColor() - this uses the default terminal color scheme if
# color support is unavailable
COLOR_ATTR_INITIALIZED = False
COLOR_ATTR = dict([(color, 0) for color in COLOR_LIST])

# value tuples for label conversions (bits / bytes / seconds, short label, long label)
SIZE_UNITS_BITS =  [(140737488355328.0, " Pb", " Petabit"), (137438953472.0, " Tb", " Terabit"),
                    (134217728.0, " Gb", " Gigabit"),       (131072.0, " Mb", " Megabit"),
                    (128.0, " Kb", " Kilobit"),             (0.125, " b", " Bit")]
SIZE_UNITS_BYTES = [(1125899906842624.0, " PB", " Petabyte"), (1099511627776.0, " TB", " Terabyte"),
                    (1073741824.0, " GB", " Gigabyte"),       (1048576.0, " MB", " Megabyte"),
                    (1024.0, " KB", " Kilobyte"),             (1.0, " B", " Byte")]
TIME_UNITS = [(86400.0, "d", " day"), (3600.0, "h", " hour"),
              (60.0, "m", " minute"), (1.0, "s", " second")]

END_WITH_ELLIPSE, END_WITH_HYPHEN = range(1, 3)
SCROLL_KEYS = (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE, curses.KEY_NPAGE, curses.KEY_HOME, curses.KEY_END)
CONFIG = {"features.colorInterface": True,
          "log.cursesColorSupport": log.INFO}

def loadConfig(config):
  config.update(CONFIG)

def demoGlyphs():
  """
  Displays all ACS options with their corresponding representation. These are
  undocumented in the pydocs. For more information see the following man page:
  http://www.mkssoftware.com/docs/man5/terminfo.5.asp
  """
  
  try: curses.wrapper(_showGlyphs)
  except KeyboardInterrupt: pass # quit

def _showGlyphs(stdscr):
  """
  Renders a chart with the ACS glyphs.
  """
  
  # allows things like semi-transparent backgrounds
  try: curses.use_default_colors()
  except curses.error: pass
  
  # attempts to make the cursor invisible
  try: curses.curs_set(0)
  except curses.error: pass
  
  acsOptions = [item for item in curses.__dict__.items() if item[0].startswith("ACS_")]
  acsOptions.sort(key=lambda i: (i[1])) # order by character codes
  
  # displays a chart with all the glyphs and their representations
  height, width = stdscr.getmaxyx()
  if width < 30: return # not enough room to show a column
  columns = width / 30
  
  # display title
  stdscr.addstr(0, 0, "Curses Glyphs:", curses.A_STANDOUT)
  
  x, y = 0, 1
  while acsOptions:
    name, keycode = acsOptions.pop(0)
    stdscr.addstr(y, x * 30, "%s (%i)" % (name, keycode))
    stdscr.addch(y, (x * 30) + 25, keycode)
    
    x += 1
    if x >= columns:
      x, y = 0, y + 1
      if y >= height: break
  
  stdscr.getch() # quit on keyboard input

def getPrintable(line, keepNewlines = True):
  """
  Provides the line back with non-printable characters stripped.
  
  Arguments:
    line          - string to be processed
    stripNewlines - retains newlines if true, stripped otherwise
  """
  
  line = line.replace('\xc2', "'")
  line = "".join([char for char in line if (isprint(char) or (keepNewlines and char == "\n"))])
  return line

def getColor(color):
  """
  Provides attribute corresponding to a given text color. Supported colors
  include:
  red       green     yellow    blue
  cyan      magenta   black     white
  
  If color support isn't available or colors can't be initialized then this uses the 
  terminal's default coloring scheme.
  
  Arguments:
    color - name of the foreground color to be returned
  """
  
  if not COLOR_ATTR_INITIALIZED: _initColors()
  return COLOR_ATTR[color]

def cropStr(msg, size, minWordLen = 4, minCrop = 0, endType = END_WITH_ELLIPSE, getRemainder = False):
  """
  Provides the msg constrained to the given length, truncating on word breaks.
  If the last words is long this truncates mid-word with an ellipse. If there
  isn't room for even a truncated single word (or one word plus the ellipse if
  including those) then this provides an empty string. If a cropped string ends
  with a comma or period then it's stripped (unless we're providing the
  remainder back). Examples:
  
  cropStr("This is a looooong message", 17)
  "This is a looo..."
  
  cropStr("This is a looooong message", 12)
  "This is a..."
  
  cropStr("This is a looooong message", 3)
  ""
  
  Arguments:
    msg          - source text
    size         - room available for text
    minWordLen   - minimum characters before which a word is dropped, requires
                   whole word if None
    minCrop      - minimum characters that must be dropped if a word's cropped
    endType      - type of ending used when truncating:
                   None - blank ending
                   END_WITH_ELLIPSE - includes an ellipse
                   END_WITH_HYPHEN - adds hyphen when breaking words
    getRemainder - returns a tuple instead, with the second part being the
                   cropped portion of the message
  """
  
  # checks if there's room for the whole message
  if len(msg) <= size:
    if getRemainder: return (msg, "")
    else: return msg
  
  # avoids negative input
  size = max(0, size)
  if minWordLen != None: minWordLen = max(0, minWordLen)
  minCrop = max(0, minCrop)
  
  # since we're cropping, the effective space available is less with an
  # ellipse, and cropping words requires an extra space for hyphens
  if endType == END_WITH_ELLIPSE: size -= 3
  elif endType == END_WITH_HYPHEN and minWordLen != None: minWordLen += 1
  
  # checks if there isn't the minimum space needed to include anything
  lastWordbreak = msg.rfind(" ", 0, size + 1)
  if (minWordLen != None and size < minWordLen) or (minWordLen == None and lastWordbreak < 1):
    if getRemainder: return ("", msg)
    else: return ""
  
  if minWordLen == None: minWordLen = sys.maxint
  includeCrop = size - lastWordbreak - 1 >= minWordLen
  
  # if there's a max crop size then make sure we're cropping at least that many characters
  if includeCrop and minCrop:
    nextWordbreak = msg.find(" ", size)
    if nextWordbreak == -1: nextWordbreak = len(msg)
    includeCrop = nextWordbreak - size + 1 >= minCrop
  
  if includeCrop:
    returnMsg, remainder = msg[:size], msg[size:]
    if endType == END_WITH_HYPHEN:
      remainder = returnMsg[-1] + remainder
      returnMsg = returnMsg[:-1] + "-"
  else: returnMsg, remainder = msg[:lastWordbreak], msg[lastWordbreak:]
  
  # if this is ending with a comma or period then strip it off
  if not getRemainder and returnMsg[-1] in (",", "."): returnMsg = returnMsg[:-1]
  
  if endType == END_WITH_ELLIPSE: returnMsg += "..."
  
  if getRemainder: return (returnMsg, remainder)
  else: return returnMsg

def isScrollKey(key):
  """
  Returns true if the keycode is recognized by the getScrollPosition function
  for scrolling.
  """
  
  return key in SCROLL_KEYS

def getScrollPosition(key, position, pageHeight, contentHeight, isCursor = False):
  """
  Parses navigation keys, providing the new scroll possition the panel should
  use. Position is always between zero and (contentHeight - pageHeight). This
  handles the following keys:
  Up / Down - scrolls a position up or down
  Page Up / Page Down - scrolls by the pageHeight
  Home - top of the content
  End - bottom of the content
  
  This provides the input position if the key doesn't correspond to the above.
  
  Arguments:
    key           - keycode for the user's input
    position      - starting position
    pageHeight    - size of a single screen's worth of content
    contentHeight - total lines of content that can be scrolled
    isCursor      - tracks a cursor position rather than scroll if true
  """
  
  if isScrollKey(key):
    shift = 0
    if key == curses.KEY_UP: shift = -1
    elif key == curses.KEY_DOWN: shift = 1
    elif key == curses.KEY_PPAGE: shift = -pageHeight + 1 if isCursor else -pageHeight
    elif key == curses.KEY_NPAGE: shift = pageHeight - 1 if isCursor else pageHeight
    elif key == curses.KEY_HOME: shift = -contentHeight
    elif key == curses.KEY_END: shift = contentHeight
    
    # returns the shift, restricted to valid bounds
    maxLoc = contentHeight - 1 if isCursor else contentHeight - pageHeight
    return max(0, min(position + shift, maxLoc))
  else: return position

def getSizeLabel(bytes, decimal = 0, isLong = False, isBytes=True):
  """
  Converts byte count into label in its most significant units, for instance
  7500 bytes would return "7 KB". If the isLong option is used this expands
  unit labels to be the properly pluralized full word (for instance 'Kilobytes'
  rather than 'KB'). Units go up through PB.
  
  Example Usage:
    getSizeLabel(2000000) = '1 MB'
    getSizeLabel(1050, 2) = '1.02 KB'
    getSizeLabel(1050, 3, True) = '1.025 Kilobytes'
  
  Arguments:
    bytes   - source number of bytes for conversion
    decimal - number of decimal digits to be included
    isLong  - expands units label
    isBytes - provides units in bytes if true, bits otherwise
  """
  
  if isBytes: return _getLabel(SIZE_UNITS_BYTES, bytes, decimal, isLong)
  else: return _getLabel(SIZE_UNITS_BITS, bytes, decimal, isLong)

def getTimeLabel(seconds, decimal = 0, isLong = False):
  """
  Converts seconds into a time label truncated to its most significant units,
  for instance 7500 seconds would return "2h". Units go up through days.
  
  This defaults to presenting single character labels, but if the isLong option
  is used this expands labels to be the full word (space included and properly
  pluralized). For instance, "4h" would be "4 hours" and "1m" would become
  "1 minute".
  
  Example Usage:
    getTimeLabel(10000) = '2h'
    getTimeLabel(61, 1, True) = '1.0 minute'
    getTimeLabel(61, 2, True) = '1.01 minutes'
  
  Arguments:
    seconds - source number of seconds for conversion
    decimal - number of decimal digits to be included
    isLong  - expands units label
  """
  
  return _getLabel(TIME_UNITS, seconds, decimal, isLong)

def getTimeLabels(seconds, isLong = False):
  """
  Provides a list containing label conversions for each time unit, starting
  with its most significant units on down. Any counts that evaluate to zero are
  omitted.
  
  Example Usage:
    getTimeLabels(400) = ['6m', '40s']
    getTimeLabels(3640, True) = ['1 hour', '40 seconds']
  
  Arguments:
    seconds - source number of seconds for conversion
    isLong  - expands units label
  """
  
  timeLabels = []
  
  for countPerUnit, shortLabel, longLabel in TIME_UNITS:
    if seconds >= countPerUnit:
      timeLabels.append(_getLabel(TIME_UNITS, seconds, 0, isLong))
      seconds %= countPerUnit
  
  return timeLabels

class Scroller:
  """
  Tracks the scrolling position when there might be a visible cursor. This
  expects that there is a single line displayed per an entry in the contents.
  """
  
  def __init__(self, isCursorEnabled):
    self.scrollLoc, self.cursorLoc = 0, 0
    self.cursorSelection = None
    self.isCursorEnabled = isCursorEnabled
  
  def getScrollLoc(self, content, pageHeight):
    """
    Provides the scrolling location, taking into account its cursor's location
    content size, and page height.
    
    Arguments:
      content    - displayed content
      pageHeight - height of the display area for the content
    """
    
    if content and pageHeight:
      self.scrollLoc = max(0, min(self.scrollLoc, len(content) - pageHeight + 1))
      
      if self.isCursorEnabled:
        self.getCursorSelection(content) # resets the cursor location
        
        if self.cursorLoc < self.scrollLoc:
          self.scrollLoc = self.cursorLoc
        elif self.cursorLoc > self.scrollLoc + pageHeight - 1:
          self.scrollLoc = self.cursorLoc - pageHeight + 1
    
    return self.scrollLoc
  
  def getCursorSelection(self, content):
    """
    Provides the selected item in the content. This is the same entry until
    the cursor moves or it's no longer available (in which case it moves on to
    the next entry).
    
    Arguments:
      content - displayed content
    """
    
    # TODO: needs to handle duplicate entries when using this for the
    # connection panel
    
    if not self.isCursorEnabled: return None
    elif not content:
      self.cursorLoc, self.cursorSelection = 0, None
      return None
    
    self.cursorLoc = min(self.cursorLoc, len(content) - 1)
    if self.cursorSelection != None and self.cursorSelection in content:
      # moves cursor location to track the selection
      self.cursorLoc = content.index(self.cursorSelection)
    else:
      # select the next closest entry
      self.cursorSelection = content[self.cursorLoc]
    
    return self.cursorSelection
  
  def handleKey(self, key, content, pageHeight):
    """
    Moves either the scroll or cursor according to the given input.
    
    Arguments:
      key        - key code of user input
      content    - displayed content
      pageHeight - height of the display area for the content
    """
    
    if self.isCursorEnabled:
      self.getCursorSelection(content) # resets the cursor location
      startLoc = self.cursorLoc
    else: startLoc = self.scrollLoc
    
    newLoc = getScrollPosition(key, startLoc, pageHeight, len(content), self.isCursorEnabled)
    if startLoc != newLoc:
      if self.isCursorEnabled: self.cursorSelection = content[newLoc]
      else: self.scrollLoc = newLoc
      return True
    else: return False

def _getLabel(units, count, decimal, isLong):
  """
  Provides label corresponding to units of the highest significance in the
  provided set. This rounds down (ie, integer truncation after visible units).
  
  Arguments:
    units   - type of units to be used for conversion, a tuple containing
              (countPerUnit, shortLabel, longLabel)
    count   - number of base units being converted
    decimal - decimal precision of label
    isLong  - uses the long label if true, short label otherwise
  """
  
  format = "%%.%if" % decimal
  if count < 1:
    unitsLabel = units[-1][2] + "s" if isLong else units[-1][1]
    return "%s%s" % (format % count, unitsLabel)
  
  for countPerUnit, shortLabel, longLabel in units:
    if count >= countPerUnit:
      if count * 10 ** decimal % countPerUnit * 10 ** decimal == 0:
        # even division, keep it simple
        countLabel = format % (count / countPerUnit)
      else:
        # unfortunately the %f formatting has no method of rounding down, so
        # reducing value to only concern the digits that are visible - note
        # that this doesn't work with minuscule values (starts breaking down at
        # around eight decimal places) or edge cases when working with powers
        # of two
        croppedCount = count - (count % (countPerUnit / (10 ** decimal)))
        countLabel = format % (croppedCount / countPerUnit)
      
      if isLong:
        # plural if any of the visible units make it greater than one (for
        # instance 1.0003 is plural but 1.000 isn't)
        if decimal > 0: isPlural = count >= (countPerUnit + countPerUnit / (10 ** decimal))
        else: isPlural = count >= countPerUnit * 2
        return countLabel + longLabel + ("s" if isPlural else "")
      else: return countLabel + shortLabel

def _initColors():
  """
  Initializes color mappings usable by curses. This can only be done after
  calling curses.initscr().
  """
  
  global COLOR_ATTR_INITIALIZED
  if not COLOR_ATTR_INITIALIZED:
    COLOR_ATTR_INITIALIZED = True
    if not CONFIG["features.colorInterface"]: return
    
    try: hasColorSupport = curses.has_colors()
    except curses.error: return # initscr hasn't been called yet
    
    # initializes color mappings if color support is available
    if hasColorSupport:
      colorpair = 0
      log.log(CONFIG["log.cursesColorSupport"], "Terminal color support detected and enabled")
      
      for colorName in COLOR_LIST:
        fgColor = COLOR_LIST[colorName]
        bgColor = -1 # allows for default (possibly transparent) background
        colorpair += 1
        curses.init_pair(colorpair, fgColor, bgColor)
        COLOR_ATTR[colorName] = curses.color_pair(colorpair)
    else:
      log.log(CONFIG["log.cursesColorSupport"], "Terminal color support unavailable")

