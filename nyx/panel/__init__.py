"""
Panels consisting the nyx interface.
"""

import collections
import time
import curses
import curses.ascii
import curses.textpad

import nyx.curses
import stem.util.log

from nyx.curses import HIGHLIGHT
from stem.util import conf, str_tools

PASS = -1

__all__ = [
  'config',
  'connection',
  'header',
  'log',
  'torrc',
]


def conf_handler(key, value):
  if key == 'features.torrc.maxLineWrap':
    return max(1, value)


CONFIG = conf.config_dict('nyx', {
  'features.maxLineWrap': 8,
}, conf_handler)

HALT_ACTIVITY = False  # prevents curses redraws if set


class Help(collections.namedtuple('Help', ['key', 'description', 'current'])):
  """
  Help information about keybindings the panel handles.

  :var str key: key the user can press
  :var str description: description of what it does
  :var str current: optional current value
  """

  def __new__(self, key, description, current = None):
    return super(Help, self).__new__(self, key, description, current)


class BasicValidator(object):
  """
  Interceptor for keystrokes given to a textbox, doing the following:
  - quits by setting the input to curses.ascii.BEL when escape is pressed
  - stops the cursor at the end of the box's content when pressing the right
    arrow
  - home and end keys move to the start/end of the line
  """

  def validate(self, key, textbox):
    """
    Processes the given key input for the textbox. This may modify the
    textbox's content, cursor position, etc depending on the functionality
    of the validator. This returns the key that the textbox should interpret,
    PASS if this validator doesn't want to take any action.

    Arguments:
      key     - key code input from the user
      textbox - curses Textbox instance the input came from
    """

    result = self.handle_key(key, textbox)
    return key if result == PASS else result

  def handle_key(self, key, textbox):
    y, x = textbox.win.getyx()

    if curses.ascii.isprint(key) and x < textbox.maxx:
      # Shifts the existing text forward so input is an insert method rather
      # than replacement. The curses.textpad accepts an insert mode flag but
      # this has a couple issues...
      # - The flag is only available for Python 2.6+, before that the
      #   constructor only accepted a subwindow argument as per:
      #   https://trac.torproject.org/projects/tor/ticket/2354
      # - The textpad doesn't shift text that has text attributes. This is
      #   because keycodes read by textbox.win.inch() includes formatting,
      #   causing the curses.ascii.isprint() check it does to fail.

      current_input = textbox.gather()
      textbox.win.addstr(y, x + 1, current_input[x:textbox.maxx - 1])
      textbox.win.move(y, x)  # reverts cursor movement during gather call
    elif key == 27:
      # curses.ascii.BEL is a character codes that causes textpad to terminate

      return curses.ascii.BEL
    elif key == curses.KEY_HOME:
      textbox.win.move(y, 0)
      return None
    elif key in (curses.KEY_END, curses.KEY_RIGHT):
      msg_length = len(textbox.gather())
      textbox.win.move(y, x)  # reverts cursor movement during gather call

      if key == curses.KEY_END and msg_length > 0 and x < msg_length - 1:
        # if we're in the content then move to the end

        textbox.win.move(y, msg_length - 1)
        return None
      elif key == curses.KEY_RIGHT and x >= msg_length - 1:
        # don't move the cursor if there's no content after it

        return None
    elif key == 410:
      # if we're resizing the display during text entry then cancel it
      # (otherwise the input field is filled with nonprintable characters)

      return curses.ascii.BEL

    return PASS


