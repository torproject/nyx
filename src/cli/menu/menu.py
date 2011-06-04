
import curses

import cli.popups
import cli.controller
import cli.menu.item

from util import uiTools

def makeMenu():
  """
  Constructs the base menu and all of its contents.
  """
  
  baseMenu = cli.menu.item.Submenu("")
  
  fileMenu = cli.menu.item.Submenu("File")
  fileMenu.add(cli.menu.item.MenuItem("Exit", None))
  baseMenu.add(fileMenu)
  
  logsMenu = cli.menu.item.Submenu("Logs")
  logsMenu.add(cli.menu.item.MenuItem("Events", None))
  logsMenu.add(cli.menu.item.MenuItem("Clear", None))
  logsMenu.add(cli.menu.item.MenuItem("Save", None))
  logsMenu.add(cli.menu.item.MenuItem("Filter", None))
  
  duplicatesSubmenu = cli.menu.item.Submenu("Duplicates")
  duplicatesSubmenu.add(cli.menu.item.MenuItem("Hidden", None))
  duplicatesSubmenu.add(cli.menu.item.MenuItem("Visible", None))
  logsMenu.add(duplicatesSubmenu)
  baseMenu.add(logsMenu)
  
  viewMenu = cli.menu.item.Submenu("View")
  viewMenu.add(cli.menu.item.MenuItem("Graph", None))
  viewMenu.add(cli.menu.item.MenuItem("Connections", None))
  viewMenu.add(cli.menu.item.MenuItem("Configuration", None))
  viewMenu.add(cli.menu.item.MenuItem("Configuration File", None))
  baseMenu.add(viewMenu)
  
  graphMenu = cli.menu.item.Submenu("Graph")
  graphMenu.add(cli.menu.item.MenuItem("Stats", None))
  
  sizeSubmenu = cli.menu.item.Submenu("Size")
  sizeSubmenu.add(cli.menu.item.MenuItem("Increase", None))
  sizeSubmenu.add(cli.menu.item.MenuItem("Decrease", None))
  graphMenu.add(sizeSubmenu)
  
  graphMenu.add(cli.menu.item.MenuItem("Update Interval", None))
  
  boundsSubmenu = cli.menu.item.Submenu("Bounds")
  boundsSubmenu.add(cli.menu.item.MenuItem("Local Max", None))
  boundsSubmenu.add(cli.menu.item.MenuItem("Global Max", None))
  boundsSubmenu.add(cli.menu.item.MenuItem("Tight", None))
  graphMenu.add(boundsSubmenu)
  baseMenu.add(graphMenu)
  
  connectionsMenu = cli.menu.item.Submenu("Connections")
  connectionsMenu.add(cli.menu.item.MenuItem("Identity", None))
  connectionsMenu.add(cli.menu.item.MenuItem("Resolver", None))
  connectionsMenu.add(cli.menu.item.MenuItem("Sort Order", None))
  baseMenu.add(connectionsMenu)
  
  configurationMenu = cli.menu.item.Submenu("Configuration")
  
  commentsSubmenu = cli.menu.item.Submenu("Comments")
  commentsSubmenu.add(cli.menu.item.MenuItem("Hidden", None))
  commentsSubmenu.add(cli.menu.item.MenuItem("Visible", None))
  configurationMenu.add(commentsSubmenu)
  
  configurationMenu.add(cli.menu.item.MenuItem("Reload", None))
  configurationMenu.add(cli.menu.item.MenuItem("Reset Tor", None))
  baseMenu.add(configurationMenu)
  
  return baseMenu

