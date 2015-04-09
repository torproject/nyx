"""
Popup providing the raw descriptor and consensus information for a relay.
"""

import math
import curses

import nyx.popups
import nyx.connections.conn_entry

from nyx.util import panel, tor_controller, ui_tools

from stem.util import str_tools

# field keywords used to identify areas for coloring

LINE_NUM_COLOR = 'yellow'
HEADER_COLOR = 'cyan'
HEADER_PREFIX = ['ns/id/', 'desc/id/']

SIG_COLOR = 'red'
SIG_START_KEYS = ['-----BEGIN RSA PUBLIC KEY-----', '-----BEGIN SIGNATURE-----']
SIG_END_KEYS = ['-----END RSA PUBLIC KEY-----', '-----END SIGNATURE-----']

UNRESOLVED_MSG = 'No consensus data available'
ERROR_MSG = 'Unable to retrieve data'


def show_descriptor_popup(conn_panel):
  """
  Presents consensus descriptor in popup window with the following controls:
  Up, Down, Page Up, Page Down - scroll descriptor
  Right, Left - next / previous connection
  Enter, Space, d, D - close popup

  Arguments:
    conn_panel - connection panel providing the dialog
  """

  # hides the title of the connection panel

  conn_panel.set_title_visible(False)
  conn_panel.redraw(True)

  control = nyx.controller.get_controller()
  panel.CURSES_LOCK.acquire()
  is_done = False

  try:
    while not is_done:
      selection = conn_panel.get_selection()

      if not selection:
        break

      fingerprint = selection.foreign.get_fingerprint()

      if fingerprint == 'UNKNOWN':
        fingerprint = None

      display_text = get_display_text(fingerprint)
      display_color = nyx.connections.conn_entry.CATEGORY_COLOR[selection.get_type()]
      show_line_number = fingerprint is not None

      # determines the maximum popup size the display_text can fill

      popup_height, popup_width = get_preferred_size(display_text, conn_panel.max_x, show_line_number)

      popup, _, height = nyx.popups.init(popup_height, popup_width)

      if not popup:
        break

      scroll, is_changed = 0, True

      try:
        while not is_done:
          if is_changed:
            draw(popup, fingerprint, display_text, display_color, scroll, show_line_number)
            is_changed = False

          key = control.key_input()

          if key.is_scroll():
            # TODO: This is a bit buggy in that scrolling is by display_text
            # lines rather than the displayed lines, causing issues when
            # content wraps. The result is that we can't have a scrollbar and
            # can't scroll to the bottom if there's a multi-line being
            # displayed. However, trying to correct this introduces a big can
            # of worms and after hours decided that this isn't worth the
            # effort...

            new_scroll = ui_tools.get_scroll_position(key, scroll, height - 2, len(display_text))

            if scroll != new_scroll:
              scroll, is_changed = new_scroll, True
          elif key.is_selection() or key.match('d'):
            is_done = True  # closes popup
          elif key.match('left', 'right'):
            # navigation - pass on to conn_panel and recreate popup

            conn_panel.handle_key(panel.KeyInput(curses.KEY_UP) if key.match('left') else panel.KeyInput(curses.KEY_DOWN))
            break
      finally:
        nyx.popups.finalize()
  finally:
    conn_panel.set_title_visible(True)
    conn_panel.redraw(True)
    panel.CURSES_LOCK.release()


def get_display_text(fingerprint):
  """
  Provides the descriptor and consensus entry for a relay. This is a list of
  lines to be displayed by the dialog.
  """

  if not fingerprint:
    return [UNRESOLVED_MSG]

  controller, description = tor_controller(), []

  description.append('ns/id/%s' % fingerprint)
  consensus_entry = controller.get_info('ns/id/%s' % fingerprint, None)

  if consensus_entry:
    description += consensus_entry.split('\n')
  else:
    description += [ERROR_MSG, '']

  description.append('desc/id/%s' % fingerprint)
  descriptor_entry = controller.get_info('desc/id/%s' % fingerprint, None)

  if descriptor_entry:
    description += descriptor_entry.split('\n')
  else:
    description += [ERROR_MSG]

  return description


