"""
Functions for displaying popups in the interface.
"""

import curses
import operator

import nyx.controller

from nyx import __version__, __release_date__
from nyx.util import panel, ui_tools

NO_STATS_MSG = "Usage stats aren't available yet, press any key..."


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

      popup = panel.Panel(control.get_screen(), 'popup', top + sticky_height, left, height, width)
      popup.set_visible(True)

      # Redraws the popup to prepare a subwindow instance. If none is spawned then
      # the panel can't be drawn (for instance, due to not being visible).

      popup.redraw(True)

      if popup.win is not None:
        panel.CURSES_LOCK.acquire()
        return (popup, popup.max_x - 1, popup.max_y)
      else:
        return (None, 0, 0)

    def __exit__(self, exit_type, value, traceback):
      nyx.controller.get_controller().request_redraw()
      panel.CURSES_LOCK.release()

  return _Popup()


def input_prompt(msg, initial_value = ''):
  """
  Prompts the user to enter a string on the control line (which usually
  displays the page number and basic controls).

  Arguments:
    msg          - message to prompt the user for input with
    initial_value - initial value of the field
  """

  with panel.CURSES_LOCK:
    control = nyx.controller.get_controller()
    msg_panel = control.get_panel('msg')
    msg_panel.set_message(msg)
    msg_panel.redraw(True)
    user_input = msg_panel.getstr(0, len(msg), initial_value)
    control.set_msg()

    return user_input


def show_msg(msg, max_wait = -1, attr = curses.A_STANDOUT):
  """
  Displays a single line message on the control line for a set time. Pressing
  any key will end the message. This returns the key pressed.

  Arguments:
    msg     - message to be displayed to the user
    max_wait - time to show the message, indefinite if -1
    attr    - attributes with which to draw the message
  """

  with panel.CURSES_LOCK:
    control = nyx.controller.get_controller()
    control.set_msg(msg, attr, True)

    if max_wait == -1:
      curses.cbreak()
    else:
      curses.halfdelay(max_wait * 10)

    key_press = control.key_input()
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
      popup.addstr(0, 0, 'Page %i Commands:' % (control.get_page() + 1), curses.A_STANDOUT)

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

        popup.addstr(row, col, key, curses.A_BOLD)
        col += len(key)
        popup.addstr(row, col, description)
        col += len(description)

        if selection:
          popup.addstr(row, col, ' (')
          popup.addstr(row, col + 2, selection, curses.A_BOLD)
          popup.addstr(row, col + 2 + len(selection), ')')

      # tells user to press a key if the lower left is unoccupied

      if len(help_options) < 13 and height == 9:
        popup.addstr(7, 2, 'Press any key...')

      popup.win.refresh()
      curses.cbreak()
      exit_key = control.key_input()

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
      control = nyx.controller.get_controller()

      popup.win.box()
      popup.addstr(0, 0, 'About:', curses.A_STANDOUT)
      popup.addstr(1, 2, 'nyx, version %s (released %s)' % (__version__, __release_date__), curses.A_BOLD)
      popup.addstr(2, 4, 'Written by Damian Johnson (atagar@torproject.org)')
      popup.addstr(3, 4, 'Project page: www.atagar.com/nyx')
      popup.addstr(5, 2, 'Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)')
      popup.addstr(7, 2, 'Press any key...')
      popup.win.refresh()

      curses.cbreak()
      control.key_input()


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
      popup.addstr(1, 2, NO_STATS_MSG, curses.A_BOLD, 'cyan')
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
        x = popup.addstr(y + 1, 2, label, curses.A_BOLD, 'green')

        for j in range(graph_width * v / value_total):
          popup.addstr(y + 1, x + j + 1, ' ', curses.A_STANDOUT, 'red')

      popup.addstr(height - 2, 2, 'Press any key...')

    popup.win.box()
    popup.addstr(0, 0, title, curses.A_STANDOUT)
    popup.win.refresh()

    curses.cbreak()
    nyx.controller.get_controller().key_input()


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
      curses.cbreak()         # wait indefinitely for key presses (no timeout)

      selection_options = list(options)
      selection_options.append('Cancel')

      while len(new_selections) < len(old_selection):
        popup.win.erase()
        popup.win.box()
        popup.addstr(0, 0, title, curses.A_STANDOUT)

        _draw_sort_selection(popup, 1, 2, 'Current Order: ', old_selection, option_colors)
        _draw_sort_selection(popup, 2, 2, 'New Order: ', new_selections, option_colors)

        # presents remaining options, each row having up to four options with
        # spacing of nineteen cells

        row, col = 4, 0

        for i in range(len(selection_options)):
          option_format = curses.A_STANDOUT if cursor_location == i else curses.A_NORMAL
          popup.addstr(row, col * 19 + 2, selection_options[i], option_format)
          col += 1

          if col == 4:
            row, col = row + 1, 0

        popup.win.refresh()

        key = nyx.controller.get_controller().key_input()

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
        elif key == 27:
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

  popup.addstr(y, x, prefix, curses.A_BOLD)
  x += len(prefix)

  for i in range(len(options)):
    sort_type = options[i]
    sort_color = ui_tools.get_color(option_colors.get(sort_type, 'white'))
    popup.addstr(y, x, sort_type, sort_color | curses.A_BOLD)
    x += len(sort_type)

    # comma divider between options, if this isn't the last

    if i < len(options) - 1:
      popup.addstr(y, x, ', ', curses.A_BOLD)
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
    if popup:
      selection = old_selection if old_selection != -1 else 0

      # hides the title of the first panel on the page

      control = nyx.controller.get_controller()
      top_panel = control.get_display_panels(include_sticky = False)[0]
      top_panel.set_title_visible(False)
      top_panel.redraw(True)

      curses.cbreak()   # wait indefinitely for key presses (no timeout)

      while True:
        popup.win.erase()
        popup.win.box()
        popup.addstr(0, 0, title, curses.A_STANDOUT)

        for i in range(len(options)):
          label = options[i]
          format = curses.A_STANDOUT if i == selection else curses.A_NORMAL
          tab = '> ' if i == old_selection else '  '
          popup.addstr(i + 1, 2, tab)
          popup.addstr(i + 1, 4, ' %s ' % label, format)

        popup.win.refresh()

        key = control.key_input()

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
