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
  Common parent for interface panels, providing the ability to pause and
  configure dimensions.
  """

  def __init__(self):
    self._visible = False
    self._top = 0

  def set_visible(self, is_visible):
    """
    Toggles if the panel is visible or not.

    Arguments:
      is_visible - panel is redrawn when requested if true, skipped otherwise
    """

    self._visible = is_visible

  def get_top(self):
    """
    Provides the top position used for subwindows.
    """

    return self._top

  def set_top(self, top):
    """
    Changes the position where subwindows are placed within its parent.

    Arguments:
      top - positioning of top within parent
    """

    if self._top != top:
      self._top = top

  def get_height(self):
    """
    Provides the height used by this panel.

    :returns: **int** for the height of the panel or **None** if unlimited
    """

    return None

  def get_preferred_size(self):
    """
    Provides the dimensions the subwindow would use when next redrawn, given
    that none of the properties of the panel or parent change before then. This
    returns a tuple of (height, width).
    """

    with nyx.curses.raw_screen() as stdscr:
      new_height, new_width = stdscr.getmaxyx()

    new_height = max(0, new_height - self._top)
    new_width = max(0, new_width)

    set_height = self.get_height()

    if set_height is not None:
      new_height = min(new_height, set_height)

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

    if not self._visible or HALT_ACTIVITY:
      return

    nyx.curses.draw(self.draw, top = self._top, height = self.get_height())


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

    import nyx.controller

    last_ran = -1
    nyx_controller = nyx.controller.get_controller()

    while not self._halt:
      if nyx_controller.is_paused() or (time.time() - last_ran) < self._update_rate:
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
