"""
Generates the menu for arm, binding options with their related actions.
"""

import functools

import arm.popups
import arm.controller
import arm.menu.item
import arm.graphing.graph_panel
import arm.util.tracker

from arm.util import tor_tools, ui_tools

import stem.util.connection

from stem.util import conf, str_tools

CONFIG = conf.config_dict("arm", {
  "features.log.showDuplicateEntries": False,
})


def make_menu():
  """
  Constructs the base menu and all of its contents.
  """

  base_menu = arm.menu.item.Submenu("")
  base_menu.add(make_actions_menu())
  base_menu.add(make_view_menu())

  control = arm.controller.get_controller()

  for page_panel in control.get_display_panels(include_sticky = False):
    if page_panel.get_name() == "graph":
      base_menu.add(make_graph_menu(page_panel))
    elif page_panel.get_name() == "log":
      base_menu.add(make_log_menu(page_panel))
    elif page_panel.get_name() == "connections":
      base_menu.add(make_connections_menu(page_panel))
    elif page_panel.get_name() == "configuration":
      base_menu.add(make_configuration_menu(page_panel))
    elif page_panel.get_name() == "torrc":
      base_menu.add(make_torrc_menu(page_panel))

  base_menu.add(make_help_menu())

  return base_menu


def make_actions_menu():
  """
  Submenu consisting of...
    Close Menu
    New Identity
    Pause / Unpause
    Reset Tor
    Exit
  """

  control = arm.controller.get_controller()
  conn = tor_tools.get_conn()
  header_panel = control.get_panel("header")
  actions_menu = arm.menu.item.Submenu("Actions")
  actions_menu.add(arm.menu.item.MenuItem("Close Menu", None))
  actions_menu.add(arm.menu.item.MenuItem("New Identity", header_panel.send_newnym))

  if conn.is_alive():
    actions_menu.add(arm.menu.item.MenuItem("Stop Tor", conn.shutdown))

  actions_menu.add(arm.menu.item.MenuItem("Reset Tor", conn.reload))

  if control.is_paused():
    label, arg = "Unpause", False
  else:
    label, arg = "Pause", True

  actions_menu.add(arm.menu.item.MenuItem(label, functools.partial(control.set_paused, arg)))
  actions_menu.add(arm.menu.item.MenuItem("Exit", control.quit))

  return actions_menu


def make_view_menu():
  """
  Submenu consisting of...
    [X] <Page 1>
    [ ] <Page 2>
    [ ] etc...
        Color (Submenu)
  """

  view_menu = arm.menu.item.Submenu("View")
  control = arm.controller.get_controller()

  if control.get_page_count() > 0:
    page_group = arm.menu.item.SelectionGroup(control.set_page, control.get_page())

    for i in range(control.get_page_count()):
      page_panels = control.get_display_panels(page_number = i, include_sticky = False)
      label = " / ".join([str_tools._to_camel_case(panel.get_name()) for panel in page_panels])

      view_menu.add(arm.menu.item.SelectionMenuItem(label, page_group, i))

  if ui_tools.is_color_supported():
    color_menu = arm.menu.item.Submenu("Color")
    color_group = arm.menu.item.SelectionGroup(ui_tools.set_color_override, ui_tools.get_color_override())

    color_menu.add(arm.menu.item.SelectionMenuItem("All", color_group, None))

    for color in ui_tools.COLOR_LIST:
      color_menu.add(arm.menu.item.SelectionMenuItem(str_tools._to_camel_case(color), color_group, color))

    view_menu.add(color_menu)

  return view_menu


def make_help_menu():
  """
  Submenu consisting of...
    Hotkeys
    About
  """

  help_menu = arm.menu.item.Submenu("Help")
  help_menu.add(arm.menu.item.MenuItem("Hotkeys", arm.popups.show_help_popup))
  help_menu.add(arm.menu.item.MenuItem("About", arm.popups.show_about_popup))
  return help_menu


def make_graph_menu(graph_panel):
  """
  Submenu for the graph panel, consisting of...
    [X] <Stat 1>
    [ ] <Stat 2>
    [ ] <Stat 2>
        Resize...
        Interval (Submenu)
        Bounds (Submenu)

  Arguments:
    graph_panel - instance of the graph panel
  """

  graph_menu = arm.menu.item.Submenu("Graph")

  # stats options

  stat_group = arm.menu.item.SelectionGroup(graph_panel.set_stats, graph_panel.get_stats())
  available_stats = graph_panel.stats.keys()
  available_stats.sort()

  for stat_key in ["None"] + available_stats:
    label = str_tools._to_camel_case(stat_key, divider = " ")
    stat_key = None if stat_key == "None" else stat_key
    graph_menu.add(arm.menu.item.SelectionMenuItem(label, stat_group, stat_key))

  # resizing option

  graph_menu.add(arm.menu.item.MenuItem("Resize...", graph_panel.resize_graph))

  # interval submenu

  interval_menu = arm.menu.item.Submenu("Interval")
  interval_group = arm.menu.item.SelectionGroup(graph_panel.set_update_interval, graph_panel.get_update_interval())

  for i in range(len(arm.graphing.graph_panel.UPDATE_INTERVALS)):
    label = arm.graphing.graph_panel.UPDATE_INTERVALS[i][0]
    label = str_tools._to_camel_case(label, divider = " ")
    interval_menu.add(arm.menu.item.SelectionMenuItem(label, interval_group, i))

  graph_menu.add(interval_menu)

  # bounds submenu

  bounds_menu = arm.menu.item.Submenu("Bounds")
  bounds_group = arm.menu.item.SelectionGroup(graph_panel.set_bounds_type, graph_panel.get_bounds_type())

  for bounds_type in arm.graphing.graph_panel.Bounds:
    bounds_menu.add(arm.menu.item.SelectionMenuItem(bounds_type, bounds_group, bounds_type))

  graph_menu.add(bounds_menu)

  return graph_menu


