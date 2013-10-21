"""
Generates the menu for arm, binding options with their related actions.
"""

import functools

import arm.popups
import arm.controller
import arm.menu.item
import arm.graphing.graphPanel
import arm.util.tracker

from arm.util import torTools, uiTools

import stem.util.connection

from stem.util import conf, str_tools

CONFIG = conf.config_dict("arm", {
  "features.log.showDuplicateEntries": False,
})

def makeMenu():
  """
  Constructs the base menu and all of its contents.
  """

  baseMenu = arm.menu.item.Submenu("")
  baseMenu.add(makeActionsMenu())
  baseMenu.add(makeViewMenu())

  control = arm.controller.getController()

  for pagePanel in control.getDisplayPanels(includeSticky = False):
    if pagePanel.getName() == "graph":
      baseMenu.add(makeGraphMenu(pagePanel))
    elif pagePanel.getName() == "log":
      baseMenu.add(makeLogMenu(pagePanel))
    elif pagePanel.getName() == "connections":
      baseMenu.add(makeConnectionsMenu(pagePanel))
    elif pagePanel.getName() == "configuration":
      baseMenu.add(makeConfigurationMenu(pagePanel))
    elif pagePanel.getName() == "torrc":
      baseMenu.add(makeTorrcMenu(pagePanel))

  baseMenu.add(makeHelpMenu())

  return baseMenu

def makeActionsMenu():
  """
  Submenu consisting of...
    Close Menu
    New Identity
    Pause / Unpause
    Reset Tor
    Exit
  """

  control = arm.controller.getController()
  conn = torTools.getConn()
  headerPanel = control.getPanel("header")
  actionsMenu = arm.menu.item.Submenu("Actions")
  actionsMenu.add(arm.menu.item.MenuItem("Close Menu", None))
  actionsMenu.add(arm.menu.item.MenuItem("New Identity", headerPanel.sendNewnym))

  if conn.isAlive():
    actionsMenu.add(arm.menu.item.MenuItem("Stop Tor", conn.shutdown))

  actionsMenu.add(arm.menu.item.MenuItem("Reset Tor", conn.reload))

  if control.isPaused(): label, arg = "Unpause", False
  else: label, arg = "Pause", True
  actionsMenu.add(arm.menu.item.MenuItem(label, functools.partial(control.setPaused, arg)))

  actionsMenu.add(arm.menu.item.MenuItem("Exit", control.quit))
  return actionsMenu

def makeViewMenu():
  """
  Submenu consisting of...
    [X] <Page 1>
    [ ] <Page 2>
    [ ] etc...
        Color (Submenu)
  """

  viewMenu = arm.menu.item.Submenu("View")
  control = arm.controller.getController()

  if control.getPageCount() > 0:
    pageGroup = arm.menu.item.SelectionGroup(control.setPage, control.getPage())

    for i in range(control.getPageCount()):
      pagePanels = control.getDisplayPanels(pageNumber = i, includeSticky = False)
      label = " / ".join([str_tools._to_camel_case(panel.getName()) for panel in pagePanels])

      viewMenu.add(arm.menu.item.SelectionMenuItem(label, pageGroup, i))

  if uiTools.isColorSupported():
    colorMenu = arm.menu.item.Submenu("Color")
    colorGroup = arm.menu.item.SelectionGroup(uiTools.setColorOverride, uiTools.getColorOverride())

    colorMenu.add(arm.menu.item.SelectionMenuItem("All", colorGroup, None))

    for color in uiTools.COLOR_LIST:
      colorMenu.add(arm.menu.item.SelectionMenuItem(str_tools._to_camel_case(color), colorGroup, color))

    viewMenu.add(colorMenu)

  return viewMenu

