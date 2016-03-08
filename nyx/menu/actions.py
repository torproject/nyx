"""
Generates the menu for nyx, binding options with their related actions.
"""

import functools

import nyx.controller
import nyx.curses
import nyx.panel.graph
import nyx.popups
import nyx.menu.item
import nyx.util.tracker

from nyx.util import tor_controller

import stem
import stem.util.connection

from stem.util import conf, str_tools

CONFIG = conf.config_dict('nyx', {
  'features.log.showDuplicateEntries': False,
})


def make_menu():
  """
  Constructs the base menu and all of its contents.
  """

  base_menu = nyx.menu.item.Submenu('')
  base_menu.add(make_actions_menu())
  base_menu.add(make_view_menu())

  control = nyx.controller.get_controller()

  for page_panel in control.get_display_panels(include_sticky = False):
    if page_panel.get_name() == 'graph':
      base_menu.add(make_graph_menu(page_panel))
    elif page_panel.get_name() == 'log':
      base_menu.add(make_log_menu(page_panel))
    elif page_panel.get_name() == 'connections':
      base_menu.add(make_connections_menu(page_panel))
    elif page_panel.get_name() == 'configuration':
      base_menu.add(make_configuration_menu(page_panel))
    elif page_panel.get_name() == 'torrc':
      base_menu.add(make_torrc_menu(page_panel))

  base_menu.add(make_help_menu())

  return base_menu


def make_actions_menu():
  """
  Submenu consisting of...
    Close Menu
    New Identity
    Reset Tor
    Pause / Unpause
    Exit
  """

  control = nyx.controller.get_controller()
  controller = tor_controller()
  header_panel = control.get_panel('header')
  actions_menu = nyx.menu.item.Submenu('Actions')
  actions_menu.add(nyx.menu.item.MenuItem('Close Menu', None))
  actions_menu.add(nyx.menu.item.MenuItem('New Identity', header_panel.send_newnym))
  actions_menu.add(nyx.menu.item.MenuItem('Reset Tor', functools.partial(controller.signal, stem.Signal.RELOAD)))

  if control.is_paused():
    label, arg = 'Unpause', False
  else:
    label, arg = 'Pause', True

  actions_menu.add(nyx.menu.item.MenuItem(label, functools.partial(control.set_paused, arg)))
  actions_menu.add(nyx.menu.item.MenuItem('Exit', control.quit))

  return actions_menu


def make_view_menu():
  """
  Submenu consisting of...
    [X] <Page 1>
    [ ] <Page 2>
    [ ] etc...
        Color (Submenu)
  """

  view_menu = nyx.menu.item.Submenu('View')
  control = nyx.controller.get_controller()

  if control.get_page_count() > 0:
    page_group = nyx.menu.item.SelectionGroup(control.set_page, control.get_page())

    for i in range(control.get_page_count()):
      page_panels = control.get_display_panels(page_number = i, include_sticky = False)
      label = ' / '.join([str_tools._to_camel_case(panel.get_name()) for panel in page_panels])

      view_menu.add(nyx.menu.item.SelectionMenuItem(label, page_group, i))

  if nyx.curses.is_color_supported():
    color_menu = nyx.menu.item.Submenu('Color')
    color_group = nyx.menu.item.SelectionGroup(nyx.curses.set_color_override, nyx.curses.get_color_override())

    color_menu.add(nyx.menu.item.SelectionMenuItem('All', color_group, None))

    for color in nyx.curses.Color:
      color_menu.add(nyx.menu.item.SelectionMenuItem(str_tools._to_camel_case(color), color_group, color))

    view_menu.add(color_menu)

  return view_menu


