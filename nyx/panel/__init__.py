# Copyright 2010-2016, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Panels consisting the nyx interface.
"""

import collections
import inspect
import threading
import time

import nyx.curses

__all__ = [
  'config',
  'connection',
  'graph',
  'header',
  'log',
  'torrc',
]

HALT_ACTIVITY = False  # prevents curses redraws if set


class KeyHandler(collections.namedtuple('Help', ['key', 'description', 'current'])):
  """
  Action that can be taken via a given keybinding.

  :var str key: key the user can press
  :var str description: description of what it does
  :var str current: optional current value

  :param str key: key the user can press
  :param str description: description of what it does
  :param func action: action to be taken, this can optionally take a single
    argument which is the keypress
  :param str current: current value to be displayed
  :param func key_func: custom function to determine if this key was pressed
  """

  def __new__(self, key, description = None, action = None, current = None, key_func = None):
    instance = super(KeyHandler, self).__new__(self, key, description, current)
    instance._action = action
    instance._key_func = key_func
    return instance

  def handle(self, key):
    """
    Triggers action if our key was pressed.

    :param nyx.curses.KeyInput key: keypress to be matched against
    """

    if self._action:
      is_match = self._key_func(key) if self._key_func else key.match(self.key)

      if is_match:
        if inspect.getargspec(self._action).args == ['key']:
          self._action(key)
        else:
          self._action()


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

  def __init__(self):
    """
    Creates a durable wrapper for a curses subwindow in the given parent.

    Arguments:
      top    - positioning of top within parent
      left   - positioning of the left edge within the parent
      height - maximum height of panel (uses all available space if -1)
      width  - maximum width of panel (uses all available space if -1)
    """

    # The not-so-pythonic getters for these parameters are because some
    # implementations aren't entirely deterministic (for instance panels
    # might chose their height based on its parent's current width).

    self.visible = False

    self.paused = False
    self.pause_time = -1

    self.top = 0
    self.left = 0
    self.height = -1
    self.width = -1

    self.max_y, self.max_x = -1, -1  # subwindow dimensions when last redrawn

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
      self.redraw()

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

  def key_handlers(self):
    """
    Provides options this panel supports. This is a tuple of
    :class:`~nyx.panel.KeyHandler` instances.
    """

    return ()

  def draw(self, subwindow):
    """
    Draws display's content. This is meant to be overwritten by
    implementations and not called directly (use redraw() instead). The
    dimensions provided are the drawable dimensions, which in terms of width is
    a column less than the actual space.

    Arguments:
      subwindow - window content is drawn into
    """

    pass

  def redraw(self):
    """
    Clears display and redraws its content. This can skip redrawing content if
    able (ie, the subwindow's unchanged), instead just refreshing the display.
    """

    # skipped if not currently visible or activity has been halted

    if not self.visible or HALT_ACTIVITY:
      return

    height = self.get_height() if self.get_height() != -1 else None
    width = self.get_width() if self.get_width() != -1 else None

    nyx.curses.draw(self.draw, top = self.top, width = width, height = height)


class DaemonPanel(Panel, threading.Thread):
  def __init__(self, update_rate):
    Panel.__init__(self)
    threading.Thread.__init__(self)
    self.setDaemon(True)

    self._pause_condition = threading.Condition()
    self._halt = False  # terminates thread if true
    self._update_rate = update_rate

  def _update(self):
    pass

  def run(self):
    """
    Performs our _update() action at the given rate.
    """

    last_ran = -1

    while not self._halt:
      if self.is_paused() or (time.time() - last_ran) < self._update_rate:
        with self._pause_condition:
          if not self._halt:
            self._pause_condition.wait(0.2)

        continue  # done waiting, try again

      self._update()
      last_ran = time.time()

  def stop(self):
    """
    Halts further resolutions and terminates the thread.
    """

    with self._pause_condition:
      self._halt = True
      self._pause_condition.notifyAll()
