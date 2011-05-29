"""
A drop-down menu for sending actions to panels.
"""

import curses

import cli.controller
import popups
from util import log, panel, uiTools, menuItem

TOPLEVEL = 0

class Menu():
  """Displays a popup menu and sends keys to appropriate panels"""

  def __init__(self, item=None):
    DEFAULT_ROOT = menuItem.MenuItem(label="Root", children=(
      menuItem.MenuItem(label="File"          , callback=self._callbackDefault),
      menuItem.MenuItem(label="Logs"          , callback=self._callbackDefault),
      menuItem.MenuItem(label="View"          , callback=self._callbackDefault),
      menuItem.MenuItem(label="Graph"         , callback=self._callbackDefault),
      menuItem.MenuItem(label="Connections"   , callback=self._callbackDefault),
      menuItem.MenuItem(label="Configuration" , callback=self._callbackDefault)))

    self._first = [0]
    self._selection = [0]

    if item and item.isParent():
      self._rootItem = item
    else:
      self._rootItem = DEFAULT_ROOT

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
    labels = [menuItem.getLabel() for menuItem in self._rootItem.getChildren()]

    # width per label is set according to the longest label
    labelwidth = max(map(len, labels)) + 2

    # total number of labels that can be printed in current width
    printable = min(width / labelwidth - 1, self._rootItem.getChildrenCount())

    return (labelwidth, printable)

  def _moveTopLevelRight(self, width):
    _, printable = self._calculateTopLevelWidths(width)

    if self._selection[TOPLEVEL] < printable - 1:
      self._selection[TOPLEVEL] = self._selection[TOPLEVEL] + 1
    else:
      self._selection[TOPLEVEL] = 0
      if printable < self._rootItem.getChildrenCount():
        self._first[TOPLEVEL] = (self._first[TOPLEVEL] + printable) % self._rootItem.getChildrenCount()

    if self._first[TOPLEVEL] + self._selection[TOPLEVEL] == self._rootItem.getChildrenCount():
      self._first[TOPLEVEL] = 0
      self._selection[TOPLEVEL] = 0

  def _moveTopLevelLeft(self, width):
    _, printable = self._calculateTopLevelWidths(width)

    if self._selection[TOPLEVEL] > 0:
      self._selection[TOPLEVEL] = self._selection[TOPLEVEL] - 1
    else:
      if self._first[TOPLEVEL] == 0:
        self._first[TOPLEVEL] = (self._rootItem.getChildrenCount() / printable) * printable
      else:
        self._first[TOPLEVEL] = abs(self._first[TOPLEVEL] - printable) % self._rootItem.getChildrenCount()
      self._selection[TOPLEVEL] = self._rootItem.getChildrenCount() - self._first[TOPLEVEL] - 1

    if self._selection[TOPLEVEL] > printable:
      self._selection[TOPLEVEL] = printable - 1

  def _drawTopLevel(self, popup, width, height):
    labelwidth, printable = self._calculateTopLevelWidths(width)
    children = self._rootItem.getChildren()[self._first[TOPLEVEL]:self._first[TOPLEVEL] + printable]

    top = 1
    left = 1
    for (index, item) in enumerate(children):
      labelformat = curses.A_STANDOUT if index == self._selection[TOPLEVEL] else curses.A_NORMAL

      popup.addch(top, left, curses.ACS_VLINE)
      left = left + 1
      popup.addstr(top, left, item.getLabel().center(labelwidth), labelformat)
      left = left + labelwidth

    popup.addch(top, left, curses.ACS_VLINE)
    left = left + 1

  def _handleEvent(self):
    item = self._rootItem
    sums = [sum(values) for values in zip(self._first, self._selection)]

    for index in sums:
      if item.isParent():
        item = item.getChildren()[index]
      else:
        break

    if item.isLeaf():
      item.select()

  def _callbackDefault(self, item):
    log.log(log.NOTICE, "%s selected" % item.getLabel())

