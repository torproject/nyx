"""
Toolkit for working with curses.
"""

import sys
import curses

from curses.ascii import isprint

from arm.util import info, msg

from stem.util import conf, enum, system

COLOR_LIST = {
  'red': curses.COLOR_RED,
  'green': curses.COLOR_GREEN,
  'yellow': curses.COLOR_YELLOW,
  'blue': curses.COLOR_BLUE,
  'cyan': curses.COLOR_CYAN,
  'magenta': curses.COLOR_MAGENTA,
  'black': curses.COLOR_BLACK,
  'white': curses.COLOR_WHITE,
}

DEFAULT_COLOR_ATTR = dict([(color, 0) for color in COLOR_LIST])
COLOR_ATTR = None

Ending = enum.Enum('ELLIPSE', 'HYPHEN')
SCROLL_KEYS = (curses.KEY_UP, curses.KEY_DOWN, curses.KEY_PPAGE, curses.KEY_NPAGE, curses.KEY_HOME, curses.KEY_END)


def conf_handler(key, value):
  if key == 'features.color_override':
    if value not in COLOR_LIST.keys() and value != 'none':
      raise ValueError(msg('usage.unable_to_set_color_override', color = value))


CONFIG = conf.config_dict('arm', {
  'features.color_override': 'none',
  'features.colorInterface': True,
}, conf_handler)


def is_color_supported():
  """
  Checks if curses presently supports rendering colors.

  :returns: **True** if colors can be rendered, **False** otherwise
  """

  return _color_attr() != DEFAULT_COLOR_ATTR


def get_color(color):
  """
  Provides attribute corresponding to a given text color. Supported colors
  include:

    * red
    * green
    * yellow
    * blue
    * cyan
    * magenta
    * black
    * white

  If color support isn't available or colors can't be initialized then this uses the
  terminal's default coloring scheme.

  :param str color: color attributes to be provided

  :returns: **tuple** color pair used by curses to render the color
  """

  color_override = get_color_override()

  if color_override:
    color = color_override

  return _color_attr()[color]


def set_color_override(color = None):
  """
  Overwrites all requests for color with the given color instead.

  :param str color: color to override all requests with, **None** if color
    requests shouldn't be overwritten

  :raises: **ValueError** if the color name is invalid
  """

  arm_config = conf.get_config('arm')

  if color is None:
    arm_config.set('features.color_override', 'none')
  elif color in COLOR_LIST.keys():
    arm_config.set('features.color_override', color)
  else:
    raise ValueError(msg('usage.unable_to_set_color_override', color = color))


def get_color_override():
  """
  Provides the override color used by the interface.

  :returns: **str** for the color requrests will be overwritten with, **None**
    if no override is set
  """

  color_override = CONFIG.get('features.color_override', 'none')

  if color_override == 'none':
    return None
  else:
    return color_override


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
      color_attr = dict([(color, 0) for color in COLOR_LIST])

      for color_pair, color_name in enumerate(COLOR_LIST):
        foreground_color = COLOR_LIST[color_name]
        background_color = -1  # allows for default (possibly transparent) background
        curses.init_pair(color_pair + 1, foreground_color, background_color)
        color_attr[color_name] = curses.color_pair(color_pair + 1)

      info('setup.color_support_available')
      COLOR_ATTR = color_attr
    else:
      info('setup.color_support_unavailable')
      COLOR_ATTR = DEFAULT_COLOR_ATTR

  return COLOR_ATTR


def disable_acs():
  """
  Replaces the curses ACS characters. This can be preferable if curses is
  unable to render them...

  http://www.atagar.com/arm/images/acs_display_failure.png
  """

  for item in curses.__dict__:
    if item.startswith('ACS_'):
      curses.__dict__[item] = ord('+')

  # replace a few common border pipes that are better rendered as '|' or
  # '-' instead

  curses.ACS_SBSB = ord('|')
  curses.ACS_VLINE = ord('|')
  curses.ACS_BSBS = ord('-')
  curses.ACS_HLINE = ord('-')


def get_printable(line, keep_newlines = True):
  """
  Provides the line back with non-printable characters stripped.

  :param str line: string to be processed
  :param str keep_newlines: retains newlines if **True**, stripped otherwise

  :returns: **str** of the line with only printable content
  """

  line = line.replace('\xc2', "'")
  line = filter(lambda char: isprint(char) or (keep_newlines and char == '\n'), line)

  return line


