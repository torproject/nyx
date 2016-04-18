# Copyright 2010-2016, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Toolkit for working with curses. Curses earns its name, and this abstracts away
its usage providing us more easy to use high level functions. This abstraction
may also allow us to use libraries like `PDCurses <http://pdcurses.sourceforge.net/>`_
if we want Windows support in the future too.

**Module Overview:**

::

  start - initializes curses with the given function
  raw_screen - provides direct access to the curses screen
  key_input - get keypress by user
  str_input - text field where user can input a string
  curses_attr - curses encoded text attribute
  screen_size - provides the dimensions of our screen
  screenshot - dump of the present on-screen content

  is_color_supported - checks if terminal supports color output
  get_color_override - provides color we override requests with
  set_color_override - sets color we override requests with

  disable_acs - renders replacements for ACS characters
  is_wide_characters_supported - checks if curses supports wide character

  draw - renders subwindow that can be drawn into

  Subwindow - subwindow that can be drawn within
    |- addstr - draws a string
    |- addstr_wrap - draws a string with line wrapping
    |- box - draws box with the given dimensions
    |- hline - draws a horizontal line
    +- vline - draws a vertical line

  KeyInput - user keyboard input
    |- match - checks if this matches the given inputs
    |- is_scroll - true if key is used for scrolling
    +- is_selection - true if key should trigger selection

  Scroller - scrolls content with keyboard navigation
    |- location - present scroll location
    +- handle_key - moves scroll based on user input

  CursorScroller - scrolls content with a cursor for selecting items
    |- selection - present selection and scroll location
    +- handle_key - moves cursor based on user input

.. data:: Color (enum)

  Terminal colors.

  =========== ===========
  Color       Description
  =========== ===========
  **RED**     red color
  **GREEN**   green color
  **YELLOW**  yellow color
  **BLUE**    blue color
  **CYAN**    cyan color
  **MAGENTA** magenta color
  **BLACK**   black color
  **WHITE**   white color
  =========== ===========

.. data:: Attr (enum)

  Terminal text attributes.

  =================== ===========
  Attr                Description
  =================== ===========
  **NORMAL**          no text attributes
  **BOLD**            heavy typeface
  **UNDERLINE**       underlined text
  **HIGHLIGHT**       inverted foreground and background
  =================== ===========
