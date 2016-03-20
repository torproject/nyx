"""
Functions for displaying popups in the interface.
"""

import math
import operator

import nyx.controller
import nyx.curses
import nyx.panel

from nyx import __version__, __release_date__, tor_controller
from nyx.curses import RED, GREEN, YELLOW, CYAN, WHITE, NORMAL, BOLD, HIGHLIGHT

NO_STATS_MSG = "Usage stats aren't available yet, press any key..."

HEADERS = ['Consensus:', 'Microdescriptor:', 'Server Descriptor:']
HEADER_COLOR = CYAN
LINE_NUMBER_COLOR = YELLOW

BLOCK_START, BLOCK_END = '-----BEGIN ', '-----END '

UNRESOLVED_MSG = 'No consensus data available'
ERROR_MSG = 'Unable to retrieve data'


def popup_window(height = -1, width = -1, top = 0, left = 0, below_static = True):
  """
  Provides a popup dialog you can use in a 'with' block...

    with popup_window(5, 10) as (popup, width, height):
      if popup:
        ... do stuff...

  This popup has a lock on the curses interface for the duration of the block,
  preventing other draw operations from taking place. If the popup isn't
  visible then the popup it returns will be **None**.

  :param int height: maximum height of the popup
  :param int width: maximum width of the popup
  :param int top: top position, relative to the sticky content
  :param int left: left position from the screen
  :param bool below_static: positions popup below static content if True

  :returns: tuple of the form (subwindow, width, height) when used in a with block
  """

  class _Popup(object):
    def __enter__(self):
      control = nyx.controller.get_controller()

      if below_static:
        sticky_height = sum([sticky_panel.get_height() for sticky_panel in control.get_sticky_panels()])
      else:
        sticky_height = 0

      popup = nyx.panel.Panel('popup', top + sticky_height, left, height, width)
      popup.set_visible(True)

      # Redraws the popup to prepare a subwindow instance. If none is spawned then
      # the panel can't be drawn (for instance, due to not being visible).

      popup.redraw(True)

      if popup.win is not None:
        nyx.curses.CURSES_LOCK.acquire()
        return (popup, popup.max_x - 1, popup.max_y)
      else:
        return (None, 0, 0)

    def __exit__(self, exit_type, value, traceback):
      nyx.curses.CURSES_LOCK.release()
      nyx.controller.get_controller().redraw(False)

  return _Popup()


def input_prompt(msg, initial_value = ''):
  """
  Prompts the user to enter a string on the control line (which usually
  displays the page number and basic controls).

  Arguments:
    msg          - message to prompt the user for input with
    initial_value - initial value of the field
  """

  with nyx.curses.CURSES_LOCK:
    control = nyx.controller.get_controller()
    msg_panel = control.get_panel('msg')
    msg_panel.set_message(msg)
    msg_panel.redraw(True)
    user_input = msg_panel.getstr(0, len(msg), initial_value)
    control.set_msg()

    return user_input


def show_msg(msg, max_wait = None, attr = HIGHLIGHT):
  """
  Displays a single line message on the control line for a set time. Pressing
  any key will end the message. This returns the key pressed.

  Arguments:
    msg     - message to be displayed to the user
    max_wait - time to show the message, indefinite if None
    attr    - attributes with which to draw the message
  """

  with nyx.curses.CURSES_LOCK:
    control = nyx.controller.get_controller()
    control.set_msg(msg, attr, True)

    key_press = nyx.curses.key_input(max_wait)
    control.set_msg()
    return key_press


def show_help_popup():
  """
  Presents a popup with instructions for the current page's hotkeys. This
  returns the user input used to close the popup. If the popup didn't close
  properly, this is an arrow, enter, or scroll key then this returns None.
  """

  with popup_window(9, 80) as (popup, _, height):
    if popup:
      exit_key = None
      control = nyx.controller.get_controller()
      page_panels = control.get_display_panels()

      # the first page is the only one with multiple panels, and it looks better
      # with the log entries first, so reversing the order

      page_panels.reverse()

      help_options = []

      for entry in page_panels:
        help_options += entry.get_help()

      # test doing afterward in case of overwriting

      popup.win.box()
      popup.addstr(0, 0, 'Page %i Commands:' % (control.get_page() + 1), HIGHLIGHT)

      for i in range(len(help_options)):
        if i / 2 >= height - 2:
          break

        # draws entries in the form '<key>: <description>[ (<selection>)]', for
        # instance...
        # u: duplicate log entries (hidden)

        key, description, selection = help_options[i]

        if key:
          description = ': ' + description

        row = (i / 2) + 1
        col = 2 if i % 2 == 0 else 41

        popup.addstr(row, col, key, BOLD)
        col += len(key)
        popup.addstr(row, col, description)
        col += len(description)

        if selection:
          popup.addstr(row, col, ' (')
          popup.addstr(row, col + 2, selection, BOLD)
          popup.addstr(row, col + 2 + len(selection), ')')

      # tells user to press a key if the lower left is unoccupied

      if len(help_options) < 13 and height == 9:
        popup.addstr(7, 2, 'Press any key...')

      popup.win.refresh()
      exit_key = nyx.curses.key_input()

  if not exit_key.is_selection() and not exit_key.is_scroll() and \
    not exit_key.match('left', 'right'):
    return exit_key
  else:
    return None