def make_help_menu():
  """
  Submenu consisting of...
    Hotkeys
    About
  """

  help_menu = nyx.menu.item.Submenu('Help')
  help_menu.add(nyx.menu.item.MenuItem('Hotkeys', nyx.popups.show_help_popup))
  help_menu.add(nyx.menu.item.MenuItem('About', nyx.popups.show_about_popup))
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

  graph_menu = nyx.menu.item.Submenu('Graph')

  # stats options

  stat_group = nyx.menu.item.SelectionGroup(functools.partial(setattr, graph_panel, 'displayed_stat'), graph_panel.displayed_stat)
  available_stats = graph_panel.stat_options()
  available_stats.sort()

  for stat_key in ['None'] + available_stats:
    label = str_tools._to_camel_case(stat_key, divider = ' ')
    stat_key = None if stat_key == 'None' else stat_key
    graph_menu.add(nyx.menu.item.SelectionMenuItem(label, stat_group, stat_key))

  # resizing option

  graph_menu.add(nyx.menu.item.MenuItem('Resize...', graph_panel.resize_graph))

  # interval submenu

  interval_menu = nyx.menu.item.Submenu('Interval')
  interval_group = nyx.menu.item.SelectionGroup(functools.partial(setattr, graph_panel, 'update_interval'), graph_panel.update_interval)

  for interval in nyx.panel.graph.Interval:
    interval_menu.add(nyx.menu.item.SelectionMenuItem(interval, interval_group, interval))

  graph_menu.add(interval_menu)

  # bounds submenu

  bounds_menu = nyx.menu.item.Submenu('Bounds')
  bounds_group = nyx.menu.item.SelectionGroup(functools.partial(setattr, graph_panel, 'bounds_type'), graph_panel.bounds_type)

  for bounds_type in nyx.panel.graph.Bounds:
    bounds_menu.add(nyx.menu.item.SelectionMenuItem(bounds_type, bounds_group, bounds_type))

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

  log_menu = nyx.menu.item.Submenu('Log')

  log_menu.add(nyx.menu.item.MenuItem('Events...', log_panel.show_event_selection_prompt))
  log_menu.add(nyx.menu.item.MenuItem('Snapshot...', log_panel.show_snapshot_prompt))
  log_menu.add(nyx.menu.item.MenuItem('Clear', log_panel.clear))

  if CONFIG['features.log.showDuplicateEntries']:
    label, arg = 'Hide', False
  else:
    label, arg = 'Show', True

  log_menu.add(nyx.menu.item.MenuItem('%s Duplicates' % label, functools.partial(log_panel.set_duplicate_visability, arg)))

  # filter submenu

  log_filter = log_panel.get_filter()

  filter_menu = nyx.menu.item.Submenu('Filter')
  filter_group = nyx.menu.item.SelectionGroup(log_filter.select, log_filter.selection())

  filter_menu.add(nyx.menu.item.SelectionMenuItem('None', filter_group, None))

  for option in log_filter.latest_selections():
    filter_menu.add(nyx.menu.item.SelectionMenuItem(option, filter_group, option))

  filter_menu.add(nyx.menu.item.MenuItem('New...', log_panel.show_filter_prompt))
  log_menu.add(filter_menu)

  return log_menu


def make_connections_menu(conn_panel):
  """
  Submenu for the connections panel, consisting of...
        Sorting...
        Resolver (Submenu)

  Arguments:
    conn_panel - instance of the connections panel
  """

  connections_menu = nyx.menu.item.Submenu('Connections')

  # sorting option

  connections_menu.add(nyx.menu.item.MenuItem('Sorting...', conn_panel.show_sort_dialog))

  # resolver submenu

  conn_resolver = nyx.util.tracker.get_connection_tracker()
  resolver_menu = nyx.menu.item.Submenu('Resolver')
  resolver_group = nyx.menu.item.SelectionGroup(conn_resolver.set_custom_resolver, conn_resolver.get_custom_resolver())

  resolver_menu.add(nyx.menu.item.SelectionMenuItem('auto', resolver_group, None))

  for option in stem.util.connection.Resolver:
    resolver_menu.add(nyx.menu.item.SelectionMenuItem(option, resolver_group, option))

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

  config_menu = nyx.menu.item.Submenu('Configuration')
  config_menu.add(nyx.menu.item.MenuItem('Save Config...', config_panel.show_write_dialog))
  config_menu.add(nyx.menu.item.MenuItem('Sorting...', config_panel.show_sort_dialog))
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

  torrc_menu = nyx.menu.item.Submenu('Torrc')

  if torrc_panel._show_comments:
    label, arg = 'Hide', False
  else:
    label, arg = 'Show', True

  torrc_menu.add(nyx.menu.item.MenuItem('%s Comments' % label, functools.partial(torrc_panel.set_comments_visible, arg)))

  if torrc_panel._show_line_numbers:
    label, arg = 'Hide', False
  else:
    label, arg = 'Show', True
  torrc_menu.add(nyx.menu.item.MenuItem('%s Line Numbers' % label, functools.partial(torrc_panel.set_line_number_visible, arg)))

  return torrc_menu
