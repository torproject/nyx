"""
Generates the menu for arm, binding options with their related actions.
"""

import functools

import cli.controller
import cli.menu.item

from util import torTools, uiTools

def makeMenu():
  """
  Constructs the base menu and all of its contents.
  """
  
  baseMenu = cli.menu.item.Submenu("")
  baseMenu.add(makeActionsMenu())
  baseMenu.add(makeViewMenu())
  
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

def makeActionsMenu():
  """
  Submenu consisting of...
    Close Menu
    Pause / Unpause
    Reset Tor
    Exit
  """
  
  control = cli.controller.getController()
  actionsMenu = cli.menu.item.Submenu("Actions")
  actionsMenu.add(cli.menu.item.MenuItem("Close Menu", None))
  
  if control.isPaused(): label, arg = "Unpause", False
  else: label, arg = "Pause", True
  actionsMenu.add(cli.menu.item.MenuItem(label, functools.partial(control.setPaused, arg)))
  
  actionsMenu.add(cli.menu.item.MenuItem("Reset Tor", torTools.getConn().reload))
  actionsMenu.add(cli.menu.item.MenuItem("Exit", control.quit))
  return actionsMenu

def makeViewMenu():
  """
  Submenu consisting of...
    [X] <Page 1>
    [ ] <Page 2>
    [ ] etc...
        Color (Submenu)
  """
  
  viewMenu = cli.menu.item.Submenu("View")
  control = cli.controller.getController()
  
  if control.getPageCount() > 0:
    pageGroup = cli.menu.item.SelectionGroup(control.setPage, control.getPage())
    
    for i in range(control.getPageCount()):
      pagePanels = control.getDisplayPanels(pageNumber = i, includeSticky = False)
      label = " / ".join([uiTools.camelCase(panel.getName()) for panel in pagePanels])
      
      viewMenu.add(cli.menu.item.SelectionMenuItem(label, pageGroup, i))
  
  if uiTools.isColorSupported():
    colorMenu = cli.menu.item.Submenu("Color")
    colorGroup = cli.menu.item.SelectionGroup(uiTools.setColorOverride, uiTools.getColorOverride())
    
    colorMenu.add(cli.menu.item.SelectionMenuItem("All", colorGroup, None))
    
    for color in uiTools.COLOR_LIST:
      colorMenu.add(cli.menu.item.SelectionMenuItem(uiTools.camelCase(color), colorGroup, color))
    
    viewMenu.add(colorMenu)
  
  return viewMenu