def show_about_popup():
  """
  Presents a popup with author and version information.
  """

  with popup_window(9, 80) as (popup, _, height):
    if popup:
      popup.win.box()
      popup.addstr(0, 0, 'About:', HIGHLIGHT)
      popup.addstr(1, 2, 'nyx, version %s (released %s)' % (__version__, __release_date__), BOLD)
      popup.addstr(2, 4, 'Written by Damian Johnson (atagar@torproject.org)')
      popup.addstr(3, 4, 'Project page: www.atagar.com/nyx')
      popup.addstr(5, 2, 'Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)')
      popup.addstr(7, 2, 'Press any key...')
      popup.win.refresh()

      nyx.curses.key_input()


def show_count_dialog(title, counts):
  """
  Provides a dialog with bar graphs and percentages for the given set of
  counts. Pressing any key closes the dialog.

  :param str title: dialog title
  :param dict counts: mapping of labels to their value
  """

  if not counts:
    height, width = 3, len(NO_STATS_MSG) + 4
  else:
    height, width = 4 + max(1, len(counts)), 80

  with nyx.popups.popup_window(height, width) as (popup, width, height):
    if not popup:
      return

    if not counts:
      popup.addstr(1, 2, NO_STATS_MSG, CYAN, BOLD)
    else:
      key_width, val_width, value_total = 3, 1, 0

      for k, v in counts.items():
        key_width = max(key_width, len(k))
        val_width = max(val_width, len(str(v)))
        value_total += v

      sorted_counts = sorted(counts.iteritems(), key = operator.itemgetter(1), reverse = True)
      graph_width = width - key_width - val_width - 11  # border, extra spaces, and percentage column

      for y, (k, v) in enumerate(sorted_counts):
        label = '%s %s (%-2i%%)' % (k.ljust(key_width), str(v).rjust(val_width), v * 100 / value_total)
        x = popup.addstr(y + 1, 2, label, GREEN, BOLD)

        for j in range(graph_width * v / value_total):
          popup.addstr(y + 1, x + j + 1, ' ', RED, HIGHLIGHT)

      popup.addstr(height - 2, 2, 'Press any key...')

    popup.win.box()
    popup.addstr(0, 0, title, HIGHLIGHT)
    popup.win.refresh()

    nyx.curses.key_input()


def show_sort_dialog(title, options, old_selection, option_colors):
  """
  Displays a sorting dialog of the form:

    Current Order: <previous selection>
    New Order: <selections made>

    <option 1>    <option 2>    <option 3>   Cancel

  Options are colored when among the "Current Order" or "New Order", but not
  when an option below them. If cancel is selected or the user presses escape
  then this returns None. Otherwise, the new ordering is provided.

  Arguments:
    title   - title displayed for the popup window
    options      - ordered listing of option labels
    old_selection - current ordering
    option_colors - mappings of options to their color
  """

  with popup_window(9, 80) as (popup, _, _):
    if popup:
      new_selections = []  # new ordering
      cursor_location = 0     # index of highlighted option

      selection_options = list(options)
      selection_options.append('Cancel')

      while len(new_selections) < len(old_selection):
        popup.win.erase()
        popup.win.box()
        popup.addstr(0, 0, title, HIGHLIGHT)

        _draw_sort_selection(popup, 1, 2, 'Current Order: ', old_selection, option_colors)
        _draw_sort_selection(popup, 2, 2, 'New Order: ', new_selections, option_colors)

        # presents remaining options, each row having up to four options with
        # spacing of nineteen cells

        row, col = 4, 0

        for i in range(len(selection_options)):
          option_format = HIGHLIGHT if cursor_location == i else NORMAL
          popup.addstr(row, col * 19 + 2, selection_options[i], option_format)
          col += 1

          if col == 4:
            row, col = row + 1, 0

        popup.win.refresh()

        key = nyx.curses.key_input()

        if key.match('left'):
          cursor_location = max(0, cursor_location - 1)
        elif key.match('right'):
          cursor_location = min(len(selection_options) - 1, cursor_location + 1)
        elif key.match('up'):
          cursor_location = max(0, cursor_location - 4)
        elif key.match('down'):
          cursor_location = min(len(selection_options) - 1, cursor_location + 4)
        elif key.is_selection():
          selection = selection_options[cursor_location]

          if selection == 'Cancel':
            break
          else:
            new_selections.append(selection)
            selection_options.remove(selection)
            cursor_location = min(cursor_location, len(selection_options) - 1)
        elif key.match('esc'):
          break  # esc - cancel

  if len(new_selections) == len(old_selection):
    return new_selections
  else:
    return None