def make_log_menu(log_panel):
  """
  Submenu for the log panel, consisting of...
    Events...
    Snapshot...
    Clear
    Show / Hide Duplicates
    Filter (Submenu)

  Arguments:
    log_panel - instance of the log panel
  """

  log_menu = arm.menu.item.Submenu("Log")

  log_menu.add(arm.menu.item.MenuItem("Events...", log_panel.show_event_selection_prompt))
  log_menu.add(arm.menu.item.MenuItem("Snapshot...", log_panel.show_snapshot_prompt))
  log_menu.add(arm.menu.item.MenuItem("Clear", log_panel.clear))

  if CONFIG["features.log.showDuplicateEntries"]:
    label, arg = "Hide", False
  else:
    label, arg = "Show", True

  log_menu.add(arm.menu.item.MenuItem("%s Duplicates" % label, functools.partial(log_panel.set_duplicate_visability, arg)))

  # filter submenu

  filter_menu = arm.menu.item.Submenu("Filter")
  filter_group = arm.menu.item.SelectionGroup(log_panel.make_filter_selection, log_panel.get_filter())

  filter_menu.add(arm.menu.item.SelectionMenuItem("None", filter_group, None))

  for option in log_panel.filter_options:
    filter_menu.add(arm.menu.item.SelectionMenuItem(option, filter_group, option))

  filter_menu.add(arm.menu.item.MenuItem("New...", log_panel.show_filter_prompt))
  log_menu.add(filter_menu)

  return log_menu


def make_connections_menu(conn_panel):
  """
  Submenu for the connections panel, consisting of...
    [X] IP Address
    [ ] Fingerprint
    [ ] Nickname
        Sorting...
        Resolver (Submenu)

  Arguments:
    conn_panel - instance of the connections panel
  """

  connections_menu = arm.menu.item.Submenu("Connections")

  # listing options

  listing_group = arm.menu.item.SelectionGroup(conn_panel.set_listing_type, conn_panel.get_listing_type())

  listing_options = list(arm.connections.entries.ListingType)
  listing_options.remove(arm.connections.entries.ListingType.HOSTNAME)

  for option in listing_options:
    connections_menu.add(arm.menu.item.SelectionMenuItem(option, listing_group, option))

  # sorting option

  connections_menu.add(arm.menu.item.MenuItem("Sorting...", conn_panel.show_sort_dialog))

  # resolver submenu

  conn_resolver = arm.util.tracker.get_connection_tracker()
  resolver_menu = arm.menu.item.Submenu("Resolver")
  resolver_group = arm.menu.item.SelectionGroup(conn_resolver.set_custom_resolver, conn_resolver.get_custom_resolver())

  resolver_menu.add(arm.menu.item.SelectionMenuItem("auto", resolver_group, None))

  for option in stem.util.connection.Resolver:
    resolver_menu.add(arm.menu.item.SelectionMenuItem(option, resolver_group, option))

  connections_menu.add(resolver_menu)

  return connections_menu


def make_configuration_menu(config_panel):
  """
  Submenu for the configuration panel, consisting of...
    Save Config...
    Sorting...
    Filter / Unfilter Options

  Arguments:
    config_panel - instance of the configuration panel
  """

  config_menu = arm.menu.item.Submenu("Configuration")
  config_menu.add(arm.menu.item.MenuItem("Save Config...", config_panel.show_write_dialog))
  config_menu.add(arm.menu.item.MenuItem("Sorting...", config_panel.show_sort_dialog))

  if config_panel.show_all:
    label, arg = "Filter", True
  else:
    label, arg = "Unfilter", False

  config_menu.add(arm.menu.item.MenuItem("%s Options" % label, functools.partial(config_panel.set_filtering, arg)))

  return config_menu


def make_torrc_menu(torrc_panel):
  """
  Submenu for the torrc panel, consisting of...
    Reload
    Show / Hide Comments
    Show / Hide Line Numbers

  Arguments:
    torrc_panel - instance of the torrc panel
  """

  torrc_menu = arm.menu.item.Submenu("Torrc")
  torrc_menu.add(arm.menu.item.MenuItem("Reload", torrc_panel.reload_torrc))

  if torrc_panel.strip_comments:
    label, arg = "Show", True
  else:
    label, arg = "Hide", False

  torrc_menu.add(arm.menu.item.MenuItem("%s Comments" % label, functools.partial(torrc_panel.set_comments_visible, arg)))

  if torrc_panel.show_line_num:
    label, arg = "Hide", False
  else:
    label, arg = "Show", True
  torrc_menu.add(arm.menu.item.MenuItem("%s Line Numbers" % label, functools.partial(torrc_panel.set_line_number_visible, arg)))

  return torrc_menu