"""

from __future__ import absolute_import

import collections
import curses
import curses.ascii
import curses.textpad
import threading

import stem.util.conf
import stem.util.enum
import stem.util.str_tools
import stem.util.system

from nyx import msg, log

# Curses screen we've initialized and lock for interacting with it. Curses
# isn't thread safe and concurrency bugs produce especially sinister glitches.

CURSES_SCREEN = None
CURSES_LOCK = threading.RLock()

# Text colors and attributes. These are *very* commonly used so including
# shorter aliases (so they can be referenced as just GREEN or BOLD).

Color = stem.util.enum.Enum('RED', 'GREEN', 'YELLOW', 'BLUE', 'CYAN', 'MAGENTA', 'BLACK', 'WHITE')
RED, GREEN, YELLOW, BLUE, CYAN, MAGENTA, BLACK, WHITE = list(Color)

Attr = stem.util.enum.Enum('NORMAL', 'BOLD', 'UNDERLINE', 'HIGHLIGHT')
NORMAL, BOLD, UNDERLINE, HIGHLIGHT = list(Attr)

CURSES_COLORS = {
  Color.RED: curses.COLOR_RED,
  Color.GREEN: curses.COLOR_GREEN,
  Color.YELLOW: curses.COLOR_YELLOW,
  Color.BLUE: curses.COLOR_BLUE,
  Color.CYAN: curses.COLOR_CYAN,
  Color.MAGENTA: curses.COLOR_MAGENTA,
  Color.BLACK: curses.COLOR_BLACK,
  Color.WHITE: curses.COLOR_WHITE,
}

CURSES_ATTRIBUTES = {
  Attr.NORMAL: curses.A_NORMAL,
  Attr.BOLD: curses.A_BOLD,
  Attr.UNDERLINE: curses.A_UNDERLINE,
  Attr.HIGHLIGHT: curses.A_STANDOUT,
}

DEFAULT_COLOR_ATTR = dict([(color, 0) for color in Color])
COLOR_ATTR = None

SCROLL_KEYS = (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE, curses.KEY_NPAGE, curses.KEY_HOME, curses.KEY_END)

SPECIAL_KEYS = {
  'up': curses.KEY_UP,
  'down': curses.KEY_DOWN,
  'left': curses.KEY_LEFT,
  'right': curses.KEY_RIGHT,
  'home': curses.KEY_HOME,
  'end': curses.KEY_END,
  'page_up': curses.KEY_PPAGE,
  'page_down': curses.KEY_NPAGE,
  'esc': 27,
}

Dimensions = collections.namedtuple('Dimensions', ['width', 'height'])


def conf_handler(key, value):
  if key == 'features.colorOverride':
    if value not in Color and value != 'None':
      raise ValueError(msg('usage.unable_to_set_color_override', color = value))
  elif key == 'features.torrc.maxLineWrap':
    return max(1, value)


CONFIG = stem.util.conf.config_dict('nyx', {
  'features.colorOverride': 'None',
  'features.colorInterface': True,
  'features.maxLineWrap': 8,
}, conf_handler)


def start(function, transparent_background = False, cursor = True):
  """
  Starts a curses interface, delegating to the given function.

  :param funtion: function to invoke when curses starts
  :param bool transparent_background: allows background transparency
  :param bool cursor: makes cursor visible
  """

  def _wrapper(stdscr):
    global CURSES_SCREEN

    CURSES_SCREEN = stdscr

    if transparent_background:
      try:
        curses.use_default_colors()
      except curses.error:
        pass

    if not cursor:
      try:
        curses.curs_set(0)
      except curses.error:
        pass

    function()

  curses.wrapper(_wrapper)


def raw_screen():
  """
  Provides the curses screen. This can only be called after
  :func:`~nyx.curses.start`, and is used as follows...

  ::

    with nyx.curses.raw_screen() as stdscr:
      ... work with curses...

  In the future this will never be called directly. This is just an
  intermediate function as we migrate.
  """

  class _Wrapper(object):
    def __enter__(self):
      # TODO: We should be wrapping this with CURSES_LOCK.acquire/release(),
      # but doing so seems to be causing frequent terminal gliches when
      # shutting down. Strange since this should be strictly safer. Oh well -
      # something to dig into later.

      return CURSES_SCREEN

    def __exit__(self, exit_type, value, traceback):
      pass

  return _Wrapper()


def key_input(input_timeout = None):
  """
  Gets a key press from the user.

  :param int input_timeout: duration in seconds to wait for user input

  :returns: :class:`~nyx.curses.KeyInput` that was pressed
  """

  if input_timeout:
    # Timeout can't be longer than 25.5 seconds...
    # https://docs.python.org/2/library/curses.html?#curses.halfdelay

    curses.halfdelay(min(input_timeout * 10, 255))
  else:
    curses.cbreak()  # wait indefinitely for key presses (no timeout)

  return KeyInput(CURSES_SCREEN.getch())


def str_input(x, y, initial_text = ''):
  """
  Provides a text field where the user can input a string, blocking until
  they've done so and returning the result. If the user presses escape then
  this terminates and provides back **None**.

  This blanks any content within the space that the input field is rendered
  (otherwise stray characters would be interpreted as part of the initial
  input).

  :param int x: horizontal location
  :param int y: vertical location
  :param str initial_text: initial input of the field

  :returns: **str** with the user input or **None** if the prompt is caneled
  """

  def handle_key(textbox, key):
    y, x = textbox.win.getyx()

    if key == 27:
      return curses.ascii.BEL  # user pressed esc
    elif key == curses.KEY_HOME:
      textbox.win.move(y, 0)
    elif key in (curses.KEY_END, curses.KEY_RIGHT):
      msg_length = len(textbox.gather())
      textbox.win.move(y, x)  # reverts cursor movement during gather call

      if key == curses.KEY_END and msg_length > 0 and x < msg_length - 1:
        textbox.win.move(y, msg_length - 1)  # if we're in the content then move to the end
      elif key == curses.KEY_RIGHT and x < msg_length - 1:
        textbox.win.move(y, x + 1)  # only move cursor if there's content after it
    elif key == 410:
      # if we're resizing the display during text entry then cancel it
      # (otherwise the input field is filled with nonprintable characters)

      return curses.ascii.BEL
    else:
      return key

  with CURSES_LOCK:
    try:
      curses.curs_set(1)  # show cursor
    except curses.error:
      pass

    width = screen_size().width - x

    curses_subwindow = CURSES_SCREEN.subwin(1, width, y, x)
    curses_subwindow.erase()
    curses_subwindow.addstr(0, 0, initial_text[:width - 1])

    textbox = curses.textpad.Textbox(curses_subwindow, insert_mode = True)
    user_input = textbox.edit(lambda key: handle_key(textbox, key)).strip()

    try:
      curses.curs_set(0)  # hide cursor
    except curses.error:
      pass

    return None if textbox.lastcmd == curses.ascii.BEL else user_input


def curses_attr(*attributes):
  """
  Provides encoding for the given curses text attributes.

  :param list attributes: curses text attributes and colors

  :returns: **int** that can be used with curses
  """

  encoded = curses.A_NORMAL

  for attr in attributes:
    if attr in Color:
      override = get_color_override()
      encoded |= _color_attr()[override if override else attr]
    elif attr in Attr:
      encoded |= CURSES_ATTRIBUTES[attr]
    else:
      raise ValueError("'%s' isn't a valid curses text attribute" % attr)

  return encoded


def screen_size():
  """
  Provides the current dimensions of our screen.

  :returns: :data:`~nyx.curses.Dimensions` with our screen size
  """

  height, width = CURSES_SCREEN.getmaxyx()
  return Dimensions(width, height)


def screenshot():
  """
  Provides a dump of the present content of the screen.

  :returns: **str** with the present content shown on the screen
  """

  lines = []

  for y in range(screen_size().height):
    lines.append(CURSES_SCREEN.instr(y, 0).rstrip())

  return '\n'.join(lines).rstrip()


def is_color_supported():
  """
  Checks if curses currently supports rendering colors.

  :returns: **True** if colors can be rendered, **False** otherwise
  """

  return _color_attr() != DEFAULT_COLOR_ATTR


def get_color_override():
  """
  Provides the override color used by the interface.

  :returns: :data:`~nyx.curses.Color` for the color requrests will be
    overwritten with, **None** if no override is set
  """

  color_override = CONFIG.get('features.colorOverride', 'None')
  return None if color_override == 'None' else color_override


def set_color_override(color = None):
  """
  Overwrites all requests for color with the given color instead.

  :param nyx.curses.Color color: color to override all requests with, **None**
    if color requests shouldn't be overwritten

  :raises: **ValueError** if the color name is invalid
  """

  nyx_config = stem.util.conf.get_config('nyx')

  if color is None:
    nyx_config.set('features.colorOverride', 'None')
  elif color in Color:
    nyx_config.set('features.colorOverride', color)
  else:
    raise ValueError(msg('usage.unable_to_set_color_override', color = color))


def _color_attr():
  """
  Initializes color mappings usable by curses. This can only be done after
  calling curses.initscr().
  """

  global COLOR_ATTR

  if COLOR_ATTR is None:
    if not CONFIG['features.colorInterface']:
      COLOR_ATTR = DEFAULT_COLOR_ATTR
    elif curses.has_colors():
      color_attr = dict(DEFAULT_COLOR_ATTR)

      for color_pair, color_name in enumerate(CURSES_COLORS):
        foreground_color = CURSES_COLORS[color_name]
        background_color = -1  # allows for default (possibly transparent) background
        curses.init_pair(color_pair + 1, foreground_color, background_color)
        color_attr[color_name] = curses.color_pair(color_pair + 1)

      log.info('setup.color_support_available')
      COLOR_ATTR = color_attr
    else:
      log.info('setup.color_support_unavailable')
      COLOR_ATTR = DEFAULT_COLOR_ATTR

  return COLOR_ATTR


def disable_acs():
  """
  Replaces ACS characters used for showing borders. This can be preferable if
  curses is `unable to render them
  <https://www.atagar.com/arm/images/acs_display_failure.png>`_.
  """

  for item in curses.__dict__:
    if item.startswith('ACS_'):
      curses.__dict__[item] = ord('+')

  # replace common border pipe cahracters

  curses.ACS_SBSB = ord('|')
  curses.ACS_VLINE = ord('|')
  curses.ACS_BSBS = ord('-')
  curses.ACS_HLINE = ord('-')


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

    if stem.util.system.is_available('ldd'):
      return 'libncursesw' in '\n'.join(lib_dependency_lines = stem.util.system.call('ldd %s' % _curses.__file__))
    elif stem.util.system.is_available('otool'):
      return 'libncursesw' in '\n'.join(lib_dependency_lines = stem.util.system.call('otool -L %s' % _curses.__file__))
  except:
    pass

  return False


def draw(func, left = 0, top = 0, width = None, height = None, background = None):
  """
  Renders a subwindow. This calls the given draw function with a
  :class:`~nyx.curses._Subwindow`.

  :param function func: draw function for rendering the subwindow
  :param int left: left position of the panel
  :param int top: top position of the panel
  :param int width: panel width, uses all available space if **None**
  :param int height: panel height, uses all available space if **None**
  :param nyx.curses.Color background: background color, unset if **None**
  """

  with CURSES_LOCK:
    dimensions = screen_size()
    subwindow_width = max(0, dimensions.width - left)
    subwindow_height = max(0, dimensions.height - top)

    if width:
      subwindow_width = min(width, subwindow_width)

    if height:
      subwindow_height = min(height, subwindow_height)

    curses_subwindow = CURSES_SCREEN.subwin(subwindow_height, subwindow_width, top, left)
    curses_subwindow.erase()

    if background:
      curses_subwindow.bkgd(' ', curses_attr(background, HIGHLIGHT))

    func(_Subwindow(subwindow_width, subwindow_height, curses_subwindow))
    curses_subwindow.refresh()


class _Subwindow(object):
  """
  Subwindow that can be drawn within.

  :var int width: subwindow width
  :var int height: subwindow height
  """

  def __init__(self, width, height, curses_subwindow):
    self.width = width
    self.height = height
    self._curses_subwindow = curses_subwindow

  def addstr(self, x, y, msg, *attr):
    """
    Draws a string in the subwindow.

    :param int x: horizontal location
    :param int y: vertical location
    :param str msg: string to be written
    :param list attr: text attributes to apply

    :returns: **int** with the horizontal position we drew to
    """

    if self.width > x and self.height > y:
      try:
        cropped_msg = msg[:self.width - x]
        self._curses_subwindow.addstr(y, x, cropped_msg, curses_attr(*attr))
        return x + len(cropped_msg)
      except:
        pass

    return x

  def addstr_wrap(self, x, y, msg, width, min_x = 0, *attr):
    """
    Draws a string in the subwindow, with text wrapped if it exceeds a width.

    :param int x: horizontal location
    :param int y: vertical location
    :param str msg: string to be written
    :param int width: width avaialble to render the string
    :param int min_x: horizontal position to wrap to on new lines
    :param list attr: text attributes to apply

    :returns: **tuple** of the (x, y) position we drew to
    """

    orig_y = y

    while msg:
      draw_msg, msg = stem.util.str_tools.crop(msg, width - x, None, ending = None, get_remainder = True)

      if not draw_msg:
        draw_msg, msg = stem.util.str_tools.crop(msg, width - x), ''  # first word is longer than the line

      x = self.addstr(x, y, draw_msg, *attr)

      if (y - orig_y + 1) >= CONFIG['features.maxLineWrap']:
        break  # maximum number we'll wrap

      if msg:
        x, y = min_x, y + 1

    return x, y

  def box(self, left = 0, top = 0, width = None, height = None, *attr):
    """
    Draws a box with the given bounds.

    :param int left: left position of the box
    :param int top: top position of the box
    :param int width: box width, uses all available space if **None**
    :param int height: box height, uses all available space if **None**
    :param list attr: text attributes to apply
    """

    max_width = self.width - left
    max_height = self.height - top

    width = max_width if width is None else min(width, max_width)
    height = max_height if height is None else min(height, max_height)

    self.hline(left + 1, top, width - 2, *attr)  # top
    self.hline(left + 1, top + height - 1, width - 2, *attr)  # bottom
    self.vline(left, top + 1, height - 2, *attr)  # left
    self.vline(left + width - 1, top + 1, height - 2, *attr)  # right

    self._addch(left, top, curses.ACS_ULCORNER, *attr)  # upper left corner
    self._addch(left, top + height - 1, curses.ACS_LLCORNER, *attr)  # lower left corner
    self._addch(left + width - 1, top, curses.ACS_URCORNER, *attr)  # upper right corner
    self._addch(left + width - 1, top + height - 1, curses.ACS_LRCORNER, *attr)  # lower right corner

  def _addch(self, x, y, char, *attr):
    if self.width > x and self.height > y:
      try:
        self._curses_subwindow.addch(y, x, char, curses_attr(*attr))
        return x + 1
      except:
        pass

    return x

  def hline(self, x, y, length, *attr):
    if self.width > x and self.height > y:
      try:
        self._curses_subwindow.hline(y, x, curses.ACS_HLINE | curses_attr(*attr), min(length, self.width - x))
      except:
        pass

  def vline(self, x, y, length, *attr):
    if self.width > x and self.height > y:
      try:
        self._curses_subwindow.vline(y, x, curses.ACS_VLINE | curses_attr(*attr), min(length, self.height - y))
      except:
        pass


class KeyInput(object):
  """
  Keyboard input by the user.
  """

  def __init__(self, key):
    self._key = key  # pressed key as an integer

  def match(self, *keys):
    """
    Checks if we have a case insensitive match with the given key. Beside
    characters, this also recognizes: up, down, left, right, home, end,
    page_up, page_down, and esc.
    """

    for key in keys:
      if key in SPECIAL_KEYS:
        if self._key == SPECIAL_KEYS[key]:
          return True
      elif len(key) == 1:
        if self._key in (ord(key.lower()), ord(key.upper())):
          return True
      else:
        raise ValueError("%s wasn't among our recognized key codes" % key)

    return False

  def is_scroll(self):
    """
    True if the key is used for scrolling, false otherwise.
    """

    return self._key in SCROLL_KEYS

  def is_selection(self):
    """
    True if the key matches the enter or space keys.
    """

    return self._key in (curses.KEY_ENTER, 10, ord(' '))

  def __eq__(self, other):
    if isinstance(other, KeyInput):
      return self._key == other._key
    else:
      return False

  def __ne__(self, other):
    return not self == other


class Scroller(object):
  """
  Simple scroller that provides keyboard navigation of content.
  """

  def __init__(self):
    self._location = 0

  def location(self, content_height = None, page_height = None):
    """
    Provides the position we've scrolled to.

    If a **content_height** and **page_height** are provided this ensures our
    scroll position falls within a valid range. This should be done when the
    content changes or panel resized.

    :param int content_height: height of the content being renered
    :param int page_height: height visible on the page

    :returns: **int** position we've scrolled to
    """

    if content_height is not None and page_height is not None:
      self._location = max(0, min(self._location, content_height - page_height))

    return self._location

  def handle_key(self, key, content_height, page_height):
    """
    Moves scrolling location according to the given input...

      * up / down - scrolls one position up or down
      * page up / page down - scrolls by the page_height
      * home / end - moves to the top or bottom

    :param nyx.curses.KeyInput key: pressed key
    :param int content_height: height of the content being renered
    :param int page_height: height visible on the page

    :returns: **bool** that's **True** if the scrolling position changed and
      **False** otherwise
    """

    new_location = _scroll_position(self._location, key, content_height, page_height, False)

    if new_location != self._location:
      self._location = new_location
      return True
    else:
      return False


class CursorScroller(object):
  """
  Scroller that tracks a cursor's position.
  """

  def __init__(self):
    self._location = 0

    # We track the cursor location by the item we have selected, so it stays
    # selected as the content changes. We also keep track of its last location
    # so we can fall back to that if it disappears.

    self._cursor_location = 0
    self._cursor_selection = None

  def selection(self, content, page_height = None):
    """
    Provides the item from the content that's presently selected. If provided
    the height of our page this provides the scroll position as well...

    ::

      selected, scroll = my_scroller.selection(content, page_height)

    :param list content: content the scroller is tracking
    :param int page_height: height visible on the page

    :returns: **tuple** of the form **(cursor, scroll)**, the cursor is
      **None** if content is empty
    """

    content = list(content)  # shallow copy for thread safety

    if not content:
      self._cursor_location = 0
      self._cursor_selection = None
      return None if page_height is None else None, 0

    if self._cursor_selection in content:
      # moves cursor location to track the selection
      self._cursor_location = content.index(self._cursor_selection)
    else:
      # select the next closest entry
      self._cursor_location = max(0, min(self._cursor_location, len(content) - 1))
      self._cursor_selection = content[self._cursor_location]

    # ensure our cursor is visible

    if page_height:
      if self._cursor_location < self._location:
        self._location = self._cursor_location
      elif self._cursor_location > self._location + page_height - 1:
        self._location = self._cursor_location - page_height + 1

    if page_height is None:
      return self._cursor_selection
    else:
      return self._cursor_selection, self._location

  def handle_key(self, key, content, page_height):
    self.selection(content, page_height)  # reset cursor position
    new_location = _scroll_position(self._cursor_location, key, len(content), page_height, True)

    if new_location != self._cursor_location:
      self._cursor_location = new_location
      self._cursor_selection = content[new_location]

      return True
    else:
      return False


def _scroll_position(location, key, content_height, page_height, is_cursor):
  if key.match('up'):
    shift = -1
  elif key.match('down'):
    shift = 1
  elif key.match('page_up'):
    shift = -page_height + 1 if is_cursor else -page_height
  elif key.match('page_down'):
    shift = page_height - 1 if is_cursor else page_height
  elif key.match('home'):
    shift = -content_height
  elif key.match('end'):
    shift = content_height
  else:
    return location

  max_position = content_height - 1 if is_cursor else content_height - page_height
  return max(0, min(location + shift, max_position))
