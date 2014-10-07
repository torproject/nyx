"""
Wrapper for safely working with curses subwindows.
"""

import copy
import time
import curses
import curses.ascii
import curses.textpad
from threading import RLock

from arm.util import text_input, ui_tools

from stem.util import log

# global ui lock governing all panel instances (curses isn't thread save and
# concurrency bugs produce especially sinister glitches)

CURSES_LOCK = RLock()

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


# tags used by addfstr - this maps to functor/argument combinations since the
# actual values (in the case of color attributes) might not yet be initialized

def _no_op(arg):
  return arg


FORMAT_TAGS = {
  "<b>": (_no_op, curses.A_BOLD),
  "<u>": (_no_op, curses.A_UNDERLINE),
  "<h>": (_no_op, curses.A_STANDOUT),
}

for color_label in ui_tools.COLOR_LIST:
  FORMAT_TAGS["<%s>" % color_label] = (ui_tools.get_color, color_label)

# prevents curses redraws if set
HALT_ACTIVITY = False


class Panel():
  """
  Wrapper for curses subwindows. This hides most of the ugliness in common
  curses operations including:
    - locking when concurrently drawing to multiple windows
    - gracefully handle terminal resizing
    - clip text that falls outside the panel
    - convenience methods for word wrap, in-line formatting, etc

  This uses a design akin to Swing where panel instances provide their display
  implementation by overwriting the draw() method, and are redrawn with
  redraw().
  """

  def __init__(self, parent, name, top, left = 0, height = -1, width = -1):
    """
    Creates a durable wrapper for a curses subwindow in the given parent.

    Arguments:
      parent - parent curses window
      name   - identifier for the panel
      top    - positioning of top within parent
      left   - positioning of the left edge within the parent
      height - maximum height of panel (uses all available space if -1)
      width  - maximum width of panel (uses all available space if -1)
    """

    # The not-so-pythonic getters for these parameters are because some
    # implementations aren't entirely deterministic (for instance panels
    # might chose their height based on its parent's current width).

    self.panel_name = name
    self.parent = parent
    self.visible = False
    self.title_visible = True

    # Attributes for pausing. The pause_attr contains variables our get_attr
    # method is tracking, and the pause buffer has copies of the values from
    # when we were last unpaused (unused unless we're paused).

    self.paused = False
    self.pause_attr = []
    self.pause_buffer = {}
    self.pause_time = -1

    self.top = top
    self.left = left
    self.height = height
    self.width = width

    # The panel's subwindow instance. This is made available to implementors
    # via their draw method and shouldn't be accessed directly.
    #
    # This is None if either the subwindow failed to be created or needs to be
    # remade before it's used. The later could be for a couple reasons:
    # - The subwindow was never initialized.
    # - Any of the parameters used for subwindow initialization have changed.

    self.win = None

    self.max_y, self.max_x = -1, -1  # subwindow dimensions when last redrawn

  def get_name(self):
    """
    Provides panel's identifier.
    """

    return self.panel_name

  def is_title_visible(self):
    """
    True if the title is configured to be visible, False otherwise.
    """

    return self.title_visible

  def set_title_visible(self, is_visible):
    """
    Configures the panel's title to be visible or not when it's next redrawn.
    This is not guarenteed to be respected (not all panels have a title).
    """

    self.title_visible = is_visible

  def get_parent(self):
    """
    Provides the parent used to create subwindows.
    """

    return self.parent

  def is_visible(self):
    """
    Provides if the panel's configured to be visible or not.
    """

    return self.visible

  def set_visible(self, is_visible):
    """
    Toggles if the panel is visible or not.

    Arguments:
      is_visible - panel is redrawn when requested if true, skipped otherwise
    """

    self.visible = is_visible

  def is_paused(self):
    """
    Provides if the panel's configured to be paused or not.
    """

    return self.paused

  def set_pause_attr(self, attr):
    """
    Configures the panel to track the given attribute so that get_attr provides
    the value when it was last unpaused (or its current value if we're
    currently unpaused). For instance...

    > self.set_pause_attr("myVar")
    > self.myVar = 5
    > self.myVar = 6  # self.get_attr("myVar") -> 6
    > self.set_paused(True)
    > self.myVar = 7  # self.get_attr("myVar") -> 6
    > self.set_paused(False)
    > self.myVar = 7  # self.get_attr("myVar") -> 7

    Arguments:
      attr - parameter to be tracked for get_attr
    """

    self.pause_attr.append(attr)
    self.pause_buffer[attr] = self.copy_attr(attr)

  def get_attr(self, attr):
    """
    Provides the value of the given attribute when we were last unpaused. If
    we're currently unpaused then this is the current value. If untracked this
    returns None.

    Arguments:
      attr - local variable to be returned
    """

    if attr not in self.pause_attr:
      return None
    elif self.paused:
      return self.pause_buffer[attr]
    else:
      return self.__dict__.get(attr)

  def copy_attr(self, attr):
    """
    Provides a duplicate of the given configuration value, suitable for the
    pause buffer.

    Arguments:
      attr - parameter to be provided back
    """

    current_value = self.__dict__.get(attr)
    return copy.copy(current_value)

  def set_paused(self, is_pause, suppress_redraw = False):
    """
    Toggles if the panel is paused or not. This causes the panel to be redrawn
    when toggling is pause state unless told to do otherwise. This is
    important when pausing since otherwise the panel's display could change
    when redrawn for other reasons.

    This returns True if the panel's pause state was changed, False otherwise.

    Arguments:
      is_pause        - freezes the state of the pause attributes if true, makes
                        them editable otherwise
      suppress_redraw - if true then this will never redraw the panel
    """

    if is_pause != self.paused:
      if is_pause:
        self.pause_time = time.time()

      self.paused = is_pause

      if is_pause:
        # copies tracked attributes so we know what they were before pausing

        for attr in self.pause_attr:
          self.pause_buffer[attr] = self.copy_attr(attr)

      if not suppress_redraw:
        self.redraw(True)

      return True
    else:
      return False

  def get_pause_time(self):
    """
    Provides the time that we were last paused, returning -1 if we've never
    been paused.
    """

    return self.pause_time

  def get_top(self):
    """
    Provides the position subwindows are placed at within its parent.
    """

    return self.top

  def set_top(self, top):
    """
    Changes the position where subwindows are placed within its parent.

    Arguments:
      top - positioning of top within parent
    """

    if self.top != top:
      self.top = top
      self.win = None

  def get_left(self):
    """
    Provides the left position where this subwindow is placed within its
    parent.
    """

    return self.left

  def set_left(self, left):
    """
    Changes the left position where this subwindow is placed within its parent.

    Arguments:
      left - positioning of top within parent
    """

    if self.left != left:
      self.left = left
      self.win = None

  def get_height(self):
    """
    Provides the height used for subwindows (-1 if it isn't limited).
    """

    return self.height

  def set_height(self, height):
    """
    Changes the height used for subwindows. This uses all available space if -1.

    Arguments:
      height - maximum height of panel (uses all available space if -1)
    """

    if self.height != height:
      self.height = height
      self.win = None

  def get_width(self):
    """
    Provides the width used for subwindows (-1 if it isn't limited).
    """

    return self.width

  def set_width(self, width):
    """
    Changes the width used for subwindows. This uses all available space if -1.

    Arguments:
      width - maximum width of panel (uses all available space if -1)
    """

    if self.width != width:
      self.width = width
      self.win = None

  def get_preferred_size(self):
    """
    Provides the dimensions the subwindow would use when next redrawn, given
    that none of the properties of the panel or parent change before then. This
    returns a tuple of (height, width).
    """

    new_height, new_width = self.parent.getmaxyx()
    set_height, set_width = self.get_height(), self.get_width()
    new_height = max(0, new_height - self.top)
    new_width = max(0, new_width - self.left)

    if set_height != -1:
      new_height = min(new_height, set_height)

    if set_width != -1:
      new_width = min(new_width, set_width)

    return (new_height, new_width)

  def handle_key(self, key):
    """
    Handler for user input. This returns true if the key press was consumed,
    false otherwise.

    Arguments:
      key - keycode for the key pressed
    """

    return False

  def get_help(self):
    """
    Provides help information for the controls this page provides. This is a
    list of tuples of the form...
    (control, description, status)
    """

    return []

  def draw(self, width, height):
    """
    Draws display's content. This is meant to be overwritten by
    implementations and not called directly (use redraw() instead). The
    dimensions provided are the drawable dimensions, which in terms of width is
    a column less than the actual space.

    Arguments:
      width  - horizontal space available for content
      height - vertical space available for content
    """

    pass

  def redraw(self, force_redraw=False, block=False):
    """
    Clears display and redraws its content. This can skip redrawing content if
    able (ie, the subwindow's unchanged), instead just refreshing the display.

    Arguments:
      force_redraw - forces the content to be cleared and redrawn if true
      block       - if drawing concurrently with other panels this determines
                    if the request is willing to wait its turn or should be
                    abandoned
    """

    # skipped if not currently visible or activity has been halted

    if not self.is_visible() or HALT_ACTIVITY:
      return

    # if the panel's completely outside its parent then this is a no-op

    new_height, new_width = self.get_preferred_size()

    if new_height == 0 or new_width == 0:
      self.win = None
      return

    # recreates the subwindow if necessary

    is_new_window = self._reset_subwindow()

    # The reset argument is disregarded in a couple of situations:
    # - The subwindow's been recreated (obviously it then doesn't have the old
    #   content to refresh).
    # - The subwindow's dimensions have changed since last drawn (this will
    #   likely change the content's layout)

    subwin_max_y, subwin_max_x = self.win.getmaxyx()

    if is_new_window or subwin_max_y != self.max_y or subwin_max_x != self.max_x:
      force_redraw = True

    self.max_y, self.max_x = subwin_max_y, subwin_max_x

    if not CURSES_LOCK.acquire(block):
      return

    try:
      if force_redraw:
        self.win.erase()  # clears any old contents
        self.draw(self.max_x, self.max_y)
      self.win.refresh()
    finally:
      CURSES_LOCK.release()

  def hline(self, y, x, length, attr=curses.A_NORMAL):
    """
    Draws a horizontal line. This should only be called from the context of a
    panel's draw method.

    Arguments:
      y      - vertical location
      x      - horizontal location
      length - length the line spans
      attr   - text attributes
    """

    if self.win and self.max_x > x and self.max_y > y:
      try:
        draw_length = min(length, self.max_x - x)
        self.win.hline(y, x, curses.ACS_HLINE | attr, draw_length)
      except:
        # in edge cases drawing could cause a _curses.error
        pass

  def vline(self, y, x, length, attr=curses.A_NORMAL):
    """
    Draws a vertical line. This should only be called from the context of a
    panel's draw method.

    Arguments:
      y      - vertical location
      x      - horizontal location
      length - length the line spans
      attr   - text attributes
    """

    if self.win and self.max_x > x and self.max_y > y:
      try:
        draw_length = min(length, self.max_y - y)
        self.win.vline(y, x, curses.ACS_VLINE | attr, draw_length)
      except:
        # in edge cases drawing could cause a _curses.error
        pass

  def addch(self, y, x, char, attr=curses.A_NORMAL):
    """
    Draws a single character. This should only be called from the context of a
    panel's draw method.

    Arguments:
      y    - vertical location
      x    - horizontal location
      char - character to be drawn
      attr - text attributes
    """

    if self.win and self.max_x > x and self.max_y > y:
      try:
        self.win.addch(y, x, char, attr)
      except:
        # in edge cases drawing could cause a _curses.error
        pass

  def addstr(self, y, x, msg, *attributes):
    """
    Writes string to subwindow if able. This takes into account screen bounds
    to avoid making curses upset. This should only be called from the context
    of a panel's draw method.

    Arguments:
      y    - vertical location
      x    - horizontal location
      msg  - text to be added
      attr - text attributes
    """

    format_attr = curses.A_NORMAL

    for attr in attributes:
      if isinstance(attr, str):
        format_attr |= ui_tools.get_color(attr)
      else:
        format_attr |= attr

    # subwindows need a single character buffer (either in the x or y
    # direction) from actual content to prevent crash when shrank

    if self.win and self.max_x > x and self.max_y > y:
      try:
        drawn_msg = msg[:self.max_x - x]
        self.win.addstr(y, x, drawn_msg, format_attr)
        return x + len(drawn_msg)
      except:
        # this might produce a _curses.error during edge cases, for instance
        # when resizing with visible popups

        pass

    return x

  def addfstr(self, y, x, msg):
    """
    Writes string to subwindow. The message can contain xhtml-style tags for
    formatting, including:
    <b>text</b>               bold
    <u>text</u>               underline
    <h>text</h>               highlight
    <[color]>text</[color]>   use color (see ui_tools.get_color() for constants)

    Tag nesting is supported and tag closing is strictly enforced (raising an
    exception for invalid formatting). Unrecognized tags are treated as normal
    text. This should only be called from the context of a panel's draw method.

    Text in multiple color tags (for instance "<blue><red>hello</red></blue>")
    uses the bitwise OR of those flags (hint: that's probably not what you
    want).

    Arguments:
      y    - vertical location
      x    - horizontal location
      msg  - formatted text to be added
    """

    if self.win and self.max_y > y:
      formatting = [curses.A_NORMAL]
      expected_close_tags = []
      unused_msg = msg

      while self.max_x > x and len(unused_msg) > 0:
        # finds next consumeable tag (left as None if there aren't any left)

        next_tag, tag_start, tag_end = None, -1, -1

        tmp_checked = 0  # portion of the message cleared for having any valid tags
        expected_tags = FORMAT_TAGS.keys() + expected_close_tags

        while next_tag is None:
          tag_start = unused_msg.find("<", tmp_checked)
          tag_end = unused_msg.find(">", tag_start) + 1 if tag_start != -1 else -1

          if tag_start == -1 or tag_end == -1:
            break  # no more tags to consume
          else:
            # check if the tag we've found matches anything being expected
            if unused_msg[tag_start:tag_end] in expected_tags:
              next_tag = unused_msg[tag_start:tag_end]
              break  # found a tag to use
            else:
              # not a valid tag - narrow search to everything after it
              tmp_checked = tag_end

        # splits into text before and after tag

        if next_tag:
          msg_segment = unused_msg[:tag_start]
          unused_msg = unused_msg[tag_end:]
        else:
          msg_segment = unused_msg
          unused_msg = ""

        # adds text before tag with current formatting

        attr = 0

        for text_format in formatting:
          attr |= text_format

        self.win.addstr(y, x, msg_segment[:self.max_x - x - 1], attr)
        x += len(msg_segment)

        # applies tag attributes for future text

        if next_tag:
          format_tag = "<" + next_tag[2:] if next_tag.startswith("</") else next_tag
          format_match = FORMAT_TAGS[format_tag][0](FORMAT_TAGS[format_tag][1])

          if not next_tag.startswith("</"):
            # open tag - add formatting
            expected_close_tags.append("</" + next_tag[1:])
            formatting.append(format_match)
          else:
            # close tag - remove formatting
            expected_close_tags.remove(next_tag)
            formatting.remove(format_match)

      # only check for unclosed tags if we processed the whole message (if we
      # stopped processing prematurely it might still be valid)

      if expected_close_tags and not unused_msg:
        # if we're done then raise an exception for any unclosed tags (tisk, tisk)
        base_msg = "Unclosed formatting tag%s:" % ("s" if len(expected_close_tags) > 1 else "")
        raise ValueError("%s: '%s'\n  \"%s\"" % (base_msg, "', '".join(expected_close_tags), msg))

  def getstr(self, y, x, initial_text = "", text_format = None, max_width = None, validator = None):
    """
    Provides a text field where the user can input a string, blocking until
    they've done so and returning the result. If the user presses escape then
    this terminates and provides back None. This should only be called from
    the context of a panel's draw method.

    This blanks any content within the space that the input field is rendered
    (otherwise stray characters would be interpreted as part of the initial
    input).

    Arguments:
      y            - vertical location
      x            - horizontal location
      initial_text - starting text in this field
      text_format  - format used for the text
      max_width    - maximum width for the text field
      validator    - custom TextInputValidator for handling keybindings
    """

    if not text_format:
      text_format = curses.A_NORMAL

    # makes cursor visible

    try:
      previous_cursor_state = curses.curs_set(1)
    except curses.error:
      previous_cursor_state = 0

    # temporary subwindow for user input

    display_width = self.get_preferred_size()[1]

    if max_width:
      display_width = min(display_width, max_width + x)

    input_subwindow = self.parent.subwin(1, display_width - x, self.top + y, self.left + x)

    # blanks the field's area, filling it with the font in case it's hilighting

    input_subwindow.clear()
    input_subwindow.bkgd(' ', text_format)

    # prepopulates the initial text

    if initial_text:
      input_subwindow.addstr(0, 0, initial_text[:display_width - x - 1], text_format)

    # Displays the text field, blocking until the user's done. This closes the
    # text panel and returns user_input to the initial text if the user presses
    # escape.

    textbox = curses.textpad.Textbox(input_subwindow)

    if not validator:
      validator = text_input.BasicValidator()

    textbox.win.attron(text_format)
    user_input = textbox.edit(lambda key: validator.validate(key, textbox)).strip()
    textbox.win.attroff(text_format)

    if textbox.lastcmd == curses.ascii.BEL:
      user_input = None

    # reverts visability settings

    try:
      curses.curs_set(previous_cursor_state)
    except curses.error:
      pass

    return user_input

  def add_scroll_bar(self, top, bottom, size, draw_top = 0, draw_bottom = -1, draw_left = 0):
    """
    Draws a left justified scroll bar reflecting position within a vertical
    listing. This is shorted if necessary, and left undrawn if no space is
    available. The bottom is squared off, having a layout like:
     |
    *|
    *|
    *|
     |
    -+

    This should only be called from the context of a panel's draw method.

    Arguments:
      top        - list index for the top-most visible element
      bottom     - list index for the bottom-most visible element
      size       - size of the list in which the listed elements are contained
      draw_top    - starting row where the scroll bar should be drawn
      draw_bottom - ending row where the scroll bar should end, -1 if it should
                   span to the bottom of the panel
      draw_left   - left offset at which to draw the scroll bar
    """

    if (self.max_y - draw_top) < 2:
      return  # not enough room

    # sets draw_bottom to be the actual row on which the scrollbar should end

    if draw_bottom == -1:
      draw_bottom = self.max_y - 1
    else:
      draw_bottom = min(draw_bottom, self.max_y - 1)

    # determines scrollbar dimensions

    scrollbar_height = draw_bottom - draw_top
    slider_top = scrollbar_height * top / size
    slider_size = scrollbar_height * (bottom - top) / size

    # ensures slider isn't at top or bottom unless really at those extreme bounds

    if top > 0:
      slider_top = max(slider_top, 1)

    if bottom != size:
      slider_top = min(slider_top, scrollbar_height - slider_size - 2)

    # avoids a rounding error that causes the scrollbar to be too low when at
    # the bottom

    if bottom == size:
      slider_top = scrollbar_height - slider_size - 1

    # draws scrollbar slider

    for i in range(scrollbar_height):
      if i >= slider_top and i <= slider_top + slider_size:
        self.addstr(i + draw_top, draw_left, " ", curses.A_STANDOUT)
      else:
        self.addstr(i + draw_top, draw_left, " ")

    # draws box around the scroll bar

    self.vline(draw_top, draw_left + 1, draw_bottom - 1)
    self.addch(draw_bottom, draw_left + 1, curses.ACS_LRCORNER)
    self.addch(draw_bottom, draw_left, curses.ACS_HLINE)

  def _reset_subwindow(self):
    """
    Create a new subwindow instance for the panel if:
    - Panel currently doesn't have a subwindow (was uninitialized or
      invalidated).
    - There's room for the panel to grow vertically (curses automatically
      lets subwindows regrow horizontally, but not vertically).
    - The subwindow has been displaced. This is a curses display bug that
      manifests if the terminal's shrank then re-expanded. Displaced
      subwindows are never restored to their proper position, resulting in
      graphical glitches if we draw to them.
    - The preferred size is smaller than the actual size (should shrink).

    This returns True if a new subwindow instance was created, False otherwise.
    """

    new_height, new_width = self.get_preferred_size()

    if new_height == 0:
      return False  # subwindow would be outside its parent

    # determines if a new subwindow should be recreated

    recreate = self.win is None

    if self.win:
      subwin_max_y, subwin_max_x = self.win.getmaxyx()
      recreate |= subwin_max_y < new_height           # check for vertical growth
      recreate |= self.top > self.win.getparyx()[0]   # check for displacement
      recreate |= subwin_max_x > new_width or subwin_max_y > new_height  # shrinking

    # I'm not sure if recreating subwindows is some sort of memory leak but the
    # Python curses bindings seem to lack all of the following:
    # - subwindow deletion (to tell curses to free the memory)
    # - subwindow moving/resizing (to restore the displaced windows)
    # so this is the only option (besides removing subwindows entirely which
    # would mean far more complicated code and no more selective refreshing)

    if recreate:
      self.win = self.parent.subwin(new_height, new_width, self.top, self.left)

      # note: doing this log before setting win produces an infinite loop
      log.debug("recreating panel '%s' with the dimensions of %i/%i" % (self.get_name(), new_height, new_width))

    return recreate


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
