"""
Listing of the currently established connections tor has made.
"""

import re
import time
import collections
import curses
import itertools
import threading

import nyx.popups
import nyx.util.tracker

from nyx.connections import descriptor_popup, entries, conn_entry, circ_entry
from nyx.util import panel, tor_controller, tracker, ui_tools

from stem.control import State
from stem.util import conf, connection, enum

# height of the detail panel content, not counting top and bottom border

DETAILS_HEIGHT = 7

# listing types

Listing = enum.Enum(('IP_ADDRESS', 'IP Address'), 'FINGERPRINT', 'NICKNAME')

EXIT_USAGE_WIDTH = 15
UPDATE_RATE = 5  # rate in seconds at which we refresh


def conf_handler(key, value):
  if key == 'features.connection.listing_type':
    return conf.parse_enum(key, value, Listing)
  elif key == 'features.connection.order':
    return conf.parse_enum_csv(key, value[0], entries.SortAttr, 3)


CONFIG = conf.config_dict('nyx', {
  'features.connection.resolveApps': True,
  'features.connection.listing_type': Listing.IP_ADDRESS,
  'features.connection.order': [
    entries.SortAttr.CATEGORY,
    entries.SortAttr.LISTING,
    entries.SortAttr.UPTIME],
  'features.connection.showIps': True,
}, conf_handler)


