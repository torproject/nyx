"""
Generates the menu for arm, binding options with their related actions.
"""

import functools

import cli.controller
import cli.menu.item
import cli.graphing.graphPanel

from util import connections, torTools, uiTools

def makeMenu():
  """
  Constructs the base menu and all of its contents.
  """
  
  baseMenu = cli.menu.item.Submenu("")
  baseMenu.add(makeActionsMenu())
  baseMenu.add(makeViewMenu())
  
  control = cli.controller.getController()
  
  for pagePanel in control.getDisplayPanels(includeSticky = False):
    if pagePanel.getName() == "graph":
      baseMenu.add(makeGraphMenu(pagePanel))
    elif pagePanel.getName() == "log":
      baseMenu.add(makeLogMenu(pagePanel))
    elif pagePanel.getName() == "connections":
      baseMenu.add(makeConnectionsMenu(pagePanel))
  
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

def makeGraphMenu(graphPanel):
  """
  Submenu for the graph panel, consisting of...
    [X] <Stat 1>
    [ ] <Stat 2>
    [ ] <Stat 2>
        Resize...
        Interval (Submenu)
        Bounds (Submenu)
  
  Arguments:
    graphPanel - instance of the graph panel
  """
  
  graphMenu = cli.menu.item.Submenu("Graph")
  
  # stats options
  statGroup = cli.menu.item.SelectionGroup(graphPanel.setStats, graphPanel.getStats())
  availableStats = graphPanel.stats.keys()
  availableStats.sort()
  
  for statKey in ["None"] + availableStats:
    label = uiTools.camelCase(statKey, divider = " ")
    statKey = None if statKey == "None" else statKey
    graphMenu.add(cli.menu.item.SelectionMenuItem(label, statGroup, statKey))
  
  # resizing option
  graphMenu.add(cli.menu.item.MenuItem("Resize...", graphPanel.resizeGraph))
  
  # interval submenu
  intervalMenu = cli.menu.item.Submenu("Interval")
  intervalGroup = cli.menu.item.SelectionGroup(graphPanel.setUpdateInterval, graphPanel.getUpdateInterval())
  
  for i in range(len(cli.graphing.graphPanel.UPDATE_INTERVALS)):
    label = cli.graphing.graphPanel.UPDATE_INTERVALS[i][0]
    label = uiTools.camelCase(label, divider = " ")
    intervalMenu.add(cli.menu.item.SelectionMenuItem(label, intervalGroup, i))
  
  graphMenu.add(intervalMenu)
  
  # bounds submenu
  boundsMenu = cli.menu.item.Submenu("Bounds")
  boundsGroup = cli.menu.item.SelectionGroup(graphPanel.setBoundsType, graphPanel.getBoundsType())
  
  for boundsType in cli.graphing.graphPanel.Bounds.values():
    boundsMenu.add(cli.menu.item.SelectionMenuItem(boundsType, boundsGroup, boundsType))
  
  graphMenu.add(boundsMenu)
  
  return graphMenu

def makeLogMenu(logPanel):
  """
  Submenu for the log panel, consisting of...
    Events...
    Snapshot...
    Clear
    Show / Hide Duplicates
    Filter (Submenu)
  
  Arguments:
    logPanel - instance of the log panel
  """
  
  logMenu = cli.menu.item.Submenu("Log")
  
  logMenu.add(cli.menu.item.MenuItem("Events...", logPanel.showEventSelectionPrompt))
  logMenu.add(cli.menu.item.MenuItem("Snapshot...", logPanel.showSnapshotPrompt))
  logMenu.add(cli.menu.item.MenuItem("Clear", logPanel.clear))
  
  if logPanel.showDuplicates: label, arg = "Hide", False
  else: label, arg = "Show", True
  logMenu.add(cli.menu.item.MenuItem("%s Duplicates" % label, functools.partial(logPanel.setDuplicateVisability, arg)))
  
  # filter submenu
  filterMenu = cli.menu.item.Submenu("Filter")
  filterGroup = cli.menu.item.SelectionGroup(logPanel.makeFilterSelection, logPanel.getFilter())
  
  filterMenu.add(cli.menu.item.SelectionMenuItem("None", filterGroup, None))
  
  for option in logPanel.filterOptions:
    filterMenu.add(cli.menu.item.SelectionMenuItem(option, filterGroup, option))
  
  filterMenu.add(cli.menu.item.MenuItem("New...", logPanel.showFilterPrompt))
  logMenu.add(filterMenu)
  
  return logMenu

def makeConnectionsMenu(connPanel):
  """
  Submenu for the connections panel, consisting of...
    [X] IP Address
    [ ] Fingerprint
    [ ] Nickname
        Sorting...
        Resolver (Submenu)
  
  Arguments:
    connPanel - instance of the connections panel
  """
  
  connectionsMenu = cli.menu.item.Submenu("Connections")
  
  # listing options
  listingGroup = cli.menu.item.SelectionGroup(connPanel.setListingType, connPanel.getListingType())
  
  listingOptions = cli.connections.entries.ListingType.values()
  listingOptions.remove(cli.connections.entries.ListingType.HOSTNAME)
  
  for option in listingOptions:
    connectionsMenu.add(cli.menu.item.SelectionMenuItem(option, listingGroup, option))
  
  # sorting option
  connectionsMenu.add(cli.menu.item.MenuItem("Sorting...", connPanel.showSortDialog))
  
  # resolver submenu
  connResolver = connections.getResolver("tor")
  resolverMenu = cli.menu.item.Submenu("Resolver")
  resolverGroup = cli.menu.item.SelectionGroup(connResolver.setOverwriteResolver, connResolver.getOverwriteResolver())
  
  resolverMenu.add(cli.menu.item.SelectionMenuItem("auto", resolverGroup, None))
  
  for option in connections.Resolver.values():
    resolverMenu.add(cli.menu.item.SelectionMenuItem(option, resolverGroup, option))
  
  connectionsMenu.add(resolverMenu)
  
  return connectionsMenu