def _draw_sort_selection(popup, y, x, prefix, options, option_colors):
  """
  Draws a series of comma separated sort selections. The whole line is bold
  and sort options also have their specified color. Example:

    Current Order: Man Page Entry, Option Name, Is Default

  Arguments:
    popup        - panel in which to draw sort selection
    y            - vertical location
    x            - horizontal location
    prefix       - initial string description
    options      - sort options to be shown
    option_colors - mappings of options to their color
  """

  popup.addstr(y, x, prefix, BOLD)
  x += len(prefix)

  for i in range(len(options)):
    sort_type = options[i]
    popup.addstr(y, x, sort_type, option_colors.get(sort_type, WHITE), BOLD)
    x += len(sort_type)

    # comma divider between options, if this isn't the last

    if i < len(options) - 1:
      popup.addstr(y, x, ', ', BOLD)
      x += 2


def show_menu(title, options, old_selection):
  """
  Provides menu with options laid out in a single column. User can cancel
  selection with the escape key, in which case this proives -1. Otherwise this
  returns the index of the selection.

  Arguments:
    title        - title displayed for the popup window
    options      - ordered listing of options to display
    old_selection - index of the initially selected option (uses the first
                   selection without a carrot if -1)
  """

  max_width = max(map(len, options)) + 9

  with popup_window(len(options) + 2, max_width) as (popup, _, _):
    if not popup:
      return -1

    selection = old_selection if old_selection != -1 else 0

    # hides the title of the first panel on the page

    control = nyx.controller.get_controller()
    top_panel = control.get_display_panels(include_sticky = False)[0]
    top_panel.set_title_visible(False)
    top_panel.redraw(True)

    while True:
      popup.win.erase()
      popup.win.box()
      popup.addstr(0, 0, title, HIGHLIGHT)

      for i in range(len(options)):
        label = options[i]
        format = HIGHLIGHT if i == selection else NORMAL
        tab = '> ' if i == old_selection else '  '
        popup.addstr(i + 1, 2, tab)
        popup.addstr(i + 1, 4, ' %s ' % label, format)

      popup.win.refresh()

      key = nyx.curses.key_input()

      if key.match('up'):
        selection = max(0, selection - 1)
      elif key.match('down'):
        selection = min(len(options) - 1, selection + 1)
      elif key.is_selection():
        break
      elif key.match('esc'):
        selection = -1
        break

  top_panel.set_title_visible(True)
  return selection


def show_descriptor_popup(fingerprint, color, max_width, is_close_key):
  """
  Provides a dialog showing the descriptors for a given relay.

  :param str fingerprint: fingerprint of the relay to be shown
  :param str color: text color of the dialog
  :param int max_width: maximum width of the dialog
  :param function is_close_key: method to indicate if a key should close the
    dialog or not

  :returns: :class:`~nyx.curses.KeyInput` for the keyboard input that
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

  with popup_window(popup_height, popup_width) as (popup, _, height):
    if not popup:
      return None

    scroller, redraw = nyx.curses.Scroller(), True

    while True:
      if redraw:
        _draw(popup, title, lines, color, scroller.location(), show_line_numbers)
        redraw = False

      key = nyx.curses.key_input()

      if key.is_scroll():
        redraw = scroller.handle_key(key, len(lines), height - 2)
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
      popup.addstr(y, 2, str(i + 1).rjust(line_number_width), LINE_NUMBER_COLOR, BOLD)

    x, y = popup.addstr_wrap(y, 3 + line_number_width, keyword, width, offset, color, BOLD)
    x, y = popup.addstr_wrap(y, x + 1, value, width, offset, color)

    y += 1

    if y > height:
      break

  popup.win.box()
  popup.addstr(0, 0, title, HIGHLIGHT)
  popup.win.refresh()
