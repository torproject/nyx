# Copyright 2016, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Functions for displaying popups in the interface.
"""

import math
import operator

import nyx
import nyx.controller
import nyx.curses
import nyx.panel

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
        sticky_height = control.header_panel().get_height()
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


def show_help():
  """
  Presents a popup with the current page's hotkeys.

  :returns: :class:`~nyx.curses.KeyInput` that was pressed to close the popup
    if it's one panels should act upon, **None** otherwise
  """

  control = nyx.controller.get_controller()
  handlers = []

  for panel in reversed(control.get_display_panels()):
    handlers += [handler for handler in panel.key_handlers() if handler.description]

  def _render(subwindow):
    subwindow.box()
    subwindow.addstr(0, 0, 'Page %i Commands:' % (control.get_page() + 1), HIGHLIGHT)

    for i, option in enumerate(handlers):
      if i / 2 >= subwindow.height - 2:
        break

      # Entries are shown in the form '<key>: <description>[ (<selection>)]',
      # such as...
      #
      #   u: duplicate log entries (hidden)

      x = 2 if i % 2 == 0 else 41
      y = (i / 2) + 1

      x = subwindow.addstr(x, y, option.key, BOLD)
      x = subwindow.addstr(x, y, ': ' + option.description)

      if option.current:
        x = subwindow.addstr(x, y, ' (')
        x = subwindow.addstr(x, y, option.current, BOLD)
        x = subwindow.addstr(x, y, ')')

    # tells user to press a key if the lower left is unoccupied

    if len(handlers) < 13 and subwindow.height == 9:
      subwindow.addstr(2, 7, 'Press any key...')

  with nyx.curses.CURSES_LOCK:
    nyx.curses.draw(_render, top = control.header_panel().get_height(), width = 80, height = 9)
    keypress = nyx.curses.key_input()

  if keypress.is_selection() or keypress.is_scroll() or keypress.match('left', 'right'):
    return None
  else:
    return keypress


def show_about():
  """
  Presents a popup with author and version information.
  """

  def _render(subwindow):
    subwindow.box()
    subwindow.addstr(0, 0, 'About:', HIGHLIGHT)
    subwindow.addstr(2, 1, 'Nyx, version %s (released %s)' % (nyx.__version__, nyx.__release_date__), BOLD)
    subwindow.addstr(4, 2, 'Written by Damian Johnson (atagar@torproject.org)')
    subwindow.addstr(4, 3, 'Project page: %s' % nyx.__url__)
    subwindow.addstr(2, 5, 'Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)')
    subwindow.addstr(2, 7, 'Press any key...')

  with nyx.curses.CURSES_LOCK:
    nyx.curses.draw(_render, top = nyx.controller.get_controller().header_panel().get_height(), width = 80, height = 9)
    nyx.curses.key_input()


def show_counts(title, counts, fill_char = ' '):
  """
  Provides a dialog with bar graphs and percentages for the given set of
  counts. Pressing any key closes the dialog.

  :param str title: dialog title
  :param dict counts: mapping of labels to their value
  :param str fill_char: character to use for rendering the bar graph
  """

  def _render_no_stats(subwindow):
    subwindow.box()
    subwindow.addstr(0, 0, title, HIGHLIGHT)
    subwindow.addstr(2, 1, NO_STATS_MSG, CYAN, BOLD)

  def _render_stats(subwindow):
    key_width, val_width, value_total = 3, 1, 0

    for k, v in counts.items():
      key_width = max(key_width, len(k))
      val_width = max(val_width, len(str(v)))
      value_total += v

    subwindow.box()
    subwindow.addstr(0, 0, title, HIGHLIGHT)

    graph_width = subwindow.width - key_width - val_width - 11  # border, extra spaces, and percentage column
    sorted_counts = sorted(counts.iteritems(), key = operator.itemgetter(1), reverse = True)

    for y, (k, v) in enumerate(sorted_counts):
      label = '%s %s (%-2i%%)' % (k.ljust(key_width), str(v).rjust(val_width), v * 100 / value_total)
      x = subwindow.addstr(2, y + 1, label, GREEN, BOLD)

      for j in range(graph_width * v / value_total):
        subwindow.addstr(x + j + 1, y + 1, fill_char, RED, HIGHLIGHT)

    subwindow.addstr(2, subwindow.height - 2, 'Press any key...')

  top = nyx.controller.get_controller().header_panel().get_height()

  with nyx.curses.CURSES_LOCK:
    if not counts:
      nyx.curses.draw(_render_no_stats, top = top, width = len(NO_STATS_MSG) + 4, height = 3)
    else:
      nyx.curses.draw(_render_stats, top = top, width = 80, height = 4 + max(1, len(counts)))

    nyx.curses.key_input()


def show_selector(title, options, previous_selection):
  """
  Provides list of items the user can choose from.

  :param str title: dialog title
  :param list options: options that can be selected from
  :param str previous_selection: previously selected option

  :returns: **str** of selection or **previous_selection** if dialog is canceled
  """

  selected_index = options.index(previous_selection) if previous_selection in options else 0
  top = nyx.controller.get_controller().header_panel().get_height()

  def _render(subwindow):
    subwindow.box()
    subwindow.addstr(0, 0, title, HIGHLIGHT)

    for i, option in enumerate(options):
      if option == previous_selection:
        subwindow.addstr(2, i + 1, '> ')

      attr = HIGHLIGHT if i == selected_index else NORMAL
      subwindow.addstr(4, i + 1, ' %s ' % option, attr)

  with nyx.curses.CURSES_LOCK:
    while True:
      nyx.curses.draw(lambda subwindow: subwindow.addstr(0, 0, ' ' * 500), top = top, height = 1)  # hides title below us
      nyx.curses.draw(_render, top = top, width = max(map(len, options)) + 9, height = len(options) + 2)
      key = nyx.curses.key_input()

      if key.match('up'):
        selected_index = max(0, selected_index - 1)
      elif key.match('down'):
        selected_index = min(len(options) - 1, selected_index + 1)
      elif key.is_selection():
        return options[selected_index]
      elif key.match('esc'):
        return previous_selection


def show_sort_dialog(title, options, previous_order, option_colors):
  """
  Provides sorting dialog of the form...

    Current Order: <previous order>
    New Order: <selected options>

    <option 1>    <option 2>    <option 3>   Cancel

  :param str title: dialog title
  :param list options: sort options to be provided
  :param list previous_order: previous ordering
  :param dict option_colors: mapping of options to their color

  :returns: **list** of the new sort order or **None** if dialog is canceled
  """

  new_order = []
  cursor_index = 0
  shown_options = list(options) + ['Cancel']

  def _draw_selection(subwindow, y, label, selection):
    x = subwindow.addstr(2, y, label, BOLD)

    for i, option in enumerate(selection):
      x = subwindow.addstr(x, y, option, option_colors.get(option, WHITE), BOLD)

      if i < len(selection) - 1:
        x = subwindow.addstr(x, y, ', ', BOLD)

  def _render(subwindow):
    subwindow.box()
    subwindow.addstr(0, 0, title, HIGHLIGHT)

    _draw_selection(subwindow, 1, 'Current Order: ', previous_order)
    _draw_selection(subwindow, 2, 'New Order: ', new_order)

    # presents remaining options, each row having up to four options

    for i, option in enumerate(shown_options):
      attr = HIGHLIGHT if i == cursor_index else NORMAL
      subwindow.addstr((i % 4) * 19 + 2, (i / 4) + 4, option, attr)

  with nyx.curses.CURSES_LOCK:
    while len(new_order) < len(previous_order):
      nyx.curses.draw(_render, top = nyx.controller.get_controller().header_panel().get_height(), width = 80, height = 9)
      key = nyx.curses.key_input()

      if key.match('left'):
        cursor_index = max(0, cursor_index - 1)
      elif key.match('right'):
        cursor_index = min(len(shown_options) - 1, cursor_index + 1)
      elif key.match('up'):
        cursor_index = max(0, cursor_index - 4)
      elif key.match('down'):
        cursor_index = min(len(shown_options) - 1, cursor_index + 4)
      elif key.is_selection():
        selection = shown_options[cursor_index]

        if selection == 'Cancel':
          return None
        else:
          new_order.append(selection)
          shown_options.remove(selection)
          cursor_index = min(cursor_index, len(shown_options) - 1)
      elif key.match('esc'):
        return None

  return new_order


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

    with popup_window(1, -1) as (title_erase, _, _):
      title_erase.addstr(0, 0, ' ' * 500)  # hide title of the panel below us

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

  controller = nyx.tor_controller()
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

  popup.draw_box()
  popup.addstr(0, 0, title, HIGHLIGHT)
  popup.win.refresh()