class Panel(object):
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

  def __init__(self, name, top = 0, left = 0, height = -1, width = -1):
    """
    Creates a durable wrapper for a curses subwindow in the given parent.

    Arguments:
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
    self.visible = False

    self.paused = False
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

  def set_paused(self, is_pause):
    """
    Toggles if the panel is paused or not. This causes the panel to be redrawn
    when toggling is pause state unless told to do otherwise. This is
    important when pausing since otherwise the panel's display could change
    when redrawn for other reasons.

    Arguments:
      is_pause        - freezes the state of the pause attributes if true, makes
                        them editable otherwise
    """

    if is_pause != self.paused:
      if is_pause:
        self.pause_time = time.time()

      self.paused = is_pause
      self.redraw(True)

  def get_pause_time(self):
    """
    Provides the time that we were last paused, returning -1 if we've never
    been paused.
    """

    return self.pause_time

  def set_top(self, top):
    """
    Changes the position where subwindows are placed within its parent.

    Arguments:
      top - positioning of top within parent
    """

    if self.top != top:
      self.top = top
      self.win = None

  def get_height(self):
    """
    Provides the height used for subwindows (-1 if it isn't limited).
    """

    return self.height

  def get_width(self):
    """
    Provides the width used for subwindows (-1 if it isn't limited).
    """

    return self.width

  def get_preferred_size(self):
    """
    Provides the dimensions the subwindow would use when next redrawn, given
    that none of the properties of the panel or parent change before then. This
    returns a tuple of (height, width).
    """

    with nyx.curses.raw_screen() as stdscr:
      new_height, new_width = stdscr.getmaxyx()

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
    tuple of :class:`~nyx.panel.Help` instances.
    """

    return ()

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

  def redraw(self, force_redraw=False):
    """
    Clears display and redraws its content. This can skip redrawing content if
    able (ie, the subwindow's unchanged), instead just refreshing the display.

    Arguments:
      force_redraw - forces the content to be cleared and redrawn if true
    """

    # skipped if not currently visible or activity has been halted

    if not self.visible or HALT_ACTIVITY:
      return

    # if the panel's completely outside its parent then this is a no-op

    new_height, new_width = self.get_preferred_size()

    if new_height == 0 or new_width == 0:
      self.win = None
      return

    # recreates the subwindow if necessary

    is_new_window = self._reset_subwindow()

    if not self.win:
      return

    # The reset argument is disregarded in a couple of situations:
    # - The subwindow's been recreated (obviously it then doesn't have the old
    #   content to refresh).
    # - The subwindow's dimensions have changed since last drawn (this will
    #   likely change the content's layout)

    subwin_max_y, subwin_max_x = self.win.getmaxyx()

    if is_new_window or subwin_max_y != self.max_y or subwin_max_x != self.max_x:
      force_redraw = True

    self.max_y, self.max_x = subwin_max_y, subwin_max_x

    if not nyx.curses.CURSES_LOCK.acquire(False):
      return

    try:
      if force_redraw:
        self.win.erase()  # clears any old contents
        self.draw(self.max_x, self.max_y)
      self.win.refresh()
    finally:
      nyx.curses.CURSES_LOCK.release()

  def hline(self, y, x, length, *attributes):
    """
    Draws a horizontal line. This should only be called from the context of a
    panel's draw method.

    Arguments:
      y      - vertical location
      x      - horizontal location
      length - length the line spans
      attr   - text attributes
    """

    format_attr = nyx.curses.curses_attr(*attributes)

    if self.win and self.max_x > x and self.max_y > y:
      try:
        draw_length = min(length, self.max_x - x)
        self.win.hline(y, x, curses.ACS_HLINE | format_attr, draw_length)
      except:
        # in edge cases drawing could cause a _curses.error
        pass

  def vline(self, y, x, length, *attributes):
    """
    Draws a vertical line. This should only be called from the context of a
    panel's draw method.

    Arguments:
      y      - vertical location
      x      - horizontal location
      length - length the line spans
      attr   - text attributes
    """

    format_attr = nyx.curses.curses_attr(*attributes)

    if self.win and self.max_x > x and self.max_y > y:
      try:
        draw_length = min(length, self.max_y - y)
        self.win.vline(y, x, curses.ACS_VLINE | format_attr, draw_length)
      except:
        # in edge cases drawing could cause a _curses.error
        pass

  def addch(self, y, x, char, *attributes):
    """
    Draws a single character. This should only be called from the context of a
    panel's draw method.

    Arguments:
      y    - vertical location
      x    - horizontal location
      char - character to be drawn
      attr - text attributes
    """

    format_attr = nyx.curses.curses_attr(*attributes)

    if self.win and self.max_x > x and self.max_y > y:
      try:
        self.win.addch(y, x, char, format_attr)
        return x + 1
      except:
        # in edge cases drawing could cause a _curses.error
        pass

    return x

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

    format_attr = nyx.curses.curses_attr(*attributes)

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

  def addstr_wrap(self, y, x, msg, width, min_x = 0, *attr):
    orig_y = y

    while msg:
      draw_msg, msg = str_tools.crop(msg, width - x, None, ending = None, get_remainder = True)

      if not draw_msg:
        draw_msg, msg = str_tools.crop(msg, width - x), ''  # first word is longer than the line

      x = self.addstr(y, x, draw_msg, *attr)

      if (y - orig_y + 1) >= CONFIG['features.maxLineWrap']:
        break  # maximum number we'll wrap

      if msg:
        x, y = min_x, y + 1

    return x, y

  def getstr(self, y, x, initial_text = ''):
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
    """

    # makes cursor visible

    try:
      previous_cursor_state = curses.curs_set(1)
    except curses.error:
      previous_cursor_state = 0

    # temporary subwindow for user input

    display_width = self.get_preferred_size()[1]

    with nyx.curses.raw_screen() as stdscr:
      input_subwindow = stdscr.subwin(1, display_width - x, self.top + y, self.left + x)

    # blanks the field's area, filling it with the font in case it's hilighting

    input_subwindow.clear()
    input_subwindow.bkgd(' ', curses.A_NORMAL)

    # prepopulates the initial text

    if initial_text:
      input_subwindow.addstr(0, 0, initial_text[:display_width - x - 1], curses.A_NORMAL)

    # Displays the text field, blocking until the user's done. This closes the
    # text panel and returns user_input to the initial text if the user presses
    # escape.

    textbox = curses.textpad.Textbox(input_subwindow)

    validator = BasicValidator()

    textbox.win.attron(curses.A_NORMAL)
    user_input = textbox.edit(lambda key: validator.validate(key, textbox)).strip()
    textbox.win.attroff(curses.A_NORMAL)

    if textbox.lastcmd == curses.ascii.BEL:
      user_input = None

    # reverts visability settings

    try:
      curses.curs_set(previous_cursor_state)
    except curses.error:
      pass

    return user_input

  def add_scroll_bar(self, top, bottom, size, draw_top = 0):
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
    """

    if (self.max_y - draw_top) < 2:
      return  # not enough room

    # determines scrollbar dimensions

    scrollbar_height = self.max_y - draw_top - 1
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
        self.addstr(i + draw_top, 0, ' ', HIGHLIGHT)
      else:
        self.addstr(i + draw_top, 0, ' ')

    # draws box around the scroll bar

    self.vline(draw_top, 1, self.max_y - 2)
    self.addch(self.max_y - 1, 1, curses.ACS_LRCORNER)
    self.addch(self.max_y - 1, 0, curses.ACS_HLINE)

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
      with nyx.curses.raw_screen() as stdscr:
        self.win = stdscr.subwin(new_height, new_width, self.top, self.left)

      # note: doing this log before setting win produces an infinite loop
      stem.util.log.debug("recreating panel '%s' with the dimensions of %i/%i" % (self.get_name(), new_height, new_width))

    return recreate

  def draw_box(self, top = 0, left = 0, width = -1, height = -1, *attributes):
    """
    Draws a box in the panel with the given bounds.

    Arguments:
      top    - vertical position of the box's top
      left   - horizontal position of the box's left side
      width  - width of the drawn box
      height - height of the drawn box
      attr   - text attributes
    """

    if width == -1 or height == -1:
      panel_height, panel_width = self.get_preferred_size()

      if width == -1:
        width = panel_width - left

      if height == -1:
        height = panel_height - top

    # draws the top and bottom

    self.hline(top, left + 1, width - 2, *attributes)
    self.hline(top + height - 1, left + 1, width - 2, *attributes)

    # draws the left and right sides

    self.vline(top + 1, left, height - 2, *attributes)
    self.vline(top + 1, left + width - 1, height - 2, *attributes)

    # draws the corners

    self.addch(top, left, curses.ACS_ULCORNER, *attributes)
    self.addch(top, left + width - 1, curses.ACS_URCORNER, *attributes)
    self.addch(top + height - 1, left, curses.ACS_LLCORNER, *attributes)
    self.addch(top + height - 1, left + width - 1, curses.ACS_LRCORNER, *attributes)
