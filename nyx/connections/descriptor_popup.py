"""
Popup providing the raw descriptor and consensus information for a relay.
"""

import math
import curses

import nyx.popups

from nyx.util import tor_controller, ui_tools

from stem.util import str_tools

HEADERS = ['Consensus:', 'Microdescriptor:', 'Server Descriptor:']
HEADER_COLOR = 'cyan'
LINE_NUMBER_COLOR = 'yellow'

BLOCK_START, BLOCK_END = '-----BEGIN ', '-----END '

UNRESOLVED_MSG = 'No consensus data available'
ERROR_MSG = 'Unable to retrieve data'


def show_descriptor_popup(fingerprint, color, max_width, is_close_key):
  """
  Provides a dialog showing the descriptors for a given relay.

  :param str fingerprint: fingerprint of the relay to be shown
  :param str color: text color of the dialog
  :param int max_width: maximum width of the dialog
  :param function is_close_key: method to indicate if a key should close the
    dialog or not

  :returns: :class:`~nyx.util.panel.KeyInput` for the keyboard input that
    closed the dialog
  """

  if fingerprint:
    title = 'Consensus Descriptor:'
    lines = _display_text(fingerprint)
    show_line_numbers = True
  else:
    title = 'Consensus Descriptor (%s):' % fingerprint
    lines = [UNRESOLVED_MSG]
    show_line_numbers = False

  popup_height, popup_width = _preferred_size(lines, max_width, show_line_numbers)

  with nyx.popups.popup_window(popup_height, popup_width) as (popup, _, height):
    if not popup:
      return None

    scroll, redraw = 0, True

    while True:
      if redraw:
        _draw(popup, title, lines, color, scroll, show_line_numbers)
        redraw = False

      key = nyx.controller.get_controller().key_input()

      if key.is_scroll():
        new_scroll = ui_tools.get_scroll_position(key, scroll, height - 2, len(lines))

        if scroll != new_scroll:
          scroll, redraw = new_scroll, True
      elif is_close_key(key):
        return key


def _display_text(fingerprint):
  """
  Provides the descriptors for a relay.

  :param str fingerprint: relay fingerprint to be looked up

  :returns: **list** with the lines that should be displayed in the dialog
  """

  controller = tor_controller()
  router_status_entry = controller.get_network_status(fingerprint, None)
  microdescriptor = controller.get_microdescriptor(fingerprint, None)
  server_descriptor = controller.get_server_descriptor(fingerprint, None)

  description = 'Consensus:\n\n%s' % (router_status_entry if router_status_entry else ERROR_MSG)

  if server_descriptor:
    description += '\n\nServer Descriptor:\n\n%s' % server_descriptor

  if microdescriptor:
    description += '\n\nMicrodescriptor:\n\n%s' % microdescriptor

  return description.split('\n')


def _preferred_size(text, max_width, show_line_numbers):
  """
  Provides the preferred dimensions of our dialog.

  :param list text: lines of text to be shown
  :param int max_width: maximum width the dialog can be
  :param bool show_line_numbers: if we should leave room for line numbers

  :returns: **tuple** of the preferred (height, width)
  """

  width, height = 0, len(text) + 2
  line_number_width = int(math.log10(len(text))) + 2 if show_line_numbers else 0
  max_content_width = max_width - line_number_width - 4

  for line in text:
    width = min(max_width, max(width, len(line) + line_number_width + 4))
    height += len(line) / max_content_width  # extra lines due to text wrap

  return (height, width)


def _draw(popup, title, lines, entry_color, scroll, show_line_numbers):
  def draw_msg(popup, min_x, x, y, width, msg, *attr):
    while msg:
      draw_msg, msg = str_tools.crop(msg, width - x, None, ending = None, get_remainder = True)

      if not draw_msg:
        draw_msg, msg = str_tools.crop(msg, width - x), ''  # first word is longer than the line

      x = popup.addstr(y, x, draw_msg, *attr)

      if msg:
        x, y = min_x, y + 1

    return x, y

  popup.win.erase()

  line_number_width = int(math.log10(len(lines))) + 1
  in_block = False   # flag indicating if we're currently in crypto content
  width = popup.max_x - 2  # leave space on the right for the border and an empty line
  height = popup.max_y - 2  # height of the dialog without the top and bottom border
  offset = line_number_width + 3 if show_line_numbers else 2

  y = 1

  for i, line in enumerate(lines):
    keyword, value = line, ''
    color = entry_color

    if line in HEADERS:
      color = HEADER_COLOR
    elif line.startswith(BLOCK_START):
      in_block = True
    elif line.startswith(BLOCK_END):
      in_block = False
    elif in_block:
      keyword, value = '', line
    elif ' ' in line and line != UNRESOLVED_MSG and line != ERROR_MSG:
      keyword, value = line.split(' ', 1)

    if i < scroll:
      continue

    if show_line_numbers:
      popup.addstr(y, 2, str(i + 1).rjust(line_number_width), curses.A_BOLD, LINE_NUMBER_COLOR)

    x, y = draw_msg(popup, offset, offset, y, width, keyword, color, curses.A_BOLD)
    x, y = draw_msg(popup, offset, x + 1, y, width, value, color)

    y += 1

    if y > height:
      break

  popup.win.box()
  popup.addstr(0, 0, title, curses.A_STANDOUT)
  popup.win.refresh()
