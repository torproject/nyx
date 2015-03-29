"""
Provides a dialog with client locale or exiting port counts.
"""

import curses
import operator

import nyx.controller
import nyx.popups

from stem.util import connection, enum, log

CountType = enum.Enum("CLIENT_LOCALE", "EXIT_PORT")
EXIT_USAGE_WIDTH = 15


def showCountDialog(count_type, counts):
  """
  Provides a dialog with bar graphs and percentages for the given set of
  counts. Pressing any key closes the dialog.

  Arguments:
    count_type - type of counts being presented
    counts    - mapping of labels to counts
  """

  is_no_stats = not counts
  no_stats_msg = "Usage stats aren't available yet, press any key..."

  if is_no_stats:
    popup, width, height = nyx.popups.init(3, len(no_stats_msg) + 4)
  else:
    popup, width, height = nyx.popups.init(4 + max(1, len(counts)), 80)

  if not popup:
    return

  try:
    control = nyx.controller.get_controller()

    popup.win.box()

    # dialog title

    if count_type == CountType.CLIENT_LOCALE:
      title = "Client Locales"
    elif count_type == CountType.EXIT_PORT:
      title = "Exiting Port Usage"
    else:
      title = ""
      log.warn("Unrecognized count type: %s" % count_type)

    popup.addstr(0, 0, title, curses.A_STANDOUT)

    if is_no_stats:
      popup.addstr(1, 2, no_stats_msg, curses.A_BOLD, 'cyan')
    else:
      sorted_counts = sorted(counts.iteritems(), key=operator.itemgetter(1))
      sorted_counts.reverse()

      # constructs string formatting for the max key and value display width

      key_width, val_width, value_total = 3, 1, 0

      for k, v in sorted_counts:
        key_width = max(key_width, len(k))
        val_width = max(val_width, len(str(v)))
        value_total += v

      # extra space since we're adding usage informaion

      if count_type == CountType.EXIT_PORT:
        key_width += EXIT_USAGE_WIDTH

      label_format = "%%-%is %%%ii (%%%%%%-2i)" % (key_width, val_width)

      for i in range(height - 4):
        k, v = sorted_counts[i]

        # includes a port usage column

        if count_type == CountType.EXIT_PORT:
          usage = connection.port_usage(k)

          if usage:
            key_format = "%%-%is   %%s" % (key_width - EXIT_USAGE_WIDTH)
            k = key_format % (k, usage[:EXIT_USAGE_WIDTH - 3])

        label = label_format % (k, v, v * 100 / value_total)
        popup.addstr(i + 1, 2, label, curses.A_BOLD, 'green')

        # All labels have the same size since they're based on the max widths.
        # If this changes then this'll need to be the max label width.

        label_width = len(label)

        # draws simple bar graph for percentages

        fill_width = v * (width - 4 - label_width) / value_total

        for j in range(fill_width):
          popup.addstr(i + 1, 3 + label_width + j, " ", curses.A_STANDOUT, 'red')

      popup.addstr(height - 2, 2, "Press any key...")

    popup.win.refresh()

    curses.cbreak()
    control.key_input()
  finally:
    nyx.popups.finalize()
