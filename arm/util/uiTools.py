"""
Toolkit for common ui tasks when working with curses. This provides a quick and
easy method of providing the following interface components:
- preinitialized curses color attributes
- unit conversion for labels
"""

import sys
import curses

from curses.ascii import isprint

from stem.util import conf, enum, log, system

# colors curses can handle
COLOR_LIST = {"red": curses.COLOR_RED,        "green": curses.COLOR_GREEN,
              "yellow": curses.COLOR_YELLOW,  "blue": curses.COLOR_BLUE,
              "cyan": curses.COLOR_CYAN,      "magenta": curses.COLOR_MAGENTA,
              "black": curses.COLOR_BLACK,    "white": curses.COLOR_WHITE}

# boolean for if we have color support enabled, None not yet determined
COLOR_IS_SUPPORTED = None

# mappings for getColor() - this uses the default terminal color scheme if
# color support is unavailable
COLOR_ATTR_INITIALIZED = False
COLOR_ATTR = dict([(color, 0) for color in COLOR_LIST])

Ending = enum.Enum("ELLIPSE", "HYPHEN")
SCROLL_KEYS = (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE, curses.KEY_NPAGE, curses.KEY_HOME, curses.KEY_END)

def conf_handler(key, value):
  if key == "features.colorOverride" and value != "none":
    try: setColorOverride(value)
    except ValueError, exc:
      log.notice(exc)

CONFIG = conf.config_dict("arm", {
  "features.colorOverride": "none",
  "features.colorInterface": True,
  "features.acsSupport": True,
}, conf_handler)

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

def isColorSupported():
  """
  True if the display supports showing color, false otherwise.
  """

  if COLOR_IS_SUPPORTED == None: _initColors()
  return COLOR_IS_SUPPORTED

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

  colorOverride = getColorOverride()
  if colorOverride: color = colorOverride
  if not COLOR_ATTR_INITIALIZED: _initColors()
  return COLOR_ATTR[color]

def setColorOverride(color = None):
  """
  Overwrites all requests for color with the given color instead. This raises
  a ValueError if the color is invalid.

  Arguments:
    color - name of the color to overwrite requests with, None to use normal
            coloring
  """

  if color == None:
    CONFIG["features.colorOverride"] = "none"
  elif color in COLOR_LIST.keys():
    CONFIG["features.colorOverride"] = color
  else: raise ValueError("\"%s\" isn't a valid color" % color)

def getColorOverride():
  """
  Provides the override color used by the interface, None if it isn't set.
  """

  colorOverride = CONFIG.get("features.colorOverride", "none")
  if colorOverride == "none": return None
  else: return colorOverride

def cropStr(msg, size, minWordLen = 4, minCrop = 0, endType = Ending.ELLIPSE, getRemainder = False):
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
                   Ending.ELLIPSE - includes an ellipse
                   Ending.HYPHEN - adds hyphen when breaking words
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
  if endType == Ending.ELLIPSE: size -= 3
  elif endType == Ending.HYPHEN and minWordLen != None: minWordLen += 1

  # checks if there isn't the minimum space needed to include anything
  lastWordbreak = msg.rfind(" ", 0, size + 1)

  if lastWordbreak == -1:
    # we're splitting the first word
    if minWordLen == None or size < minWordLen:
      if getRemainder: return ("", msg)
      else: return ""

    includeCrop = True
  else:
    lastWordbreak = len(msg[:lastWordbreak].rstrip()) # drops extra ending whitespaces
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
    if endType == Ending.HYPHEN:
      remainder = returnMsg[-1] + remainder
      returnMsg = returnMsg[:-1].rstrip() + "-"
  else: returnMsg, remainder = msg[:lastWordbreak], msg[lastWordbreak:]

  # if this is ending with a comma or period then strip it off
  if not getRemainder and returnMsg and returnMsg[-1] in (",", "."):
    returnMsg = returnMsg[:-1]

  if endType == Ending.ELLIPSE:
    returnMsg = returnMsg.rstrip() + "..."

  if getRemainder: return (returnMsg, remainder)
  else: return returnMsg

def padStr(msg, size, cropExtra = False):
  """
  Provides the string padded with whitespace to the given length.

  Arguments:
    msg       - string to be padded
    size      - length to be padded to
    cropExtra - crops string if it's longer than the size if true
  """

  if cropExtra: msg = msg[:size]
  return ("%%-%is" % size) % msg

def drawBox(panel, top, left, width, height, attr=curses.A_NORMAL):
  """
  Draws a box in the panel with the given bounds.

  Arguments:
    panel  - panel in which to draw
    top    - vertical position of the box's top
    left   - horizontal position of the box's left side
    width  - width of the drawn box
    height - height of the drawn box
    attr   - text attributes
  """

  # draws the top and bottom
  panel.hline(top, left + 1, width - 2, attr)
  panel.hline(top + height - 1, left + 1, width - 2, attr)

  # draws the left and right sides
  panel.vline(top + 1, left, height - 2, attr)
  panel.vline(top + 1, left + width - 1, height - 2, attr)

  # draws the corners
  panel.addch(top, left, curses.ACS_ULCORNER, attr)
  panel.addch(top, left + width - 1, curses.ACS_URCORNER, attr)
  panel.addch(top + height - 1, left, curses.ACS_LLCORNER, attr)

