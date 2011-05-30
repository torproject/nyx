"""
A drop-down menu for sending actions to panels.
"""

import curses

import cli.controller
import popups
from util import log, panel, uiTools, menuItem

PARENTLEVEL, TOPLEVEL = (-1, 0)

class Menu():
  """Displays a popup menu and sends keys to appropriate panels"""

  def __init__(self, item=None):
    DEFAULT_ROOT = menuItem.MenuItem(label="Root", children=(
      menuItem.MenuItem(label="File"          , children=(
        menuItem.MenuItem(label="Exit"                , callback=self._callbackDefault),)),
      menuItem.MenuItem(label="Logs"          , children=(
        menuItem.MenuItem(label="Events"              , callback=self._callbackDefault),
        menuItem.MenuItem(label="Clear"               , callback=self._callbackDefault),
        menuItem.MenuItem(label="Save"                , callback=self._callbackDefault),
        menuItem.MenuItem(label="Filter"              , callback=self._callbackDefault),
        menuItem.MenuItem(label="Duplicates"          , children=(
          menuItem.MenuItem(label="Hidden"            , callback=self._callbackDefault),
          menuItem.MenuItem(label="Visible"           , callback=self._callbackDefault),))
        )),
      menuItem.MenuItem(label="View"          , children=(
        menuItem.MenuItem(label="Graph"               , callback=self._callbackDefault),
        menuItem.MenuItem(label="Connections"         , callback=self._callbackDefault),
        menuItem.MenuItem(label="Configuration"       , callback=self._callbackDefault),
        menuItem.MenuItem(label="Configuration File"  , callback=self._callbackDefault),)),
      menuItem.MenuItem(label="Graph"         , children=(
        menuItem.MenuItem(label="Stats"               , children=(
          menuItem.MenuItem(label="Bandwidth"         , callback=self._callbackDefault),
          menuItem.MenuItem(label="Connections"       , callback=self._callbackDefault),
          menuItem.MenuItem(label="Resources"         , callback=self._callbackDefault),
        )),
        menuItem.MenuItem(label="Size"                , children=(
          menuItem.MenuItem(label="Increase"          , callback=self._callbackDefault),
          menuItem.MenuItem(label="Decrease"          , callback=self._callbackDefault),
        )),
        menuItem.MenuItem(label="Update Interval"     , children=(
          menuItem.MenuItem(label="Each second"       , callback=self._callbackDefault),
          menuItem.MenuItem(label="5 seconds"         , callback=self._callbackDefault),
          menuItem.MenuItem(label="30 seconds"        , callback=self._callbackDefault),
          menuItem.MenuItem(label="1 minute"          , callback=self._callbackDefault),
          menuItem.MenuItem(label="30 minutes"        , callback=self._callbackDefault),
          menuItem.MenuItem(label="Hourly"            , callback=self._callbackDefault),
          menuItem.MenuItem(label="Daily"             , callback=self._callbackDefault),
        )),
        menuItem.MenuItem(label="Bounds"              , children=(
          menuItem.MenuItem(label="Local Max"         , callback=self._callbackDefault),
          menuItem.MenuItem(label="Global Max"        , callback=self._callbackDefault),
          menuItem.MenuItem(label="Tight"             , callback=self._callbackDefault),
        )),)),
      menuItem.MenuItem(label="Connections"   , children=(
        menuItem.MenuItem(label="Identity"            , children=(
          menuItem.MenuItem(label="IP"                , callback=self._callbackDefault),
          menuItem.MenuItem(label="Fingerprints"      , callback=self._callbackDefault),
          menuItem.MenuItem(label="Nicknames"             , callback=self._callbackDefault),
        )),
        menuItem.MenuItem(label="Resolver"            , children=(
          menuItem.MenuItem(label="auto"              , callback=self._callbackDefault),
          menuItem.MenuItem(label="proc"              , callback=self._callbackDefault),
          menuItem.MenuItem(label="netstat"           , callback=self._callbackDefault),
          menuItem.MenuItem(label="ss"                , callback=self._callbackDefault),
          menuItem.MenuItem(label="lsof"              , callback=self._callbackDefault),
          menuItem.MenuItem(label="sockstat"          , callback=self._callbackDefault),
          menuItem.MenuItem(label="sockstat (bsd)"    , callback=self._callbackDefault),
          menuItem.MenuItem(label="procstat (bsd)"    , callback=self._callbackDefault),
        )),
        menuItem.MenuItem(label="Sort Order"          , callback=self._callbackDefault),)),
      menuItem.MenuItem(label="Configuration" , children=(
        menuItem.MenuItem(label="Comments"            , children=(
          menuItem.MenuItem(label="Hidden"            , callback=self._callbackDefault),
          menuItem.MenuItem(label="Visible"           , callback=self._callbackDefault),
        )),
        menuItem.MenuItem(label="Reload"              , callback=self._callbackDefault),
        menuItem.MenuItem(label="Reset Tor"           , callback=self._callbackDefault),))
      ))

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
            self._moveTopLevelRight(width)
          elif key == curses.KEY_LEFT:
            self._moveTopLevelLeft(width)
          elif key == curses.KEY_DOWN:
            self._showNLevel()
            break
          elif uiTools.isSelectionKey(key):
            self._handleEvent()
            break

      finally:
        popups.finalize()

  def _getCurrentTopLevelItem(self):
    index = self._first[TOPLEVEL] + self._selection[TOPLEVEL]
    return self._rootItem.getChildren()[index]

  def _getCurrentItem(self, level=0):
    item = self._rootItem
    if level == 0:
      sums = [sum(values) for values in zip(self._first, self._selection)]
    else:
      sums = [sum(values) for values in zip(self._first[:level], self._selection[:level])]

    for index in sums:
      if item.isParent():
        item = item.getChildren()[index]
      else:
        break

    return item

  def _calculateTopLevelWidths(self, width=0):
    labels = [menuItem.getLabel() for menuItem in self._rootItem.getChildren()]

    # width per label is set according to the longest label
    labelwidth = max(map(len, labels)) + 2

    # total number of labels that can be printed in supplied width
    printable = min(width / labelwidth - 1, self._rootItem.getChildrenCount())

    return (labelwidth, printable)

  def _calculateNLevelWidths(self, level=0):
    parent = self._getCurrentItem(level)

    if parent.isLeaf():
      return 0

    labels = [menuItem.getLabel() for menuItem in parent.getChildren()]

    labelwidth = max(map(len, labels))

    return labelwidth

  def _calculateNLevelHeights(self, height=0, level=0):
    control = cli.controller.getController()
    height, _ = control.getScreen().getmaxyx()
    topSize = sum(stickyPanel.getHeight() for stickyPanel in control.getStickyPanels())
    height = height - topSize

    parent = self._getCurrentItem(level)

    if parent.isLeaf():
      return 0

    printable = min(height - 4, parent.getChildrenCount())

    return printable if printable else parent.getChildrenCount()

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
        self._first[TOPLEVEL] = ((self._rootItem.getChildrenCount() / printable) * printable) % self._rootItem.getChildrenCount()
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

  def _showNLevel(self):
    self._first.append(0)
    self._selection.append(0)

    parent = self._getCurrentItem(level=PARENTLEVEL)

    if parent.isLeaf():
      return

    labelwidth = self._calculateNLevelWidths(level=PARENTLEVEL)
    printable = self._calculateNLevelHeights(level=PARENTLEVEL)

    toplabelwidth, _ = self._calculateTopLevelWidths()
    left = (toplabelwidth + 2) * self._selection[TOPLEVEL]

    popup, width, height = popups.init(height=printable+2, width=labelwidth+2, top=2, left=left)

    if popup.win:
      try:
        while True:
          popup.win.erase()
          popup.win.box()

          self._drawNLevel(popup, width, height)

          popup.win.refresh()

          control = cli.controller.getController()
          key = control.getScreen().getch()

          if key == curses.KEY_DOWN:
            self._moveNLevelDown(height)
          elif key == curses.KEY_UP:
            self._moveNLevelUp(height)
          elif key == curses.KEY_RIGHT:
            self._showNLevel()
            break
          elif uiTools.isSelectionKey(key):
            self._handleEvent()
            self._first.pop()
            self._selection.pop()
            break

      finally:
        popups.finalize()

  def _drawNLevel(self, popup, width, height):
    printable = self._calculateNLevelHeights(level=PARENTLEVEL)
    parent = self._getCurrentItem(level=PARENTLEVEL)
    children = parent.getChildren()[self._first[PARENTLEVEL]:self._first[PARENTLEVEL] + printable]

    top = 1
    left = 1
    for (index, item) in enumerate(children):
      labelformat = curses.A_STANDOUT if index == self._selection[PARENTLEVEL] else curses.A_NORMAL

      popup.addstr(top, left, item.getLabel(), labelformat)
      top = top + 1

  def _moveNLevelDown(self, height):
    printable = self._calculateNLevelHeights(level=PARENTLEVEL)
    parent = self._getCurrentItem(level=PARENTLEVEL)

    if self._selection[PARENTLEVEL] < printable - 1:
      self._selection[PARENTLEVEL] = self._selection[PARENTLEVEL] + 1
    else:
      self._selection[PARENTLEVEL] = 0
      if printable < parent.getChildrenCount():
        self._first[PARENTLEVEL] = (self._first[PARENTLEVEL] + printable) % parent.getChildrenCount()

    if self._first[PARENTLEVEL] + self._selection[PARENTLEVEL] == parent.getChildrenCount():
      self._first[PARENTLEVEL] = 0
      self._selection[PARENTLEVEL] = 0

  def _moveNLevelUp(self, height):
    printable = self._calculateNLevelHeights(level=PARENTLEVEL)
    parent = self._getCurrentItem(level=PARENTLEVEL)

    if self._selection[PARENTLEVEL] > 0:
      self._selection[PARENTLEVEL] = self._selection[PARENTLEVEL] - 1
    else:
      if self._first[PARENTLEVEL] == 0:
        self._first[PARENTLEVEL] = ((parent.getChildrenCount() / printable) * printable) % parent.getChildrenCount()
      else:
        self._first[PARENTLEVEL] = abs(self._first[PARENTLEVEL] - printable) % parent.getChildrenCount()
      self._selection[PARENTLEVEL] = parent.getChildrenCount() - self._first[PARENTLEVEL] - 1

    if self._selection[PARENTLEVEL] > printable:
      self._selection[PARENTLEVEL] = printable - 1

  def _handleEvent(self):
    item = self._getCurrentItem()

    if item.isLeaf():
      item.select()
    else:
      self._showNLevel()

  def _callbackDefault(self, item):
    log.log(log.NOTICE, "%s selected" % item.getLabel())