class ConnectionPanel(panel.Panel, threading.Thread):
  """
  Listing of connections tor is making, with information correlated against
  the current consensus and other data sources.
  """

  def __init__(self, stdscr):
    panel.Panel.__init__(self, stdscr, 'connections', 0)
    threading.Thread.__init__(self)
    self.setDaemon(True)

    # defaults our listing selection to fingerprints if ip address
    # displaying is disabled
    #
    # TODO: This is a little sucky in that it won't work if showIps changes
    # while we're running (... but nyx doesn't allow for that atm)

    if not CONFIG['features.connection.showIps'] and CONFIG['features.connection.listing_type'] == 0:
      nyx_config = conf.get_config('nyx')
      nyx_config.set('features.connection.listing_type', Listing.keys()[Listing.index_of(Listing.FINGERPRINT)])

    self._scroller = ui_tools.Scroller(True)
    self._entries = []            # last fetched display entries
    self._entry_lines = []        # individual lines rendered from the entries listing
    self._show_details = False    # presents the details panel if true

    self._last_update = -1        # time the content was last revised
    self._is_tor_running = True   # indicates if tor is currently running or not
    self._halt_time = None        # time when tor was stopped
    self._vals_lock = threading.RLock()

    self._pause_condition = threading.Condition()
    self._halt = False  # terminates thread if true

    # Tracks exiting port and client country statistics

    self._client_locale_usage = {}
    self._exit_port_usage = {}

    # If we're a bridge and been running over a day then prepopulates with the
    # last day's clients.

    controller = tor_controller()
    bridge_clients = controller.get_info('status/clients-seen', None)

    if bridge_clients:
      # Response has a couple arguments...
      # TimeStarted="2011-08-17 15:50:49" CountrySummary=us=16,de=8,uk=8

      country_summary = None

      for line in bridge_clients.split():
        if line.startswith('CountrySummary='):
          country_summary = line[15:]
          break

      if country_summary:
        for entry in country_summary.split(','):
          if re.match('^..=[0-9]+$', entry):
            locale, count = entry.split('=', 1)
            self._client_locale_usage[locale] = int(count)

    # Last sampling received from the ConnectionResolver, used to detect when
    # it changes.

    self._last_resource_fetch = -1

    # resolver for the command/pid associated with SOCKS, HIDDEN, and CONTROL connections

    self._app_resolver = tracker.get_port_usage_tracker()

    # mark the initially exitsing connection uptimes as being estimates

    for entry in self._entries:
      if isinstance(entry, conn_entry.ConnectionEntry):
        entry.get_lines()[0].is_initial_connection = True

    # listens for when tor stops so we know to stop reflecting changes

    controller.add_status_listener(self.tor_state_listener)

  def tor_state_listener(self, controller, event_type, _):
    """
    Freezes the connection contents when Tor stops.
    """

    self._is_tor_running = event_type in (State.INIT, State.RESET)
    self._halt_time = None if self._is_tor_running else time.time()
    self.redraw(True)

  def get_pause_time(self):
    """
    Provides the time Tor stopped if it isn't running. Otherwise this is the
    time we were last paused.
    """

    return self._halt_time if self._halt_time else panel.Panel.get_pause_time(self)

  def set_sort_order(self, ordering = None):
    """
    Sets the connection attributes we're sorting by and resorts the contents.

    Arguments:
      ordering - new ordering, if undefined then this resorts with the last
                 set ordering
    """

    with self._vals_lock:
      if ordering:
        nyx_config = conf.get_config('nyx')

        ordering_keys = [entries.SortAttr.keys()[entries.SortAttr.index_of(v)] for v in ordering]
        nyx_config.set('features.connection.order', ', '.join(ordering_keys))

      self._entries.sort(key = lambda i: (i.get_sort_values(CONFIG['features.connection.order'], self.get_listing_type())))
      self._entry_lines = list(itertools.chain.from_iterable([entry.get_lines() for entry in self._entries]))

  def get_listing_type(self):
    """
    Provides the priority content we list connections by.
    """

    return CONFIG['features.connection.listing_type']

  def set_listing_type(self, listing_type):
    """
    Sets the priority information presented by the panel.

    Arguments:
      listing_type - Listing instance for the primary information to be shown
    """

    if self.get_listing_type() == listing_type:
      return

    with self._vals_lock:
      nyx_config = conf.get_config('nyx')
      nyx_config.set('features.connection.listing_type', Listing.keys()[Listing.index_of(listing_type)])

      # if we're sorting by the listing then we need to resort

      if entries.SortAttr.LISTING in CONFIG['features.connection.order']:
        self.set_sort_order()

  def show_sort_dialog(self):
    """
    Provides the sort dialog for our connections.
    """

    # set ordering for connection options

    title_label = 'Connection Ordering:'
    options = list(entries.SortAttr)
    old_selection = CONFIG['features.connection.order']
    option_colors = dict([(attr, entries.SORT_COLORS[attr]) for attr in options])
    results = nyx.popups.show_sort_dialog(title_label, options, old_selection, option_colors)

    if results:
      self.set_sort_order(results)

  def handle_key(self, key):
    with self._vals_lock:
      user_traffic_allowed = tor_controller().is_user_traffic_allowed()

      if key.is_scroll():
        page_height = self.get_preferred_size()[0] - 1

        if self._show_details:
          page_height -= (DETAILS_HEIGHT + 1)

        is_changed = self._scroller.handle_key(key, self._entry_lines, page_height)

        if is_changed:
          self.redraw(True)
      elif key.is_selection():
        self._show_details = not self._show_details
        self.redraw(True)
      elif key.match('s'):
        self.show_sort_dialog()
      elif key.match('u'):
        # provides a menu to pick the connection resolver

        title = 'Resolver Util:'
        options = ['auto'] + list(connection.Resolver)
        conn_resolver = nyx.util.tracker.get_connection_tracker()

        current_overwrite = conn_resolver.get_custom_resolver()

        if current_overwrite is None:
          old_selection = 0
        else:
          old_selection = options.index(current_overwrite)

        selection = nyx.popups.show_menu(title, options, old_selection)

        # applies new setting

        if selection != -1:
          selected_option = options[selection] if selection != 0 else None
          conn_resolver.set_custom_resolver(selected_option)
      elif key.match('l'):
        # provides a menu to pick the primary information we list connections by

        title = 'List By:'
        options = list(entries.ListingType)

        old_selection = options.index(self.get_listing_type())
        selection = nyx.popups.show_menu(title, options, old_selection)

        # applies new setting

        if selection != -1:
          self.set_listing_type(options[selection])
      elif key.match('d'):
        self.set_title_visible(False)
        self.redraw(True)

        while True:
          selection = self.get_selection()

          if not selection:
            break

          color = nyx.connections.conn_entry.CATEGORY_COLOR[selection.get_type()]
          fingerprint = selection.foreign.get_fingerprint()
          is_close_key = lambda key: key.is_selection() or key.match('d') or key.match('left') or key.match('right')
          key = descriptor_popup.show_descriptor_popup(fingerprint, color, self.max_x, is_close_key)

          if not key or key.is_selection() or key.match('d'):
            break  # closes popup
          elif key.match('left'):
            self.handle_key(panel.KeyInput(curses.KEY_UP))
          elif key.match('right'):
            self.handle_key(panel.KeyInput(curses.KEY_DOWN))

        self.set_title_visible(True)
        self.redraw(True)
      elif key.match('c') and user_traffic_allowed.inbound:
        nyx.popups.show_count_dialog('Client Locales', self._client_locale_usage)
      elif key.match('e') and user_traffic_allowed.outbound:
        counts = {}
        key_width = max(map(len, self._exit_port_usage.keys()))

        for k, v in self._exit_port_usage.items():
          usage = connection.port_usage(k)

          if usage:
            k = k.ljust(key_width + 3) + usage.ljust(EXIT_USAGE_WIDTH)

          counts[k] = v

        nyx.popups.show_count_dialog('Exiting Port Usage', counts)
      else:
        return False

      return True

  def run(self):
    """
    Keeps connections listing updated, checking for new entries at a set rate.
    """

    last_ran = -1

    while not self._halt:
      if self.is_paused() or not self._is_tor_running or (time.time() - last_ran) < UPDATE_RATE:
        with self._pause_condition:
          if not self._halt:
            self._pause_condition.wait(0.2)

        continue  # done waiting, try again

      self._update()
      self.redraw(True)

      # If this is our first run then fill in our fingerprint tracker. This
      # requires fetching all the router status entries which takes a few
      # seconds, so best done when we're finished with the rest of the first
      # iteration to hide the lag.

      if last_ran == -1:
        nyx.util.tracker.get_consensus_tracker().update(tor_controller().get_network_statuses([]))

      last_ran = time.time()

  def get_help(self):
    resolver_util = nyx.util.tracker.get_connection_tracker().get_custom_resolver()
    user_traffic_allowed = tor_controller().is_user_traffic_allowed()

    options = [
      ('up arrow', 'scroll up a line', None),
      ('down arrow', 'scroll down a line', None),
      ('page up', 'scroll up a page', None),
      ('page down', 'scroll down a page', None),
      ('enter', 'show connection details', None),
      ('d', 'raw consensus descriptor', None),
      ('l', 'listed identity', self.get_listing_type().lower()),
      ('s', 'sort ordering', None),
      ('u', 'resolving utility', 'auto' if resolver_util is None else resolver_util),
    ]

    if user_traffic_allowed.inbound:
      options.append(('c', 'client locale usage summary', None))

    if user_traffic_allowed.outbound:
      options.append(('e', 'exit port usage summary', None))

    return options

  def get_selection(self):
    """
    Provides the currently selected connection entry.
    """

    return self._scroller.get_cursor_selection(self._entry_lines)

  def draw(self, width, height):
    with self._vals_lock:
      # if we don't have any contents then refuse to show details

      if not self._entries:
        self._show_details = False

      # extra line when showing the detail panel is for the bottom border

      detail_panel_offset = DETAILS_HEIGHT + 1 if self._show_details else 0
      is_scrollbar_visible = len(self._entry_lines) > height - detail_panel_offset - 1

      scroll_location = self._scroller.get_scroll_location(self._entry_lines, height - detail_panel_offset - 1)
      cursor_selection = self.get_selection()

      # draws the detail panel if currently displaying it

      if self._show_details and cursor_selection:
        # This is a solid border unless the scrollbar is visible, in which case a
        # 'T' pipe connects the border to the bar.

        ui_tools.draw_box(self, 0, 0, width, DETAILS_HEIGHT + 2)

        if is_scrollbar_visible:
          self.addch(DETAILS_HEIGHT + 1, 1, curses.ACS_TTEE)

        draw_entries = cursor_selection.get_details(width)

        for i in range(min(len(draw_entries), DETAILS_HEIGHT)):
          self.addstr(1 + i, 2, draw_entries[i][0], *draw_entries[i][1])

      # title label with connection counts

      if self.is_title_visible():
        self._draw_title(self._entries)

      scroll_offset = 0

      if is_scrollbar_visible:
        scroll_offset = 2
        self.add_scroll_bar(scroll_location, scroll_location + height - detail_panel_offset - 1, len(self._entry_lines), 1 + detail_panel_offset)

      if self.is_paused() or not self._is_tor_running:
        current_time = self.get_pause_time()
      else:
        current_time = time.time()

      for line_number in range(scroll_location, len(self._entry_lines)):
        entry_line = self._entry_lines[line_number]

        # hilighting if this is the selected line

        extra_format = curses.A_STANDOUT if entry_line == cursor_selection else curses.A_NORMAL

        draw_line = line_number + detail_panel_offset + 1 - scroll_location

        prefix = entry_line.get_listing_prefix()

        for i in range(len(prefix)):
          self.addch(draw_line, scroll_offset + i, prefix[i])

        x_offset = scroll_offset + len(prefix)
        draw_entry = entry_line.get_listing_entry(width - scroll_offset - len(prefix), current_time, self.get_listing_type())

        for msg, attr in draw_entry:
          attr |= extra_format
          self.addstr(draw_line, x_offset, msg, attr)
          x_offset += len(msg)

        if draw_line >= height:
          break

  def _draw_title(self, entries):
    if self._show_details:
      title = 'Connection Details:'
    elif not entries:
      title = 'Connections:'
    else:
      counts = collections.Counter([entry.get_lines()[0].get_type() for entry in entries])
      count_labels = ['%i %s' % (counts[category], category.lower()) for category in conn_entry.Category if counts[category]]
      title = 'Connections (%s):' % ', '.join(count_labels)

    self.addstr(0, 0, title, curses.A_STANDOUT)

  def stop(self):
    """
    Halts further resolutions and terminates the thread.
    """

    with self._pause_condition:
      self._halt = True
      self._pause_condition.notifyAll()

  def _update(self):
    """
    Fetches the newest resolved connections.
    """

    conn_resolver = nyx.util.tracker.get_connection_tracker()

    if not conn_resolver.is_alive():
      return  # if we're not fetching connections then this is a no-op

    current_resolution_count = conn_resolver.run_counter()

    with self._vals_lock:
      new_entries = [conn_entry.ConnectionEntry(conn) for conn in conn_resolver.get_value()]

      for circ in tor_controller().get_circuits([]):
        # Skips established single-hop circuits (these are for directory
        # fetches, not client circuits)

        if not (circ.status == 'BUILT' and len(circ.path) == 1):
          new_entries.append(circ_entry.CircEntry(circ))

      # update stats for client and exit connections

      for entry in new_entries:
        entry_line = entry.get_lines()[0]

        if entry_line.is_private() and entry_line.get_type() == conn_entry.Category.INBOUND:
          client_locale = entry_line.foreign.get_locale()

          if client_locale:
            self._client_locale_usage[client_locale] = self._client_locale_usage.get(client_locale, 0) + 1
        elif entry_line.get_type() == conn_entry.Category.EXIT:
          exit_port = entry_line.foreign.get_port()
          self._exit_port_usage[exit_port] = self._exit_port_usage.get(exit_port, 0) + 1

      self._entries, self._entry_lines = new_entries, list(itertools.chain.from_iterable([entry.get_lines() for entry in new_entries]))

      self.set_sort_order()
      self._last_resource_fetch = current_resolution_count

      self._resolve_apps()

  def _resolve_apps(self):
    """
    Triggers an asynchronous query for all unresolved SOCKS, HIDDEN, and
    CONTROL entries.
    """

    if not CONFIG['features.connection.resolveApps']:
      return

    unresolved_lines = [l for l in self._entry_lines if isinstance(l, conn_entry.ConnectionLine) and l.is_unresolved_application()]

    # get the ports used for unresolved applications

    app_ports = []

    for line in unresolved_lines:
      app_conn = line.local if line.get_type() == conn_entry.Category.HIDDEN else line.foreign
      app_ports.append(app_conn.get_port())

    # Queue up resolution for the unresolved ports (skips if it's still working
    # on the last query).

    if app_ports and not self._app_resolver.is_alive():
      self._app_resolver.get_processes_using_ports(app_ports)

    # Fetches results. If the query finishes quickly then this is what we just
    # asked for, otherwise these belong to an earlier resolution.
    #
    # The application resolver might have given up querying (for instance, if
    # the lsof lookups aren't working on this platform or lacks permissions).
    # The is_application_resolving flag lets the unresolved entries indicate if there's
    # a lookup in progress for them or not.

    time.sleep(0.2)  # TODO: previous resolver only blocked while awaiting a lookup
    app_results = self._app_resolver.get_processes_using_ports(app_ports)

    for line in unresolved_lines:
      is_local = line.get_type() == conn_entry.Category.HIDDEN
      line_port = line.local.get_port() if is_local else line.foreign.get_port()

      if line_port in app_results:
        # sets application attributes if there's a result with this as the
        # inbound port

        for inbound_port, outbound_port, cmd, pid in app_results[line_port]:
          app_port = outbound_port if is_local else inbound_port

          if line_port == app_port:
            line.application_name = cmd
            line.application_pid = pid
            line.is_application_resolving = False
      else:
        line.is_application_resolving = self._app_resolver.is_alive
