"""
A drop-down menu for sending actions to panels.
"""

import curses
from collections import namedtuple
from operator import attrgetter

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

    self._selection = [0]
    self._rootEntry = (entry and isinstance(entry, ParentEntry)) and entry or DEFAULT_ROOT

  def draw(self):
    popup, width, height = popups.init(height=3)
    if popup:
      try:
        popup.win.box()

        while True:
          self._drawTopLevel(popup, width, height)

          popup.win.refresh()

          control = cli.controller.getController()
          key = control.getScreen().getch()

          if key == curses.KEY_RIGHT:
            if len(self._selection) == 1:
              # selection is on top menu
              self._selection[0] = (self._selection[0] + 1) % len(self._rootEntry.children)
          elif key == curses.KEY_LEFT:
            if len(self._selection) == 1:
              # selection is on top menu
              self._selection[0] = (self._selection[0] - 1) % len(self._rootEntry.children)
          elif uiTools.isSelectionKey(key):
            self._handleEvent()
            break

      finally:
        popups.finalize()

  def _drawTopLevel(self, popup, width, height):
    titles = map(attrgetter('title'), self._rootEntry.children)

    # width per title is set according to the longest title
    titlewidth = max(map(lambda title: len(title), titles)) + 2

    # total number of titles that can be printed in current width
    printable = width / titlewidth - 1

    top = 1
    left = 1
    for (index, entry) in enumerate(self._rootEntry.children[:printable]):
      titleformat = curses.A_NORMAL

      if index == self._selection[0]:
        titleformat = curses.A_STANDOUT

      popup.win.addch(top, left, curses.ACS_VLINE)
      left = left + 1
      popup.win.addstr(top, left, entry.title.center(titlewidth), titleformat)
      left = left + titlewidth

    popup.win.addch(top, left, curses.ACS_VLINE)
    left = left + 1

  def _handleEvent(self):
    entry = self._rootEntry

    for index in self._selection:
      if isinstance(entry, ParentEntry):
        entry = entry.children[index]
      else:
        break

    if isinstance(entry, LeafEntry):
      entry.callback(entry)

  def _callbackDefault(self, entry):
    log.log(log.NOTICE, "%s selected" % entry.title)

