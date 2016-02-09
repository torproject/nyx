"""
Panel displaying the torrc or nyxrc with the validation done against it.
"""

import math
import curses

from nyx.util import expand_path, msg, panel, tor_controller, ui_tools

from stem import ControllerError
from stem.control import State
from stem.util import conf, str_tools


def conf_handler(key, value):
  if key == 'features.log.maxLineWrap':
    return max(1, value)


CONFIG = conf.config_dict('nyx', {
  'features.torrc.maxLineWrap': 8,
}, conf_handler)


class TorrcPanel(panel.Panel):
  """
  Renders the current torrc or nyxrc with syntax highlighting in a scrollable
  area.
  """

  def __init__(self, stdscr):
    panel.Panel.__init__(self, stdscr, 'torrc', 0)

    self._scroll = 0
    self._show_line_numbers = True  # shows left aligned line numbers
    self._strip_comments = False  # drops comments and extra whitespace

    # height of the content when last rendered (the cached value is invalid if
    # _last_content_height_args is None or differs from the current dimensions)

    self._last_content_height = 0

    self._torrc_location = None
    self._torrc_content = None
    self._torrc_load_error = None

    # listens for tor reload (sighup) events

    controller = tor_controller()
    controller.add_status_listener(self.reset_listener)

    if controller.is_alive():
      self.reset_listener(controller, State.RESET, None)

  def reset_listener(self, controller, event_type, _):
    """
    Reloads and displays the torrc on tor reload (sighup) events.
    """

    if event_type == State.RESET:
      try:
        self._torrc_location = expand_path(controller.get_info('config-file'))

        with open(self._torrc_location) as torrc_file:
          self._torrc_content = [ui_tools.get_printable(line.replace('\t', '   ')) for line in torrc_file.readlines()]
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

    Arguments:
      is_visible - displayed comments and blank lines if true, strips otherwise
    """

    self._strip_comments = not is_visible
    self.redraw(True)

  def set_line_number_visible(self, is_visible):
    """
    Sets if line numbers are shown or hidden.

    Arguments:
      is_visible - displays line numbers if true, hides otherwise
    """

    self._show_line_numbers = is_visible
    self.redraw(True)

  def handle_key(self, key):
    if key.is_scroll():
      page_height = self.get_preferred_size()[0] - 1
      new_scroll = ui_tools.get_scroll_position(key, self._scroll, page_height, self._last_content_height)

      if self._scroll != new_scroll:
        self._scroll = new_scroll
        self.redraw(True)
    elif key.match('n'):
      self.set_line_number_visible(not self._show_line_numbers)
    elif key.match('s'):
      self.set_comments_visible(self._strip_comments)
    else:
      return False

    return True

  def get_help(self):
    return [
      ('up arrow', 'scroll up a line', None),
      ('down arrow', 'scroll down a line', None),
      ('page up', 'scroll up a page', None),
      ('page down', 'scroll down a page', None),
      ('s', 'comment stripping', 'on' if self._strip_comments else 'off'),
      ('n', 'line numbering', 'on' if self._show_line_numbers else 'off'),
      ('x', 'reset tor (issue sighup)', None),
    ]

  def draw(self, width, height):
    if self.is_title_visible():
      location = ' (%s)' % self._torrc_location if self._torrc_location else ''
      self.addstr(0, 0, 'Tor Configuration File%s:' % location, curses.A_STANDOUT)

    if self._torrc_content is None:
      self.addstr(1, 0, self._torrc_load_error, 'red', curses.A_BOLD)
      return

    self._scroll = max(0, min(self._scroll, self._last_content_height - height + 1))

    if not self._show_line_numbers:
      line_number_offset = 0
    elif len(self._torrc_content) == 0:
      line_number_offset = 2
    else:
      line_number_offset = int(math.log10(len(self._torrc_content))) + 2

    scroll_offset = 0

    if self._last_content_height > height - 1:
      scroll_offset = 3
      self.add_scroll_bar(self._scroll, self._scroll + height - 1, self._last_content_height, 1)

    display_line = -self._scroll + 1  # line we're drawing on
    is_multiline = False  # true if we're in the middle of a multiline torrc entry

    for line_number, line in enumerate(self._torrc_content):
      if self._strip_comments:
        line = line[:line.find('#')].rstrip() if '#' in line else line.rstrip()

        if not line:
          continue  # skip blank lines

      # splits the line into its component (label, attr) tuples

      line_comp = {
        'option': ['', (curses.A_BOLD, 'green')],
        'argument': ['', (curses.A_BOLD, 'cyan')],
        'correction': ['', (curses.A_BOLD, 'cyan')],
        'comment': ['', ('white',)],
      }

      # parses the comment

      comment_index = line.find('#')

      if comment_index != -1:
        line_comp['comment'][0] = line[comment_index:]
        line = line[:comment_index]

      # splits the option and argument, preserving any whitespace around them

      stripped_line = line.strip()
      option_index = stripped_line.find(' ')

      if is_multiline:
        # part of a multiline entry started on a previous line so everything
        # is part of the argument
        line_comp['argument'][0] = line
      elif option_index == -1:
        # no argument provided
        line_comp['option'][0] = line
      else:
        option_text = stripped_line[:option_index]
        option_end = line.find(option_text) + len(option_text)
        line_comp['option'][0] = line[:option_end]
        line_comp['argument'][0] = line[option_end:]

      # flags following lines as belonging to this multiline entry if it ends
      # with a slash

      if stripped_line:
        is_multiline = stripped_line.endswith('\\')

      # draws the line number

      if self._show_line_numbers and display_line < height and display_line >= 1:
        self.addstr(display_line, scroll_offset, str(line_number + 1).rjust(line_number_offset - 1), curses.A_BOLD, 'yellow')

      # draws the rest of the components with line wrap

      cursor_location, line_offset = line_number_offset + scroll_offset, 0
      display_queue = [line_comp[entry] for entry in ('option', 'argument', 'correction', 'comment')]

      while display_queue:
        label, attr = display_queue.pop(0)

        max_msg_size, include_break = width - cursor_location, False

        if len(label) >= max_msg_size:
          # message is too long - break it up

          if line_offset == CONFIG['features.log.maxLineWrap'] - 1:
            label = str_tools.crop(label, max_msg_size)
          else:
            include_break = True
            label, remainder = str_tools.crop(label, max_msg_size, 4, 4, str_tools.Ending.HYPHEN, True)
            display_queue.insert(0, (remainder.strip(), attr))

        draw_line = display_line + line_offset

        if label and draw_line < height and draw_line >= 1:
          self.addstr(draw_line, cursor_location, label, *attr)

        # If we're done, and have added content to this line, then start
        # further content on the next line.

        cursor_location += len(label)
        include_break |= not display_queue and cursor_location != line_number_offset + scroll_offset

        if include_break:
          line_offset += 1
          cursor_location = line_number_offset + scroll_offset

      display_line += max(line_offset, 1)

    new_content_height = display_line + self._scroll - 1

    if self._last_content_height != new_content_height:
      self._last_content_height = new_content_height
      self.redraw(True)