class MenuCursor:
  """
  Tracks selection and key handling in the menu.
  """
  
  def __init__(self, initialSelection):
    self._selection = initialSelection
    self._isDone = False
  
  def isDone(self):
    """
    Provides true if a selection has indicated that we should close the menu.
    False otherwise.
    """
    
    return self._isDone
  
  def getSelection(self):
    """
    Provides the currently selected menu item.
    """
    
    return self._selection
  
  def handleKey(self, key):
    isSelectionSubmenu = isinstance(self._selection, cli.menu.item.Submenu)
    selectionHierarchy = self._selection.getHierarchy()
    
    if uiTools.isSelectionKey(key):
      if isSelectionSubmenu:
        if not self._selection.isEmpty():
          self._selection = self._selection.getChildren()[0]
      else: self._isDone = self._selection.select()
    elif key == curses.KEY_UP:
      self._selection = self._selection.prev()
    elif key == curses.KEY_DOWN:
      self._selection = self._selection.next()
    elif key == curses.KEY_LEFT:
      if len(selectionHierarchy) <= 3:
        # shift to the previous main submenu
        prevSubmenu = selectionHierarchy[1].prev()
        self._selection = prevSubmenu.getChildren()[0]
      else:
        # go up a submenu level
        self._selection = self._selection.getParent()
    elif key == curses.KEY_RIGHT:
      if isSelectionSubmenu:
        # open submenu (same as making a selection)
        if not self._selection.isEmpty():
          self._selection = self._selection.getChildren()[0]
      else:
        # shift to the next main submenu
        nextSubmenu = selectionHierarchy[1].next()
        self._selection = nextSubmenu.getChildren()[0]
    elif key in (27, ord('m'), ord('M')):
      # close menu
      self._isDone = True

def showMenu():
  popup, _, _ = cli.popups.init(1, belowStatic = False)
  if not popup: return
  control = cli.controller.getController()
  
  try:
    # generates the menu and uses the initial selection of the first item in
    # the file menu
    menu = makeMenu()
    cursor = MenuCursor(menu.getChildren()[0].getChildren()[0])
    
    while not cursor.isDone():
      # sets the background color
      popup.win.clear()
      popup.win.bkgd(' ', curses.A_STANDOUT | uiTools.getColor("red"))
      selectionHierarchy = cursor.getSelection().getHierarchy()
      
      # renders the menu bar, noting where the open submenu is positioned
      drawLeft, selectionLeft = 0, 0
      
      for topLevelItem in menu.getChildren():
        drawFormat = curses.A_BOLD
        if topLevelItem == selectionHierarchy[1]:
          drawFormat |= curses.A_UNDERLINE
          selectionLeft = drawLeft
        
        drawLabel = " %s " % topLevelItem.getLabel()[1]
        popup.addstr(0, drawLeft, drawLabel, drawFormat)
        popup.addch(0, drawLeft + len(drawLabel), curses.ACS_VLINE)
        
        drawLeft += len(drawLabel) + 1
      
      # recursively shows opened submenus
      _drawSubmenu(cursor, 1, 1, selectionLeft)
      
      popup.win.refresh()
      
      key = control.getScreen().getch()
      cursor.handleKey(key)
      
      # redraws the rest of the interface if we're rendering on it again
      if not cursor.isDone():
        for panelImpl in control.getDisplayPanels():
          panelImpl.redraw(True)
  finally: cli.popups.finalize()

def _drawSubmenu(cursor, level, top, left):
  selectionHierarchy = cursor.getSelection().getHierarchy()
  
  # checks if there's nothing to display
  if len(selectionHierarchy) < level + 2: return
  
  # fetches the submenu and selection we're displaying
  submenu = selectionHierarchy[level]
  selection = selectionHierarchy[level + 1]
  
  # gets the size of the prefix, middle, and suffix columns
  allLabelSets = [entry.getLabel() for entry in submenu.getChildren()]
  prefixColSize = max([len(entry[0]) for entry in allLabelSets])
  middleColSize = max([len(entry[1]) for entry in allLabelSets])
  suffixColSize = max([len(entry[2]) for entry in allLabelSets])
  
  # formatted string so we can display aligned menu entries
  labelFormat = " %%-%is%%-%is%%-%is " % (prefixColSize, middleColSize, suffixColSize)
  menuWidth = len(labelFormat % ("", "", ""))
  
  popup, _, _ = cli.popups.init(len(submenu.getChildren()), menuWidth, top, left, belowStatic = False)
  if not popup: return
  
  try:
    # sets the background color
    popup.win.bkgd(' ', curses.A_STANDOUT | uiTools.getColor("red"))
    
    drawTop, selectionTop = 0, 0
    for menuItem in submenu.getChildren():
      if menuItem == selection:
        drawFormat = curses.A_BOLD | uiTools.getColor("white")
        selectionTop = drawTop
      else: drawFormat = curses.A_NORMAL
      
      popup.addstr(drawTop, 0, labelFormat % menuItem.getLabel(), drawFormat)
      drawTop += 1
    
    popup.win.refresh()
    
    # shows the next submenu
    _drawSubmenu(cursor, level + 1, top + selectionTop, left + menuWidth)
  finally: cli.popups.finalize()
  