def makeHelpMenu():
  """
  Submenu consisting of...
    Hotkeys
    About
  """

  helpMenu = arm.menu.item.Submenu("Help")
  helpMenu.add(arm.menu.item.MenuItem("Hotkeys", arm.popups.showHelpPopup))
  helpMenu.add(arm.menu.item.MenuItem("About", arm.popups.showAboutPopup))
  return helpMenu

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

  graphMenu = arm.menu.item.Submenu("Graph")

  # stats options
  statGroup = arm.menu.item.SelectionGroup(graphPanel.setStats, graphPanel.getStats())
  availableStats = graphPanel.stats.keys()
  availableStats.sort()

  for statKey in ["None"] + availableStats:
    label = str_tools._to_camel_case(statKey, divider = " ")
    statKey = None if statKey == "None" else statKey
    graphMenu.add(arm.menu.item.SelectionMenuItem(label, statGroup, statKey))

  # resizing option
  graphMenu.add(arm.menu.item.MenuItem("Resize...", graphPanel.resizeGraph))

  # interval submenu
  intervalMenu = arm.menu.item.Submenu("Interval")
  intervalGroup = arm.menu.item.SelectionGroup(graphPanel.setUpdateInterval, graphPanel.getUpdateInterval())

  for i in range(len(arm.graphing.graphPanel.UPDATE_INTERVALS)):
    label = arm.graphing.graphPanel.UPDATE_INTERVALS[i][0]
    label = str_tools._to_camel_case(label, divider = " ")
    intervalMenu.add(arm.menu.item.SelectionMenuItem(label, intervalGroup, i))

  graphMenu.add(intervalMenu)

  # bounds submenu
  boundsMenu = arm.menu.item.Submenu("Bounds")
  boundsGroup = arm.menu.item.SelectionGroup(graphPanel.setBoundsType, graphPanel.getBoundsType())

  for boundsType in arm.graphing.graphPanel.Bounds:
    boundsMenu.add(arm.menu.item.SelectionMenuItem(boundsType, boundsGroup, boundsType))

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

  logMenu = arm.menu.item.Submenu("Log")

  logMenu.add(arm.menu.item.MenuItem("Events...", logPanel.showEventSelectionPrompt))
  logMenu.add(arm.menu.item.MenuItem("Snapshot...", logPanel.showSnapshotPrompt))
  logMenu.add(arm.menu.item.MenuItem("Clear", logPanel.clear))

  if CONFIG["features.log.showDuplicateEntries"]:
    label, arg = "Hide", False
  else: label, arg = "Show", True
  logMenu.add(arm.menu.item.MenuItem("%s Duplicates" % label, functools.partial(logPanel.setDuplicateVisability, arg)))

  # filter submenu
  filterMenu = arm.menu.item.Submenu("Filter")
  filterGroup = arm.menu.item.SelectionGroup(logPanel.makeFilterSelection, logPanel.getFilter())

  filterMenu.add(arm.menu.item.SelectionMenuItem("None", filterGroup, None))

  for option in logPanel.filterOptions:
    filterMenu.add(arm.menu.item.SelectionMenuItem(option, filterGroup, option))

  filterMenu.add(arm.menu.item.MenuItem("New...", logPanel.showFilterPrompt))
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

  connectionsMenu = arm.menu.item.Submenu("Connections")

  # listing options
  listingGroup = arm.menu.item.SelectionGroup(connPanel.setListingType, connPanel.getListingType())

  listingOptions = list(arm.connections.entries.ListingType)
  listingOptions.remove(arm.connections.entries.ListingType.HOSTNAME)

  for option in listingOptions:
    connectionsMenu.add(arm.menu.item.SelectionMenuItem(option, listingGroup, option))

  # sorting option
  connectionsMenu.add(arm.menu.item.MenuItem("Sorting...", connPanel.showSortDialog))

  # resolver submenu
  connResolver = arm.util.tracker.get_connection_tracker()
  resolverMenu = arm.menu.item.Submenu("Resolver")
  resolverGroup = arm.menu.item.SelectionGroup(connResolver.set_custom_resolver, connResolver.get_custom_resolver())

  resolverMenu.add(arm.menu.item.SelectionMenuItem("auto", resolverGroup, None))

  for option in stem.util.connection.Resolver:
    resolverMenu.add(arm.menu.item.SelectionMenuItem(option, resolverGroup, option))

  connectionsMenu.add(resolverMenu)

  return connectionsMenu

def makeConfigurationMenu(configPanel):
  """
  Submenu for the configuration panel, consisting of...
    Save Config...
    Sorting...
    Filter / Unfilter Options

  Arguments:
    configPanel - instance of the configuration panel
  """

  configMenu = arm.menu.item.Submenu("Configuration")
  configMenu.add(arm.menu.item.MenuItem("Save Config...", configPanel.showWriteDialog))
  configMenu.add(arm.menu.item.MenuItem("Sorting...", configPanel.showSortDialog))

  if configPanel.showAll: label, arg = "Filter", True
  else: label, arg = "Unfilter", False
  configMenu.add(arm.menu.item.MenuItem("%s Options" % label, functools.partial(configPanel.setFiltering, arg)))

  return configMenu

def makeTorrcMenu(torrcPanel):
  """
  Submenu for the torrc panel, consisting of...
    Reload
    Show / Hide Comments
    Show / Hide Line Numbers

  Arguments:
    torrcPanel - instance of the torrc panel
  """

  torrcMenu = arm.menu.item.Submenu("Torrc")
  torrcMenu.add(arm.menu.item.MenuItem("Reload", torrcPanel.reloadTorrc))

  if torrcPanel.stripComments: label, arg = "Show", True
  else: label, arg = "Hide", False
  torrcMenu.add(arm.menu.item.MenuItem("%s Comments" % label, functools.partial(torrcPanel.setCommentsVisible, arg)))

  if torrcPanel.showLineNum: label, arg = "Hide", False
  else: label, arg = "Show", True
  torrcMenu.add(arm.menu.item.MenuItem("%s Line Numbers" % label, functools.partial(torrcPanel.setLineNumberVisible, arg)))

  return torrcMenu

