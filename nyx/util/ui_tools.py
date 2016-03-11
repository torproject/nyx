"""
Toolkit for working with curses.
"""

import curses

from curses.ascii import isprint

from stem.util import system


def disable_acs():
  """
  Replaces the curses ACS characters. This can be preferable if curses is
  unable to render them...

  http://www.atagar.com/nyx/images/acs_display_failure.png
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


def draw_box(panel, top, left, width, height, *attributes):
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

  panel.hline(top, left + 1, width - 2, *attributes)
  panel.hline(top + height - 1, left + 1, width - 2, *attributes)

  # draws the left and right sides

  panel.vline(top + 1, left, height - 2, *attributes)
  panel.vline(top + 1, left + width - 1, height - 2, *attributes)

  # draws the corners

  panel.addch(top, left, curses.ACS_ULCORNER, *attributes)
  panel.addch(top, left + width - 1, curses.ACS_URCORNER, *attributes)
  panel.addch(top + height - 1, left, curses.ACS_LLCORNER, *attributes)
  panel.addch(top + height - 1, left + width - 1, curses.ACS_LRCORNER, *attributes)


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

  if key.is_scroll():
    shift = 0

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

    if system.is_available('ldd'):
      lib_dependency_lines = system.call('ldd %s' % _curses.__file__)
    elif system.is_available('otool'):
      lib_dependency_lines = system.call('otool -L %s' % _curses.__file__)

    if lib_dependency_lines:
      for line in lib_dependency_lines:
        if 'libncursesw' in line:
          return True
  except:
    pass

  return False