def get_preferred_size(text, max_width, show_line_number):
  """
  Provides the (height, width) tuple for the preferred size of the given text.
  """

  width, height = 0, len(text) + 2
  line_number_width = int(math.log10(len(text))) + 1

  for line in text:
    # width includes content, line number field, and border

    line_width = len(line) + 5

    if show_line_number:
      line_width += line_number_width

    width = max(width, line_width)

    # tracks number of extra lines that will be taken due to text wrap
    height += (line_width - 2) / max_width

  return (height, width)


def draw(popup, fingerprint, display_text, display_color, scroll, show_line_number):
  popup.win.erase()
  popup.win.box()
  x_offset = 2

  if fingerprint:
    title = 'Consensus Descriptor (%s):' % fingerprint
  else:
    title = 'Consensus Descriptor:'

  popup.addstr(0, 0, title, curses.A_STANDOUT)

  line_number_width = int(math.log10(len(display_text))) + 1
  is_encryption_block = False   # flag indicating if we're currently displaying a key

  # checks if first line is in an encryption block

  for i in range(0, scroll):
    line_text = display_text[i].strip()

    if line_text in SIG_START_KEYS:
      is_encryption_block = True
    elif line_text in SIG_END_KEYS:
      is_encryption_block = False

  draw_line, page_height = 1, popup.max_y - 2

  for i in range(scroll, scroll + page_height):
    line_text = display_text[i].strip()
    x_offset = 2

    if show_line_number:
      line_number_label = ('%%%ii' % line_number_width) % (i + 1)

      popup.addstr(draw_line, x_offset, line_number_label, curses.A_BOLD, LINE_NUM_COLOR)
      x_offset += line_number_width + 1

    # Most consensus and descriptor lines are keyword/value pairs. Both are
    # shown with the same color, but the keyword is bolded.

    keyword, value = line_text, ''
    draw_format = display_color

    if line_text.startswith(HEADER_PREFIX[0]) or line_text.startswith(HEADER_PREFIX[1]):
      keyword, value = line_text, ''
      draw_format = HEADER_COLOR
    elif line_text == UNRESOLVED_MSG or line_text == ERROR_MSG:
      keyword, value = line_text, ''
    elif line_text in SIG_START_KEYS:
      keyword, value = line_text, ''
      is_encryption_block = True
      draw_format = SIG_COLOR
    elif line_text in SIG_END_KEYS:
      keyword, value = line_text, ''
      is_encryption_block = False
      draw_format = SIG_COLOR
    elif is_encryption_block:
      keyword, value = '', line_text
      draw_format = SIG_COLOR
    elif ' ' in line_text:
      div_index = line_text.find(' ')
      keyword, value = line_text[:div_index], line_text[div_index:]

    display_queue = [(keyword, (draw_format, curses.A_BOLD)), (value, (draw_format,))]
    cursor_location = x_offset

    while display_queue:
      msg, msg_format = display_queue.pop(0)

      if not msg:
        continue

      max_msg_size = popup.max_x - 1 - cursor_location

      if len(msg) >= max_msg_size:
        # needs to split up the line

        msg, remainder = str_tools.crop(msg, max_msg_size, None, ending = None, get_remainder = True)

        if x_offset == cursor_location and msg == '':
          # first word is longer than the line

          msg = str_tools.crop(remainder, max_msg_size)

          if ' ' in remainder:
            remainder = remainder.split(' ', 1)[1]
          else:
            remainder = ''

        popup.addstr(draw_line, cursor_location, msg, *msg_format)
        cursor_location = x_offset

        if remainder:
          display_queue.insert(0, (remainder.strip(), msg_format))
          draw_line += 1
      else:
        popup.addstr(draw_line, cursor_location, msg, *msg_format)
        cursor_location += len(msg)

      if draw_line > page_height:
        break

    draw_line += 1

    if draw_line > page_height:
      break

  popup.win.refresh()
