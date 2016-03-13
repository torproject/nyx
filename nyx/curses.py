"""
Toolkit for working with curses. Curses earns its name, and this abstracts away
its usage providing us more easy to use high level functions. This abstraction
may also allow us to use libraries like `PDCurses <http://pdcurses.sourceforge.net/>`_
if we want Windows support in the future too.

**Module Overview:**

::

  curses_attr - curses encoded text attribute

  is_color_supported - checks if terminal supports color output
  get_color_override - provides color we override requests with
  set_color_override - sets color we override requests with

  disable_acs - renders replacements for ACS characters
  is_wide_characters_supported - checks if curses supports wide character

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

import curses

import stem.util.conf
import stem.util.enum
import stem.util.system

from nyx.util import msg, log

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


def conf_handler(key, value):
  if key == 'features.colorOverride':
    if value not in Color and value != 'None':
      raise ValueError(msg('usage.unable_to_set_color_override', color = value))


CONFIG = stem.util.conf.config_dict('nyx', {
  'features.colorOverride': 'None',
  'features.colorInterface': True,
}, conf_handler)


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

    :param nyx.util.panel.KeyInput key: pressed key
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
