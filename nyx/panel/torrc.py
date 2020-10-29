# Copyright 2010-2020, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Panel displays our syntax highlighted torrc.
"""

import functools
import math
import string

import nyx.curses

from nyx.curses import RED, GREEN, YELLOW, CYAN, WHITE, BOLD, HIGHLIGHT
from nyx.menu import MenuItem, Submenu
from nyx import expand_path, panel, tor_controller

from stem import ControllerError
from stem.control import State


def _read_torrc(path):
  contents = []

  with open(path) as torrc_file:
    for line in torrc_file.readlines():
      line = line.replace('\t', '   ').replace('\xc2', "'").rstrip()
      contents.append(''.join(filter(lambda char: char in string.printable, line)))

  return contents


class TorrcPanel(panel.Panel):
  """
  Renders our syntax highlighted torrc within a scrollable area.
  """

  def __init__(self):
    panel.Panel.__init__(self)

    self._scroller = nyx.curses.Scroller()
    self._show_line_numbers = True  # shows left aligned line numbers
    self._show_comments = True  # shows comments and extra whitespace
    self._last_content_height = 0

    self._torrc_location = None
    self._torrc_content = None
    self._torrc_load_error = None

    controller = tor_controller()
    controller.add_status_listener(self._reset_listener)
    self._reset_listener(controller, State.RESET, None)

  def _reset_listener(self, controller, event_type, _):
    """
    Reloads and displays the torrc on tor reload (sighup) events.
    """

    if event_type == State.RESET:
      try:
        self._torrc_location = expand_path(controller.get_info('config-file'))
        self._torrc_content = _read_torrc(self._torrc_location)
      except ControllerError as exc:
        self._torrc_load_error = 'Unable to determine our torrc location: %s' % exc
        self._torrc_location = None
        self._torrc_content = None
      except Exception as exc:
        exc_msg = exc.strerror if (hasattr(exc, 'strerror') and exc.strerror) else str(exc)
        self._torrc_load_error = 'Unable to read our torrc: %s' % exc_msg
        self._torrc_content = None

  def key_handlers(self):
    def _scroll(key):
      page_height = self.get_height() - 1
      is_changed = self._scroller.handle_key(key, self._last_content_height, page_height)

      if is_changed:
        self.redraw()

    def _toggle_comment_stripping():
      self._show_comments = not self._show_comments

    def _toggle_line_numbers():
      self._show_line_numbers = not self._show_line_numbers

    return (
      nyx.panel.KeyHandler('arrows', 'scroll up and down', _scroll, key_func = lambda key: key.is_scroll()),
      nyx.panel.KeyHandler('s', 'comment stripping', _toggle_comment_stripping, 'off' if self._show_comments else 'on'),
      nyx.panel.KeyHandler('l', 'line numbering', _toggle_line_numbers, 'on' if self._show_line_numbers else 'off'),
    )

  def submenu(self):
    """
    Submenu consisting of...

      Reload
      Show / Hide Comments
      Show / Hide Line Numbers
    """

    comments_label, comments_arg = ('Hide Comments', False) if self._show_comments else ('Show Comments', True)
    line_number_label, line_number_arg = ('Hide Line Numbers', False) if self._show_line_numbers else ('Show Line Numbers', True)

    return Submenu('Torrc', [
      MenuItem(comments_label, functools.partial(setattr, self, '_show_comments'), comments_arg),
      MenuItem(line_number_label, functools.partial(setattr, self, '_show_line_numbers'), line_number_arg),
    ])

  def _draw(self, subwindow):
    scroll = self._scroller.location(self._last_content_height, subwindow.height - 1)

    if self._torrc_content is None:
      subwindow.addstr(0, 1, self._torrc_load_error, RED, BOLD)
      new_content_height = 1
    else:
      if not self._show_line_numbers:
        line_number_offset = 0
      elif len(self._torrc_content) == 0:
        line_number_offset = 2
      else:
        line_number_offset = int(math.log10(len(self._torrc_content))) + 2

      scroll_offset = 0

      if self._last_content_height > subwindow.height - 1:
        scroll_offset = 3
        subwindow.scrollbar(1, scroll, self._last_content_height)

      y = 1 - scroll
      is_multiline = False  # true if we're in the middle of a multiline torrc entry

      for line_number, line in enumerate(self._torrc_content):
        if not self._show_comments:
          line = line[:line.find('#')].rstrip() if '#' in line else line

          if not line:
            continue  # skip blank lines

        if '#' in line:
          line, comment = line.split('#', 1)
          comment = '#' + comment
        else:
          comment = ''

        if is_multiline:
          option, argument = '', line  # previous line ended with a '\'
        elif ' ' not in line.strip():
          option, argument = line, ''  # no argument
        else:
          whitespace = ' ' * (len(line) - len(line.lstrip()))
          option, argument = line.lstrip().split(' ', 1)
          option = whitespace + option + ' '

        is_multiline = line.endswith('\\')  # next line's part of a multi-line entry

        if self._show_line_numbers:
          subwindow.addstr(scroll_offset, y, str(line_number + 1).rjust(line_number_offset - 1), YELLOW, BOLD)

        x = line_number_offset + scroll_offset
        min_x = line_number_offset + scroll_offset

        x, y = subwindow.addstr_wrap(x, y, option, subwindow.width, min_x, GREEN, BOLD)
        x, y = subwindow.addstr_wrap(x, y, argument, subwindow.width, min_x, CYAN, BOLD)
        x, y = subwindow.addstr_wrap(x, y, comment, subwindow.width, min_x, WHITE)

        y += 1

      new_content_height = y + scroll - 1

    subwindow.addstr(0, 0, ' ' * subwindow.width)  # clear line
    location = ' (%s)' % self._torrc_location if self._torrc_location else ''
    subwindow.addstr(0, 0, 'Tor Configuration File%s:' % location, HIGHLIGHT)

    if self._last_content_height != new_content_height:
      self._last_content_height = new_content_height
      self.redraw()
