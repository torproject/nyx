# Copyright 2010-2016, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Panel displaying the torrc or nyxrc with the validation done against it.
"""

import math
import string

import nyx.curses

from nyx.curses import RED, GREEN, YELLOW, CYAN, WHITE, BOLD, HIGHLIGHT
from nyx import expand_path, msg, panel, tor_controller

from stem import ControllerError
from stem.control import State


class TorrcPanel(panel.Panel):
  """
  Renders the current torrc or nyxrc with syntax highlighting in a scrollable
  area.
  """

  def __init__(self):
    panel.Panel.__init__(self, 'torrc')

    self._scroller = nyx.curses.Scroller()
    self._show_line_numbers = True  # shows left aligned line numbers
    self._show_comments = True  # shows comments and extra whitespace
    self._last_content_height = 0

    self._torrc_location = None
    self._torrc_content = None
    self._torrc_load_error = None

    controller = tor_controller()
    controller.add_status_listener(self.reset_listener)
    self.reset_listener(controller, State.RESET, None)

  def reset_listener(self, controller, event_type, _):
    """
    Reloads and displays the torrc on tor reload (sighup) events.
    """

    if event_type == State.RESET:
      try:
        self._torrc_location = expand_path(controller.get_info('config-file'))
        contents = []

        with open(self._torrc_location) as torrc_file:
          for line in torrc_file.readlines():
            line = line.replace('\t', '   ').replace('\xc2', "'").rstrip()
            contents.append(filter(lambda char: char in string.printable, line))

        self._torrc_content = contents
      except ControllerError as exc:
        self._torrc_load_error = msg('panel.torrc.unable_to_find_torrc', error = exc)
        self._torrc_location = None
        self._torrc_content = None
      except Exception as exc:
        self._torrc_load_error = msg('panel.torrc.unable_to_load_torrc', error = exc.strerror)
        self._torrc_content = None

  def set_comments_visible(self, is_visible):
    """
    Sets if comments and blank lines are shown or stripped.

    :var bool is_visible: shows comments if true, strips otherwise
    """

    self._show_comments = is_visible
    self.redraw(True)

  def set_line_number_visible(self, is_visible):
    """
    Sets if line numbers are shown or hidden.

    :var bool is_visible: displays line numbers if true, hides otherwise
    """

    self._show_line_numbers = is_visible
    self.redraw(True)

  def key_handlers(self):
    def _scroll(key):
      page_height = self.get_preferred_size()[0] - 1
      is_changed = self._scroller.handle_key(key, self._last_content_height, page_height)

      if is_changed:
        self.redraw(True)

    def _toggle_comment_stripping():
      self.set_comments_visible(not self._show_comments)

    def _toggle_line_numbers():
      self.set_line_number_visible(not self._show_line_numbers)

    return (
      nyx.panel.KeyHandler('arrows', 'scroll up and down', _scroll, key_func = lambda key: key.is_scroll()),
      nyx.panel.KeyHandler('s', 'comment stripping', _toggle_comment_stripping, 'off' if self._show_comments else 'on'),
      nyx.panel.KeyHandler('l', 'line numbering', _toggle_line_numbers, 'on' if self._show_line_numbers else 'off'),
    )

  def draw(self, width, height):
    scroll = self._scroller.location(self._last_content_height - 1, height - 1)

    if self._torrc_content is None:
      self.addstr(1, 0, self._torrc_load_error, RED, BOLD)
      new_content_height = 1
    else:
      if not self._show_line_numbers:
        line_number_offset = 0
      elif len(self._torrc_content) == 0:
        line_number_offset = 2
      else:
        line_number_offset = int(math.log10(len(self._torrc_content))) + 2

      scroll_offset = 0

      if self._last_content_height > height - 1:
        scroll_offset = 3
        self.add_scroll_bar(scroll, scroll + height - 1, self._last_content_height - 1, 1)

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
          whitespace = ' ' * (len(line) - len(line.strip()))
          option, argument = line.strip().split(' ', 1)
          option = whitespace + option + ' '

        is_multiline = line.endswith('\\')  # next line's part of a multi-line entry

        if self._show_line_numbers:
          self.addstr(y, scroll_offset, str(line_number + 1).rjust(line_number_offset - 1), YELLOW, BOLD)

        x = line_number_offset + scroll_offset
        min_x = line_number_offset + scroll_offset

        x, y = self.addstr_wrap(y, x, option, width, min_x, GREEN, BOLD)
        x, y = self.addstr_wrap(y, x, argument, width, min_x, CYAN, BOLD)
        x, y = self.addstr_wrap(y, x, comment, width, min_x, WHITE)

        y += 1

      new_content_height = y + scroll - 1

    self.addstr(0, 0, ' ' * width)  # clear line
    location = ' (%s)' % self._torrc_location if self._torrc_location else ''
    self.addstr(0, 0, 'Tor Configuration File%s:' % location, HIGHLIGHT)

    if self._last_content_height != new_content_height:
      self._last_content_height = new_content_height
      self.redraw(True)
