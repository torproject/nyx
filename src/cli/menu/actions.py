"""
Generates the menu for arm, binding options with their related actions.
"""

import cli.menu.item

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


