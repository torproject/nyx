"""
A drop-down menu for sending actions to panels.
"""

import curses
from collections import namedtuple

import cli.controller
import popups
from util import log, panel, uiTools

LeafEntry = namedtuple('LeafEntry', ['title', 'callback'])
ParentEntry = namedtuple('ParentEntry', ['title', 'children'])

class Menu():
  """Displays a popup menu and sends keys to appropriate panels"""

  def __init__(self, entry=None):
    DEFAULT_ROOT = ParentEntry(title="Root", children=(
      LeafEntry(title="File"          , callback=self._callbackDefault),
      LeafEntry(title="Logs"          , callback=self._callbackDefault),
      LeafEntry(title="View"          , callback=self._callbackDefault),
      LeafEntry(title="Graph"         , callback=self._callbackDefault),
      LeafEntry(title="Connections"   , callback=self._callbackDefault),
      LeafEntry(title="Configuration" , callback=self._callbackDefault)))

    self._first = [0]
    self._selection = [0]

    if entry and isinstance(entry, ParentEntry):
      self._rootEntry = entry
    else:
      self._rootEntry = DEFAULT_ROOT

  def showMenu(self):
    popup, width, height = popups.init(height=3)
    if popup:
      try:
        while True:
          popup.win.erase()
          popup.win.box()

          self._drawTopLevel(popup, width, height)

          popup.win.refresh()

          control = cli.controller.getController()
          key = control.getScreen().getch()

          if key == curses.KEY_RIGHT:
            if len(self._selection) == 1:
              self._moveTopLevelRight(width)
          elif key == curses.KEY_LEFT:
            if len(self._selection) == 1:
              self._moveTopLevelLeft(width)
          elif uiTools.isSelectionKey(key):
            self._handleEvent()
            break

      finally:
        popups.finalize()

  def _calculateTopLevelWidths(self, width):
    titles = [menuItem.title for menuItem in self._rootEntry.children]

    # width per title is set according to the longest title
    titlewidth = max(map(len, titles)) + 2

    # total number of titles that can be printed in current width
    printable = min(width / titlewidth - 1, len(self._rootEntry.children))

    return (titlewidth, printable)

  def _moveTopLevelRight(self, width):
    _, printable = self._calculateTopLevelWidths(width)

    if self._selection[0] < printable - 1:
      self._selection[0] = self._selection[0] + 1
    else:
      self._selection[0] = 0
      if printable < len(self._rootEntry.children):
        self._first[0] = (self._first[0] + printable) % len(self._rootEntry.children)

    if self._first[0] + self._selection[0] == len(self._rootEntry.children):
      self._first[0] = 0
      self._selection[0] = 0

  def _moveTopLevelLeft(self, width):
    _, printable = self._calculateTopLevelWidths(width)

    if self._selection[0] > 0:
      self._selection[0] = self._selection[0] - 1
    else:
      self._first[0] = abs(self._first[0] - printable) % len(self._rootEntry.children)
      self._selection[0] = len(self._rootEntry.children) - self._first[0] - 1

    if self._selection[0] > printable:
      self._selection[0] = printable - 1

  def _drawTopLevel(self, popup, width, height):
    titlewidth, printable = self._calculateTopLevelWidths(width)
    children = self._rootEntry.children[self._first[0]:self._first[0] + printable]

    top = 1
    left = 1
    for (index, entry) in enumerate(children):
      titleformat = curses.A_STANDOUT if index == self._selection[0] else curses.A_NORMAL

      popup.addch(top, left, curses.ACS_VLINE)
      left = left + 1
      popup.addstr(top, left, entry.title.center(titlewidth), titleformat)
      left = left + titlewidth

    popup.addch(top, left, curses.ACS_VLINE)
    left = left + 1

  def _handleEvent(self):
    entry = self._rootEntry
    sums = [sum(values) for values in zip(self._first, self._selection)]

    for index in sums:
      if isinstance(entry, ParentEntry):
        entry = entry.children[index]
      else:
        break

        log.log(log.ERR, "first: %d" % self._first[0])
    if isinstance(entry, LeafEntry):
      entry.callback(entry)

  def _callbackDefault(self, entry):
    log.log(log.NOTICE, "%s selected" % entry.title)