def isSelectionKey(key):
  """
  Returns true if the keycode matches the enter or space keys.

  Argument:
    key - keycode to be checked
  """

  return key in (curses.KEY_ENTER, 10, ord(' '))

def isScrollKey(key):
  """
  Returns true if the keycode is recognized by the getScrollPosition function
  for scrolling.

  Argument:
    key - keycode to be checked
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

        # makes sure the cursor is visible
        if self.cursorLoc < self.scrollLoc:
          self.scrollLoc = self.cursorLoc
        elif self.cursorLoc > self.scrollLoc + pageHeight - 1:
          self.scrollLoc = self.cursorLoc - pageHeight + 1

      # checks if the bottom would run off the content (this could be the
      # case when the content's size is dynamic and entries are removed)
      if len(content) > pageHeight:
        self.scrollLoc = min(self.scrollLoc, len(content) - pageHeight)

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

def is_wide_characters_supported():
  """
  Checks if our version of curses has wide character support. This is required
  to print unicode.

  :returns: **bool** that's **True** if curses supports wide characters, and
    **False** if it either can't or this can't be determined
  """

  try:
    # Gets the dynamic library used by the interpretor for curses. This uses
    # 'ldd' on Linux or 'otool -L' on OSX.
    #
    # atagar@fenrir:~/Desktop$ ldd /usr/lib/python2.6/lib-dynload/_curses.so
    #   linux-gate.so.1 =>  (0x00a51000)
    #   libncursesw.so.5 => /lib/libncursesw.so.5 (0x00faa000)
    #   libpthread.so.0 => /lib/tls/i686/cmov/libpthread.so.0 (0x002f1000)
    #   libc.so.6 => /lib/tls/i686/cmov/libc.so.6 (0x00158000)
    #   libdl.so.2 => /lib/tls/i686/cmov/libdl.so.2 (0x00398000)
    #   /lib/ld-linux.so.2 (0x00ca8000)
    #
    # atagar$ otool -L /System/Library/Frameworks/Python.framework/Versions/2.5/lib/python2.5/lib-dynload/_curses.so
    # /System/Library/Frameworks/Python.framework/Versions/2.5/lib/python2.5/lib-dynload/_curses.so:
    #   /usr/lib/libncurses.5.4.dylib (compatibility version 5.4.0, current version 5.4.0)
    #   /usr/lib/libgcc_s.1.dylib (compatibility version 1.0.0, current version 1.0.0)
    #   /usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 111.1.6)

    import _curses

    lib_dependency_lines = None

    if system.is_available("ldd"):
      lib_dependency_lines = system.call("ldd %s" % _curses.__file__)
    elif system.is_available("otool"):
      lib_dependency_lines = system.call("otool -L %s" % _curses.__file__)

    if lib_dependency_lines:
      for line in lib_dependency_lines:
        if "libncursesw" in line:
          return True
  except:
    pass

  return False

def _initColors():
  """
  Initializes color mappings usable by curses. This can only be done after
  calling curses.initscr().
  """

  global COLOR_ATTR_INITIALIZED, COLOR_IS_SUPPORTED
  if not COLOR_ATTR_INITIALIZED:
    # hack to replace all ACS characters with '+' if ACS support has been
    # manually disabled
    if not CONFIG["features.acsSupport"]:
      for item in curses.__dict__:
        if item.startswith("ACS_"):
          curses.__dict__[item] = ord('+')

      # replace a few common border pipes that are better rendered as '|' or
      # '-' instead

      curses.ACS_SBSB = ord('|')
      curses.ACS_VLINE = ord('|')
      curses.ACS_BSBS = ord('-')
      curses.ACS_HLINE = ord('-')

    COLOR_ATTR_INITIALIZED = True
    COLOR_IS_SUPPORTED = False
    if not CONFIG["features.colorInterface"]: return

    try: COLOR_IS_SUPPORTED = curses.has_colors()
    except curses.error: return # initscr hasn't been called yet

    # initializes color mappings if color support is available
    if COLOR_IS_SUPPORTED:
      colorpair = 0
      log.info("Terminal color support detected and enabled")

      for colorName in COLOR_LIST:
        fgColor = COLOR_LIST[colorName]
        bgColor = -1 # allows for default (possibly transparent) background
        colorpair += 1
        curses.init_pair(colorpair, fgColor, bgColor)
        COLOR_ATTR[colorName] = curses.color_pair(colorpair)
    else:
      log.info("Terminal color support unavailable")

