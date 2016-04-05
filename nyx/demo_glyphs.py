#!/usr/bin/env python
# Copyright 2014-2016, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Displays all ACS options with their corresponding representation. These are
undocumented in the pydocs. For more information see the following man page:

http://www.mkssoftware.com/docs/man5/terminfo.5.asp
"""

import curses


def main():
  try:
    curses.wrapper(_show_glyphs)
  except KeyboardInterrupt:
    pass  # quit


def _show_glyphs(stdscr):
  """
  Renders a chart with the ACS glyphs.
  """

  try:
    curses.use_default_colors()  # allow semi-transparent backgrounds
  except curses.error:
    pass

  try:
    curses.curs_set(0)  # attempt to make the cursor invisible
  except curses.error:
    pass

  height, width = stdscr.getmaxyx()
  columns = width / 30

  if columns == 0:
    return  # not wide enough to show anything

  # mapping of keycodes to their ACS option names (for instance, ACS_LTEE)

  acs_options = dict((v, k) for (k, v) in curses.__dict__.items() if k.startswith('ACS_'))

  stdscr.addstr(0, 0, 'Curses Glyphs:', curses.A_STANDOUT)
  x, y = 0, 2

  for keycode in sorted(acs_options.keys()):
    stdscr.addstr(y, x * 30, '%s (%i)' % (acs_options[keycode], keycode))
    stdscr.addch(y, (x * 30) + 25, keycode)

    x += 1

    if x >= columns:
      x, y = 0, y + 1

      if y >= height:
        break

  stdscr.getch()  # quit on keyboard input


if __name__ == '__main__':
  main()