def crop_str(msg, size, min_word_length = 4, min_crop = 0, end_type = Ending.ELLIPSE, get_remainder = False):
  """
  Provides the msg constrained to the given length, truncating on word breaks.
  If the last words is long this truncates mid-word with an ellipse. If there
  isn't room for even a truncated single word (or one word plus the ellipse if
  including those) then this provides an empty string. If a cropped string ends
  with a comma or period then it's stripped (unless we're providing the
  remainder back). For example...

    >>> crop_str("This is a looooong message", 17)
    "This is a looo..."

    >>> crop_str("This is a looooong message", 12)
    "This is a..."

    >>> crop_str("This is a looooong message", 3)
    ""

  :param str msg: text to be processed
  :param int size: space available for text
  :param int min_word_length: minimum characters before which a word is
    dropped, requires whole word if **None**
  :param int min_crop: minimum characters that must be dropped if a word is
    cropped
  :param Ending end_type: type of ending used when truncating, no special
    truncation is used if **None**
  :param bool get_remainder: returns a tuple with the second part being the
    cropped portion of the message

  :returns: **str** of the text truncated to the given length
  """

  # checks if there's room for the whole message

  if len(msg) <= size:
    if get_remainder:
      return (msg, "")
    else:
      return msg

  # avoids negative input

  size = max(0, size)

  if min_word_length is not None:
    min_word_length = max(0, min_word_length)

  min_crop = max(0, min_crop)

  # since we're cropping, the effective space available is less with an
  # ellipse, and cropping words requires an extra space for hyphens

  if end_type == Ending.ELLIPSE:
    size -= 3
  elif end_type == Ending.HYPHEN and min_word_length is not None:
    min_word_length += 1

  # checks if there isn't the minimum space needed to include anything

  last_wordbreak = msg.rfind(" ", 0, size + 1)

  if last_wordbreak == -1:
    # we're splitting the first word

    if min_word_length is None or size < min_word_length:
      if get_remainder:
        return ("", msg)
      else:
        return ""

    include_crop = True
  else:
    last_wordbreak = len(msg[:last_wordbreak].rstrip())  # drops extra ending whitespaces

    if (min_word_length is not None and size < min_word_length) or (min_word_length is None and last_wordbreak < 1):
      if get_remainder:
        return ("", msg)
      else:
        return ""

    if min_word_length is None:
      min_word_length = sys.maxint

    include_crop = size - last_wordbreak - 1 >= min_word_length

  # if there's a max crop size then make sure we're cropping at least that many characters

  if include_crop and min_crop:
    next_wordbreak = msg.find(" ", size)

    if next_wordbreak == -1:
      next_wordbreak = len(msg)

    include_crop = next_wordbreak - size + 1 >= min_crop

  if include_crop:
    return_msg, remainder = msg[:size], msg[size:]

    if end_type == Ending.HYPHEN:
      remainder = return_msg[-1] + remainder
      return_msg = return_msg[:-1].rstrip() + "-"
  else:
    return_msg, remainder = msg[:last_wordbreak], msg[last_wordbreak:]

  # if this is ending with a comma or period then strip it off

  if not get_remainder and return_msg and return_msg[-1] in (",", "."):
    return_msg = return_msg[:-1]

  if end_type == Ending.ELLIPSE:
    return_msg = return_msg.rstrip() + "..."

  if get_remainder:
    return (return_msg, remainder)
  else:
    return return_msg


def pad_str(msg, size, crop_extra = False):
  """
  Provides the string padded with whitespace to the given length.

  Arguments:
    msg       - string to be padded
    size      - length to be padded to
    crop_extra - crops string if it's longer than the size if true
  """

  if crop_extra:
    msg = msg[:size]

  return ("%%-%is" % size) % msg


def draw_box(panel, top, left, width, height, attr=curses.A_NORMAL):
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


def is_selection_key(key):
  """
  Returns true if the keycode matches the enter or space keys.

  Argument:
    key - keycode to be checked
  """

  return key in (curses.KEY_ENTER, 10, ord(' '))


