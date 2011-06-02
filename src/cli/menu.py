"""
A drop-down menu for sending actions to panels.
"""

import curses

import cli.controller
import popups

from cli.graphing.graphPanel import Bounds as GraphBounds
from util import log, panel, uiTools, menuItem

PARENTLEVEL, TOPLEVEL = (-1, 0)

class Menu():
  """Displays a popup menu and sends keys to appropriate panels"""

  def __init__(self, item=None):
    DEFAULT_ROOT = menuItem.MenuItem(label="Root", children=(
      menuItem.MenuItem(label="File", children=(
        menuItem.MenuItem(label="Exit",
                          callback=lambda item: self._callbackReturnKey(ord('q'))),)),
      menuItem.MenuItem(label="Logs", children=(
        menuItem.MenuItem(label="Events",
                          callback=lambda item: self._callbackPressKey('log', ord('e'))),
        menuItem.MenuItem(label="Clear",
                          callback=lambda item: self._callbackPressKey('log', ord('c'))),
        menuItem.MenuItem(label="Save",
                          callback=lambda item: self._callbackPressKey('log', ord('a'))),
        menuItem.MenuItem(label="Filter",
                          callback=lambda item: self._callbackPressKey('log', ord('f'))),
        menuItem.MenuItem(label="Duplicates", children=(
          menuItem.MenuItem(label="Hidden",
                            callback=lambda item: self._callbackSet('log', 'showDuplicates', False, ord('u')),
                            enabled=lambda: self._getItemEnabled('log', 'showDuplicates', False)),
          menuItem.MenuItem(label="Visible",
                            callback=lambda item: self._callbackSet('log', 'showDuplicates', True, ord('u')),
                            enabled=lambda: self._getItemEnabled('log', 'showDuplicates', True)),
          )))),
      menuItem.MenuItem(label="View", children=(
        menuItem.MenuItem(label="Graph",
                          callback=lambda item: self._callbackView('graph')),
        menuItem.MenuItem(label="Connections",
                          callback=lambda item: self._callbackView('conn')),
        menuItem.MenuItem(label="Configuration",
                          callback=lambda item: self._callbackView('configState')),
        menuItem.MenuItem(label="Configuration File",
                          callback=lambda item: self._callbackView('configFile')),)),
      menuItem.MenuItem(label="Graph", children=(
        menuItem.MenuItem(label="Stats",
                          callback=lambda item: self._callbackPressKey('graph', ord('s'))),
        menuItem.MenuItem(label="Size", children=(
          menuItem.MenuItem(label="Increase",
                            callback=lambda item: self._callbackPressKey('graph', ord('m'))),
          menuItem.MenuItem(label="Decrease",
                            callback=lambda item: self._callbackPressKey('graph', ord('n'))),
        )),
        menuItem.MenuItem(label="Update Interval",
                            callback=lambda item: self._callbackPressKey('graph', ord('i'))),
        menuItem.MenuItem(label="Bounds", children=(
          menuItem.MenuItem(label="Local Max",
                            callback=lambda item: self._callbackSet('graph', 'bounds', GraphBounds.LOCAL_MAX, ord('b')),
                            enabled=lambda: self._getItemEnabled('graph', 'bounds', GraphBounds.LOCAL_MAX)),
          menuItem.MenuItem(label="Global Max",
                            callback=lambda item: self._callbackSet('graph', 'bounds', GraphBounds.GLOBAL_MAX, ord('b')),
                            enabled=lambda: self._getItemEnabled('graph', 'bounds', GraphBounds.GLOBAL_MAX)),
          menuItem.MenuItem(label="Tight",
                            callback=lambda item: self._callbackSet('graph', 'bounds', GraphBounds.TIGHT, ord('b')),
                            enabled=lambda: self._getItemEnabled('graph', 'bounds', GraphBounds.TIGHT)),
        )),)),
      menuItem.MenuItem(label="Connections", children=(
        menuItem.MenuItem(label="Identity",
                            callback=lambda item: self._callbackPressKey('conn', ord('l'))),
        menuItem.MenuItem(label="Resolver",
                            callback=lambda item: self._callbackPressKey('conn', ord('u'))),
        menuItem.MenuItem(label="Sort Order",
                            callback=lambda item: self._callbackPressKey('conn', ord('s'))),
        )),
      menuItem.MenuItem(label="Configuration" , children=(
        menuItem.MenuItem(label="Comments", children=(
          menuItem.MenuItem(label="Hidden",
                            callback=lambda item: self._callbackSet('configFile', 'stripComments', True, ord('s')),
                            enabled=lambda: self._getItemEnabled('configFile', 'stripComments', True)),
          menuItem.MenuItem(label="Visible",
                            callback=lambda item: self._callbackSet('configFile', 'stripComments', False, ord('s')),
                            enabled=lambda: self._getItemEnabled('configFile', 'stripComments', False)),
        )),
        menuItem.MenuItem(label="Reload",
                          callback=lambda item: self._callbackPressKey('configFile', ord('r'))),
        menuItem.MenuItem(label="Reset Tor",
                          callback=lambda item: self._callbackReturnKey(ord('x'))),))
      ))

    self._first = [0]
    self._selection = [0]

    if item and item.isParent():
      self._rootItem = item
    else:
      self._rootItem = DEFAULT_ROOT

  def showMenu(self, keys=[]):
    keys.reverse()
    returnkeys = []

    popup, width, height = popups.init(height=3)
    if popup:
      try:
        while True:
          popup.win.erase()
          popup.win.box()

          self._drawTopLevel(popup, width, height)

          popup.win.refresh()

          control = cli.controller.getController()

          if keys == []:
            key = control.getScreen().getch()
          else:
            key = keys.pop()

          if key == curses.KEY_RIGHT:
            self._moveTopLevelRight(width)
          elif key == curses.KEY_LEFT:
            self._moveTopLevelLeft(width)
          elif key == curses.KEY_DOWN:
            cascaded, returnkeys = self._cascadeNLevel()
            break
          elif key == 27:
            break
          elif uiTools.isSelectionKey(key):
            self._handleEvent()
            break

      finally:
        popups.finalize()

    return returnkeys

  def _appendLevel(self):
    self._first.append(0)
    self._selection.append(0)

  def _removeLevel(self):
    self._first.pop()
    self._selection.pop()

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

  def _cascadeNLevel(self):
    parent = self._getCurrentItem()

    if parent.isLeaf():
      return (False, [])

    self._appendLevel()

    labelwidth = self._calculateNLevelWidths(level=PARENTLEVEL)
    printable = self._calculateNLevelHeights(level=PARENTLEVEL)

    toplabelwidth, _ = self._calculateTopLevelWidths()
    left = (toplabelwidth + 2) * self._selection[TOPLEVEL]

    popup, width, height = popups.init(height=printable+2, width=labelwidth+2, top=2, left=left)

    while self._getCurrentItem().isEnabled() == False:
      self._moveNLevelDown(height)

    if popup.win:
      returnkeys = []
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
            cascaded, returnkeys = self._cascadeNLevel()
            if cascaded == False:
              index = self._first[TOPLEVEL] + self._selection[TOPLEVEL] + 1
              returnkeys.append(ord('m'))
              for i in range(index):
                returnkeys.append(curses.KEY_RIGHT)
              returnkeys.append(curses.KEY_DOWN)
            break
          elif key == curses.KEY_LEFT:
            index = self._first[TOPLEVEL] + self._selection[TOPLEVEL] - 1
            index = index % self._rootItem.getChildrenCount()
            returnkeys.append(ord('m'))
            for i in range(index):
              returnkeys.append(curses.KEY_RIGHT)
            returnkeys.append(curses.KEY_DOWN)
            break
          elif key == 27:
            self._removeLevel()
            break
          elif uiTools.isSelectionKey(key):
            returnkey = self._handleEvent()
            if returnkey:
                returnkeys.append(returnkey)
            self._removeLevel()
            break

      finally:
        popups.finalize()

      return (True, returnkeys)

    return (False, [])

  def _drawNLevel(self, popup, width, height):
    printable = self._calculateNLevelHeights(level=PARENTLEVEL)
    parent = self._getCurrentItem(level=PARENTLEVEL)
    children = parent.getChildren()[self._first[PARENTLEVEL]:self._first[PARENTLEVEL] + printable]

    top = 1
    left = 1
    for (index, item) in enumerate(children):
      labelformat = curses.A_STANDOUT if index == self._selection[PARENTLEVEL] else curses.A_NORMAL

      if not item.isEnabled():
        labelformat = labelformat | uiTools.getColor('yellow')

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

    while self._getCurrentItem().isEnabled() == False:
      self._moveNLevelDown(height)

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

    while self._getCurrentItem().isEnabled() == False:
      self._moveNLevelUp(height)

  def _handleEvent(self):
    item = self._getCurrentItem()

    if item.isLeaf():
      return item.select()
    else:
      self._cascadeNLevel()

  def _callbackDefault(self, item):
    log.log(log.NOTICE, "%s selected" % item.getLabel())

  def _callbackView(self, panelname):
    control = cli.controller.getController()

    start = control.getPage()
    panels = control.getDisplayPanels(includeSticky=False)
    panelnames = [panel.getName() for panel in panels]
    while not panelname in panelnames:
      control.nextPage()
      panels = control.getDisplayPanels(includeSticky=False)
      panelnames = [panel.getName() for panel in panels]

      if control.getPage() == start:
        log.log(log.ERR, "Panel %s not found" % panelname)
        break

  def _getItemEnabled(self, panel, attr, value):
    control = cli.controller.getController()
    if control:
      panel = control.getPanel(panel)

      if panel:
        return getattr(panel, attr, None) != value

      return False

  def _callbackSet(self, panel, attr, value, key=None):
    control = cli.controller.getController()
    panel = control.getPanel(panel)

    panelattr = getattr(panel, attr, None)

    if panelattr != None:
      if hasattr(panelattr, '__call__'):
        panelattr(value)
      elif panelattr != value and key != None:
        start = panelattr
        while panelattr != value:
          panel.handleKey(key)
          panelattr = getattr(panel, attr, None)
          if panelattr == start:
            log.log(log.ERR, "Could not set %s.%s" % (panel, attr))
            break

  def _callbackPressKey(self, panel, key):
    control = cli.controller.getController()
    panel = control.getPanel(panel)
    panel.handleKey(key)

  def _callbackReturnKey(self, key):
    return key

