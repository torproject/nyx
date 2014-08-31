"""
Panel displaying the torrc or armrc with the validation done against it.
"""

import math
import curses
import threading

import arm.popups

from arm.util import panel, tor_config, tor_controller, ui_tools

from stem.control import State
from stem.util import conf, enum


def conf_handler(key, value):
  if key == "features.config.file.max_lines_per_entry":
    return max(1, value)


CONFIG = conf.config_dict("arm", {
  "features.config.file.showScrollbars": True,
  "features.config.file.max_lines_per_entry": 8,
}, conf_handler)

# TODO: The armrc use case is incomplete. There should be equivilant reloading
# and validation capabilities to the torrc.
Config = enum.Enum("TORRC", "ARMRC")  # configuration file types that can be displayed


class TorrcPanel(panel.Panel):
  """
  Renders the current torrc or armrc with syntax highlighting in a scrollable
  area.
  """

  def __init__(self, stdscr, config_type):
    panel.Panel.__init__(self, stdscr, "torrc", 0)

    self.vals_lock = threading.RLock()
    self.config_type = config_type
    self.scroll = 0
    self.show_line_num = True     # shows left aligned line numbers
    self.strip_comments = False   # drops comments and extra whitespace

    # height of the content when last rendered (the cached value is invalid if
    # _last_content_height_args is None or differs from the current dimensions)

    self._last_content_height = 1
    self._last_content_height_args = None

    # listens for tor reload (sighup) events

    controller = tor_controller()
    controller.add_status_listener(self.reset_listener)

    if controller.is_alive():
      self.reset_listener(None, State.INIT, None)

  def reset_listener(self, controller, event_type, _):
    """
    Reloads and displays the torrc on tor reload (sighup) events.
    """

    if event_type == State.INIT:
      # loads the torrc and provides warnings in case of validation errors

      try:
        loaded_torrc = tor_config.get_torrc()
        loaded_torrc.load(True)
        loaded_torrc.log_validation_issues()
        self.redraw(True)
      except:
        pass
    elif event_type == State.RESET:
      try:
        tor_config.get_torrc().load(True)
        self.redraw(True)
      except:
        pass

  def set_comments_visible(self, is_visible):
    """
    Sets if comments and blank lines are shown or stripped.

    Arguments:
      is_visible - displayed comments and blank lines if true, strips otherwise
    """

    self.strip_comments = not is_visible
    self._last_content_height_args = None
    self.redraw(True)

  def set_line_number_visible(self, is_visible):
    """
    Sets if line numbers are shown or hidden.

    Arguments:
      is_visible - displays line numbers if true, hides otherwise
    """

    self.show_line_num = is_visible
    self._last_content_height_args = None
    self.redraw(True)

  def reload_torrc(self):
    """
    Reloads the torrc, displaying an indicator of success or failure.
    """

    try:
      tor_config.get_torrc().load()
      self._last_content_height_args = None
      self.redraw(True)
      result_msg = "torrc reloaded"
    except IOError:
      result_msg = "failed to reload torrc"

    self._last_content_height_args = None
    self.redraw(True)
    arm.popups.show_msg(result_msg, 1)

  def handle_key(self, key):
    self.vals_lock.acquire()
    is_keystroke_consumed = True
    if ui_tools.is_scroll_key(key):
      page_height = self.get_preferred_size()[0] - 1
      new_scroll = ui_tools.get_scroll_position(key, self.scroll, page_height, self._last_content_height)

      if self.scroll != new_scroll:
        self.scroll = new_scroll
        self.redraw(True)
    elif key == ord('n') or key == ord('N'):
      self.set_line_number_visible(not self.show_line_num)
    elif key == ord('s') or key == ord('S'):
      self.set_comments_visible(self.strip_comments)
    elif key == ord('r') or key == ord('R'):
      self.reload_torrc()
    else:
      is_keystroke_consumed = False

    self.vals_lock.release()
    return is_keystroke_consumed

  def set_visible(self, is_visible):
    if not is_visible:
      self._last_content_height_args = None  # redraws when next displayed

    panel.Panel.set_visible(self, is_visible)

  def get_help(self):
    options = []
    options.append(("up arrow", "scroll up a line", None))
    options.append(("down arrow", "scroll down a line", None))
    options.append(("page up", "scroll up a page", None))
    options.append(("page down", "scroll down a page", None))
    options.append(("s", "comment stripping", "on" if self.strip_comments else "off"))
    options.append(("n", "line numbering", "on" if self.show_line_num else "off"))
    options.append(("r", "reload torrc", None))
    options.append(("x", "reset tor (issue sighup)", None))
    return options

  def draw(self, width, height):
    self.vals_lock.acquire()

    # If true, we assume that the cached value in self._last_content_height is
    # still accurate, and stop drawing when there's nothing more to display.
    # Otherwise the self._last_content_height is suspect, and we'll process all
    # the content to check if it's right (and redraw again with the corrected
    # height if not).

    trust_last_content_height = self._last_content_height_args == (width, height)

    # restricts scroll location to valid bounds

    self.scroll = max(0, min(self.scroll, self._last_content_height - height + 1))

    rendered_contents, corrections, conf_location = None, {}, None

    if self.config_type == Config.TORRC:
      loaded_torrc = tor_config.get_torrc()
      loaded_torrc.get_lock().acquire()
      conf_location = loaded_torrc.get_config_location()

      if not loaded_torrc.is_loaded():
        rendered_contents = ["### Unable to load the torrc ###"]
      else:
        rendered_contents = loaded_torrc.get_display_contents(self.strip_comments)

        # constructs a mapping of line numbers to the issue on it

        corrections = dict((line_number, (issue, msg)) for line_number, issue, msg in loaded_torrc.get_corrections())

      loaded_torrc.get_lock().release()
    else:
      loaded_armrc = conf.get_config("arm")
      conf_location = loaded_armrc._path
      rendered_contents = list(loaded_armrc._raw_contents)

    # offset to make room for the line numbers

    line_number_offset = 0

    if self.show_line_num:
      if len(rendered_contents) == 0:
        line_number_offset = 2
      else:
        line_number_offset = int(math.log10(len(rendered_contents))) + 2

    # draws left-hand scroll bar if content's longer than the height

    scroll_offset = 0

    if CONFIG["features.config.file.showScrollbars"] and self._last_content_height > height - 1:
      scroll_offset = 3
      self.add_scroll_bar(self.scroll, self.scroll + height - 1, self._last_content_height, 1)

    display_line = -self.scroll + 1  # line we're drawing on

    # draws the top label

    if self.is_title_visible():
      source_label = "Tor" if self.config_type == Config.TORRC else "Arm"
      location_label = " (%s)" % conf_location if conf_location else ""
      self.addstr(0, 0, "%s Configuration File%s:" % (source_label, location_label), curses.A_STANDOUT)

    is_multiline = False  # true if we're in the middle of a multiline torrc entry

    for line_number in range(0, len(rendered_contents)):
      line_text = rendered_contents[line_number]
      line_text = line_text.rstrip()  # remove ending whitespace

      # blank lines are hidden when stripping comments

      if self.strip_comments and not line_text:
        continue

      # splits the line into its component (msg, format) tuples

      line_comp = {
        'option': ['', (curses.A_BOLD, 'green')],
        'argument': ['', (curses.A_BOLD, 'cyan')],
        'correction': ['', (curses.A_BOLD, 'cyan')],
        'comment': ['', ('white',)],
      }

      # parses the comment

      comment_index = line_text.find("#")

      if comment_index != -1:
        line_comp['comment'][0] = line_text[comment_index:]
        line_text = line_text[:comment_index]

      # splits the option and argument, preserving any whitespace around them

      stripped_line = line_text.strip()
      option_index = stripped_line.find(" ")

      if is_multiline:
        # part of a multiline entry started on a previous line so everything
        # is part of the argument
        line_comp['argument'][0] = line_text
      elif option_index == -1:
        # no argument provided
        line_comp['option'][0] = line_text
      else:
        option_text = stripped_line[:option_index]
        option_end = line_text.find(option_text) + len(option_text)
        line_comp['option'][0] = line_text[:option_end]
        line_comp['argument'][0] = line_text[option_end:]

      # flags following lines as belonging to this multiline entry if it ends
      # with a slash

      if stripped_line:
        is_multiline = stripped_line.endswith("\\")

      # gets the correction

      if line_number in corrections:
        line_issue, line_issue_msg = corrections[line_number]

        if line_issue in (tor_config.ValidationError.DUPLICATE, tor_config.ValidationError.IS_DEFAULT):
          line_comp['option'][1] = (curses.A_BOLD, 'blue')
          line_comp['argument'][1] = (curses.A_BOLD, 'blue')
        elif line_issue == tor_config.ValidationError.MISMATCH:
          line_comp['argument'][1] = (curses.A_BOLD, 'red')
          line_comp['correction'][0] = ' (%s)' % line_issue_msg
        else:
          # For some types of configs the correction field is simply used to
          # provide extra data (for instance, the type for tor state fields).

          line_comp['correction'][0] = ' (%s)' % line_issue_msg
          line_comp['correction'][1] = (curses.A_BOLD, 'magenta')

      # draws the line number

      if self.show_line_num and display_line < height and display_line >= 1:
        line_number_str = ("%%%ii" % (line_number_offset - 1)) % (line_number + 1)
        self.addstr(display_line, scroll_offset, line_number_str, curses.A_BOLD, 'yellow')

      # draws the rest of the components with line wrap

      cursor_location, line_offset = line_number_offset + scroll_offset, 0
      max_lines_per_entry = CONFIG["features.config.file.max_lines_per_entry"]
      display_queue = [line_comp[entry] for entry in ('option', 'argument', 'correction', 'comment')]

      while display_queue:
        msg, format = display_queue.pop(0)

        max_msg_size, include_break = width - cursor_location, False

        if len(msg) >= max_msg_size:
          # message is too long - break it up

          if line_offset == max_lines_per_entry - 1:
            msg = ui_tools.crop_str(msg, max_msg_size)
          else:
            include_break = True
            msg, remainder = ui_tools.crop_str(msg, max_msg_size, 4, 4, ui_tools.Ending.HYPHEN, True)
            display_queue.insert(0, (remainder.strip(), format))

        draw_line = display_line + line_offset

        if msg and draw_line < height and draw_line >= 1:
          self.addstr(draw_line, cursor_location, msg, *format)

        # If we're done, and have added content to this line, then start
        # further content on the next line.

        cursor_location += len(msg)
        include_break |= not display_queue and cursor_location != line_number_offset + scroll_offset

        if include_break:
          line_offset += 1
          cursor_location = line_number_offset + scroll_offset

      display_line += max(line_offset, 1)

      if trust_last_content_height and display_line >= height:
        break

    if not trust_last_content_height:
      self._last_content_height_args = (width, height)
      new_content_height = display_line + self.scroll - 1

      if self._last_content_height != new_content_height:
        self._last_content_height = new_content_height
        self.redraw(True)

    self.vals_lock.release()