def is_scroll_key(key):
  """
  Returns true if the keycode is recognized by the get_scroll_position function
  for scrolling.

  Argument:
    key - keycode to be checked
  """

  return key in SCROLL_KEYS


def get_scroll_position(key, position, page_height, content_height, is_cursor = False):
  """
  Parses navigation keys, providing the new scroll possition the panel should
  use. Position is always between zero and (content_height - page_height). This
  handles the following keys:
  Up / Down - scrolls a position up or down
  Page Up / Page Down - scrolls by the page_height
  Home - top of the content
  End - bottom of the content

  This provides the input position if the key doesn't correspond to the above.

  Arguments:
    key           - keycode for the user's input
    position      - starting position
    page_height    - size of a single screen's worth of content
    content_height - total lines of content that can be scrolled
    is_cursor      - tracks a cursor position rather than scroll if true
  """

  if is_scroll_key(key):
    shift = 0

    if key == curses.KEY_UP:
      shift = -1
    elif key == curses.KEY_DOWN:
      shift = 1
    elif key == curses.KEY_PPAGE:
      shift = -page_height + 1 if is_cursor else -page_height
    elif key == curses.KEY_NPAGE:
      shift = page_height - 1 if is_cursor else page_height
    elif key == curses.KEY_HOME:
      shift = -content_height
    elif key == curses.KEY_END:
      shift = content_height

    # returns the shift, restricted to valid bounds

    max_location = content_height - 1 if is_cursor else content_height - page_height
    return max(0, min(position + shift, max_location))
  else:
    return position


class Scroller:
  """
  Tracks the scrolling position when there might be a visible cursor. This
  expects that there is a single line displayed per an entry in the contents.
  """

  def __init__(self, is_cursor_enabled):
    self.scroll_location, self.cursor_location = 0, 0
    self.cursor_selection = None
    self.is_cursor_enabled = is_cursor_enabled

  def get_scroll_location(self, content, page_height):
    """
    Provides the scrolling location, taking into account its cursor's location
    content size, and page height.

    Arguments:
      content    - displayed content
      page_height - height of the display area for the content
    """

    if content and page_height:
      self.scroll_location = max(0, min(self.scroll_location, len(content) - page_height + 1))

      if self.is_cursor_enabled:
        self.get_cursor_selection(content)  # resets the cursor location

        # makes sure the cursor is visible

        if self.cursor_location < self.scroll_location:
          self.scroll_location = self.cursor_location
        elif self.cursor_location > self.scroll_location + page_height - 1:
          self.scroll_location = self.cursor_location - page_height + 1

      # checks if the bottom would run off the content (this could be the
      # case when the content's size is dynamic and entries are removed)

      if len(content) > page_height:
        self.scroll_location = min(self.scroll_location, len(content) - page_height)

    return self.scroll_location

  def get_cursor_selection(self, content):
    """
    Provides the selected item in the content. This is the same entry until
    the cursor moves or it's no longer available (in which case it moves on to
    the next entry).

    Arguments:
      content - displayed content
    """

    # TODO: needs to handle duplicate entries when using this for the
    # connection panel

    if not self.is_cursor_enabled:
      return None
    elif not content:
      self.cursor_location, self.cursor_selection = 0, None
      return None

    self.cursor_location = min(self.cursor_location, len(content) - 1)

    if self.cursor_selection is not None and self.cursor_selection in content:
      # moves cursor location to track the selection
      self.cursor_location = content.index(self.cursor_selection)
    else:
      # select the next closest entry
      self.cursor_selection = content[self.cursor_location]

    return self.cursor_selection

  def handle_key(self, key, content, page_height):
    """
    Moves either the scroll or cursor according to the given input.

    Arguments:
      key        - key code of user input
      content    - displayed content
      page_height - height of the display area for the content
    """

    if self.is_cursor_enabled:
      self.get_cursor_selection(content)  # resets the cursor location
      start_location = self.cursor_location
    else:
      start_location = self.scroll_location

    new_location = get_scroll_position(key, start_location, page_height, len(content), self.is_cursor_enabled)

    if start_location != new_location:
      if self.is_cursor_enabled:
        self.cursor_selection = content[new_location]
      else:
        self.scroll_location = new_location

      return True
    else:
      return False


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
