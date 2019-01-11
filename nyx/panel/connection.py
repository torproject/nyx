# Copyright 2011-2019, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Listing of the currently established connections tor has made.
"""

import collections
import curses
import itertools
import re
import time

import nyx
import nyx.curses
import nyx.panel
import nyx.popups
import nyx.tracker

from nyx import nyx_interface, tor_controller
from nyx.curses import WHITE, NORMAL, BOLD, HIGHLIGHT
from nyx.menu import MenuItem, Submenu, RadioMenuItem, RadioGroup

from stem.control import Listener
from stem.util import datetime_to_unix, conf, connection, enum, str_tools

# height of the detail panel content, not counting top and bottom border

DETAILS_HEIGHT = 7

EXIT_USAGE_WIDTH = 15
UPDATE_RATE = 5  # rate in seconds at which we refresh

# cached information from our last _update() call

LAST_RETRIEVED_HS_CONF = None
LAST_RETRIEVED_CIRCUITS = None

ENTRY_CACHE = {}
ENTRY_CACHE_REFERENCED = {}

# Connection Categories:
#   Inbound      Relay connection, coming to us.
#   Outbound     Relay connection, leaving us.
#   Exit         Outbound relay connection leaving the Tor network.
#   Hidden       Connections to a hidden service we're providing.
#   Socks        Socks connections for applications using Tor.
#   Circuit      Circuits our tor client has created.
#   Directory    Fetching tor consensus information.
#   Control      Tor controller (nyx, vidalia, etc).

Category = enum.Enum('INBOUND', 'OUTBOUND', 'EXIT', 'HIDDEN', 'SOCKS', 'CIRCUIT', 'DIRECTORY', 'CONTROL')
SortAttr = enum.Enum('CATEGORY', 'UPTIME', 'IP_ADDRESS', 'PORT', 'FINGERPRINT', 'NICKNAME', 'COUNTRY')
LineType = enum.Enum('CONNECTION', 'CIRCUIT_HEADER', 'CIRCUIT')

Line = collections.namedtuple('Line', [
  'entry',
  'line_type',
  'connection',
  'circuit',
  'fingerprint',
  'nickname',
  'locale',
])


def conf_handler(key, value):
  if key == 'connection_order':
    return conf.parse_enum_csv(key, value[0], SortAttr, 3)


CONFIG = conf.config_dict('nyx', {
  'attr.connection.category_color': {},
  'attr.connection.sort_color': {},
  'connection_order': [SortAttr.CATEGORY, SortAttr.IP_ADDRESS, SortAttr.UPTIME],
  'resolve_processes': True,
  'show_addresses': True,
}, conf_handler)


class Entry(object):
  @staticmethod
  def from_connection(connection):
    if connection not in ENTRY_CACHE:
      ENTRY_CACHE[connection] = ConnectionEntry(connection)

    ENTRY_CACHE_REFERENCED[connection] = time.time()
    return ENTRY_CACHE[connection]

  @staticmethod
  def from_circuit(circuit):
    if circuit not in ENTRY_CACHE:
      ENTRY_CACHE[circuit] = CircuitEntry(circuit)

    ENTRY_CACHE_REFERENCED[circuit] = time.time()
    return ENTRY_CACHE[circuit]

  def __init__(self):
    self._lines = None
    self._type = None
    self._is_private_val = None

  def get_lines(self):
    """
    Provides individual lines of connection information.

    :returns: **list** of :class:`nyx.panel.connection.Line` concerning this entry
    """

    if self._lines is None:
      self._lines = self._get_lines()

    return self._lines

  def get_type(self):
    """
    Provides our best guess at the type of connection this is.

    :returns: **Category** for the connection's type
    """

    if self._type is None:
      self._type = self._get_type()

    return self._type

  def is_private(self):
    """
    Checks if information about this endpoint should be scrubbed. Relaying
    etiquette (and wiretapping laws) say these are bad things to look at so
    DON'T CHANGE THIS UNLESS YOU HAVE A DAMN GOOD REASON!

    :returns: **bool** indicating if connection information is sensive or not
    """

    if self._is_private_val is None:
      self._is_private_val = self._is_private()

    return self._is_private_val

  def sort_value(self, attr):
    """
    Provides a heuristic for sorting by a given value.

    :param SortAttr attr: sort attribute to provide a heuristic for

    :returns: comparable value for sorting
    """

    line = self.get_lines()[0]
    at_end = 'z' * 20

    if attr == SortAttr.IP_ADDRESS:
      if self.is_private():
        return 255 ** 4  # orders at the end
      else:
        address_int = connection.address_to_int(line.connection.remote_address)
        return address_int * 65536 + line.connection.remote_port
    elif attr == SortAttr.PORT:
      return line.connection.remote_port
    elif attr == SortAttr.FINGERPRINT:
      return line.fingerprint if line.fingerprint else at_end
    elif attr == SortAttr.NICKNAME:
      return line.nickname if line.nickname else at_end
    elif attr == SortAttr.CATEGORY:
      return Category.index_of(self.get_type())
    elif attr == SortAttr.UPTIME:
      return line.connection.start_time
    elif attr == SortAttr.COUNTRY:
      return line.locale if (line.locale and not self.is_private()) else at_end
    else:
      return ''

  def _get_lines(self):
    raise NotImplementedError('should be implemented by subclasses')

  def _get_type(self):
    raise NotImplementedError('should be implemented by subclasses')

  def _is_private(self):
    raise NotImplementedError('should be implemented by subclasses')


class ConnectionEntry(Entry):
  def __init__(self, connection):
    super(ConnectionEntry, self).__init__()
    self._connection = connection

  def _get_lines(self):
    fingerprint, nickname = None, None

    if self.get_type() in (Category.OUTBOUND, Category.CIRCUIT, Category.DIRECTORY, Category.EXIT):
      fingerprint = nyx.tracker.get_consensus_tracker().get_relay_fingerprints(self._connection.remote_address).get(self._connection.remote_port)

      if fingerprint:
        nickname = nyx.tracker.get_consensus_tracker().get_relay_nickname(fingerprint)

    locale = tor_controller().get_info('ip-to-country/%s' % self._connection.remote_address, None)
    return [Line(self, LineType.CONNECTION, self._connection, None, fingerprint, nickname, locale)]

  def _get_type(self):
    controller = tor_controller()

    if self._connection.local_port in controller.get_ports(Listener.OR, []):
      return Category.INBOUND
    elif self._connection.local_port in controller.get_ports(Listener.DIR, []):
      return Category.INBOUND
    elif self._connection.local_port in controller.get_ports(Listener.SOCKS, []):
      return Category.SOCKS
    elif self._connection.local_port in controller.get_ports(Listener.CONTROL, []):
      return Category.CONTROL

    if LAST_RETRIEVED_HS_CONF:
      for hs_config in LAST_RETRIEVED_HS_CONF.values():
        if self._connection.remote_port == hs_config['HiddenServicePort']:
          return Category.HIDDEN

    fingerprint = nyx.tracker.get_consensus_tracker().get_relay_fingerprints(self._connection.remote_address).get(self._connection.remote_port)
    exit_policy = controller.get_exit_policy(None)

    if fingerprint and LAST_RETRIEVED_CIRCUITS:
      for circ in LAST_RETRIEVED_CIRCUITS:
        if circ.path and len(circ.path) == 1 and circ.path[0][0] == fingerprint and circ.status == 'BUILT':
          return Category.DIRECTORY  # one-hop circuit to retrieve directory information
    elif not fingerprint and exit_policy and exit_policy.can_exit_to(self._connection.remote_address, self._connection.remote_port):
      return Category.EXIT

    return Category.OUTBOUND

  def _is_private(self):
    if not CONFIG['show_addresses']:
      return True

    if self.get_type() == Category.INBOUND:
      return len(nyx.tracker.get_consensus_tracker().get_relay_fingerprints(self._connection.remote_address)) == 0
    elif self.get_type() == Category.EXIT:
      # DNS connections exiting us aren't private (since they're hitting our
      # resolvers). Everything else is.

      return not (self._connection.remote_port == 53 and self._connection.protocol == 'udp')

    return False  # for everything else this isn't a concern


class CircuitEntry(Entry):
  def __init__(self, circuit):
    super(CircuitEntry, self).__init__()
    self._circuit = circuit

  def _get_lines(self):
    def line(fingerprint, line_type):
      address, port, nickname = '0.0.0.0', 0, None
      consensus_tracker = nyx.tracker.get_consensus_tracker()

      if fingerprint is not None:
        address, port = consensus_tracker.get_relay_address(fingerprint, ('192.168.0.1', 0))
        nickname = consensus_tracker.get_relay_nickname(fingerprint)

      locale = tor_controller().get_info('ip-to-country/%s' % address, None)
      connection = nyx.tracker.Connection(datetime_to_unix(self._circuit.created), False, '127.0.0.1', 0, address, port, 'tcp', False)
      return Line(self, line_type, connection, self._circuit, fingerprint, nickname, locale)

    header_line = line(self._circuit.path[-1][0] if self._circuit.status == 'BUILT' else None, LineType.CIRCUIT_HEADER)
    return [header_line] + [line(fp, LineType.CIRCUIT) for fp, _ in self._circuit.path]

  def _get_type(self):
    return Category.CIRCUIT

  def _is_private(self):
    return False


class ConnectionPanel(nyx.panel.DaemonPanel):
  """
  Listing of connections tor is making, with information correlated against
  the current consensus and other data sources.
  """

  def __init__(self):
    nyx.panel.DaemonPanel.__init__(self, UPDATE_RATE)

    self._scroller = nyx.curses.CursorScroller()
    self._entries = []            # last fetched display entries
    self._show_details = False    # presents the details panel if true
    self._sort_order = CONFIG['connection_order']
    self._pause_time = 0

    self._last_resource_fetch = -1  # timestamp of the last ConnectionResolver results used

    # Tracks exiting port and client country statistics

    self._client_locale_usage = {}
    self._exit_port_usage = {}
    self._counted_connections = set()

    # If we're a bridge and been running over a day then prepopulates with the
    # last day's clients.

    bridge_clients = tor_controller().get_info('status/clients-seen', None)

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

  def _show_sort_dialog(self):
    """
    Provides a dialog for sorting our connections.
    """

    sort_colors = dict([(attr, CONFIG['attr.connection.sort_color'].get(attr, WHITE)) for attr in SortAttr])
    results = nyx.popups.select_sort_order('Connection Ordering:', SortAttr, self._sort_order, sort_colors)

    if results:
      self._sort_order = results
      self._entries = sorted(self._entries, key = lambda entry: [entry.sort_value(attr) for attr in self._sort_order])

  def set_paused(self, is_pause):
    if is_pause:
      self._pause_time = time.time()

  def key_handlers(self):
    def _scroll(key):
      page_height = self.get_height() - 1

      if self._show_details:
        page_height -= (DETAILS_HEIGHT + 1)

      lines = list(itertools.chain.from_iterable([entry.get_lines() for entry in self._entries]))
      is_changed = self._scroller.handle_key(key, lines, page_height)

      if is_changed:
        self.redraw()

    def _show_details():
      self._show_details = not self._show_details
      self.redraw()

    def _show_descriptor():
      entries = self._entries

      while True:
        lines = list(itertools.chain.from_iterable([entry.get_lines() for entry in entries]))
        selected = self._scroller.selection(lines)

        if not selected:
          break

        def is_close_key(key):
          return key.is_selection() or key.match('d') or key.match('left') or key.match('right')

        color = CONFIG['attr.connection.category_color'].get(selected.entry.get_type(), WHITE)
        key = nyx.popups.show_descriptor(selected.fingerprint, color, is_close_key)

        if not key or key.is_selection() or key.match('d'):
          break  # closes popup
        elif key.match('left'):
          _scroll(nyx.curses.KeyInput(curses.KEY_UP))
        elif key.match('right'):
          _scroll(nyx.curses.KeyInput(curses.KEY_DOWN))

      self.redraw()

    def _pick_connection_resolver():
      connection_tracker = nyx.tracker.get_connection_tracker()
      resolver = connection_tracker.get_custom_resolver()
      options = ['auto'] + list(connection.Resolver) + list(nyx.tracker.CustomResolver)

      selected = nyx.popups.select_from_list('Connection Resolver:', options, resolver if resolver else 'auto')
      connection_tracker.set_custom_resolver(None if selected == 'auto' else selected)

      self.redraw()

    def _show_client_locales():
      nyx.popups.show_counts('Client Locales', self._client_locale_usage)

    def _show_exiting_port_usage():
      counts = {}
      key_width = max(map(len, self._exit_port_usage.keys())) if self._exit_port_usage else 0

      for k, v in self._exit_port_usage.items():
        usage = connection.port_usage(k)

        if usage:
          k = k.ljust(key_width + 3) + usage.ljust(EXIT_USAGE_WIDTH)

        counts[k] = v

      nyx.popups.show_counts('Exiting Port Usage', counts)

    resolver = nyx.tracker.get_connection_tracker().get_custom_resolver()
    user_traffic_allowed = tor_controller().is_user_traffic_allowed()

    options = [
      nyx.panel.KeyHandler('arrows', 'scroll up and down', _scroll, key_func = lambda key: key.is_scroll()),
      nyx.panel.KeyHandler('enter', 'show connection details', _show_details, key_func = lambda key: key.is_selection()),
      nyx.panel.KeyHandler('d', 'raw consensus descriptor', _show_descriptor),
      nyx.panel.KeyHandler('s', 'sort ordering', self._show_sort_dialog),
      nyx.panel.KeyHandler('r', 'connection resolver', _pick_connection_resolver, 'auto' if resolver is None else resolver),
    ]

    if user_traffic_allowed.inbound:
      options.append(nyx.panel.KeyHandler('c', 'client locale usage summary', _show_client_locales))

    if user_traffic_allowed.outbound:
      options.append(nyx.panel.KeyHandler('e', 'exit port usage summary', _show_exiting_port_usage))

    return tuple(options)

  def submenu(self):
    """
    Submenu consisting of...

      Sorting...
      Resolver (Submenu)
    """

    tracker = nyx.tracker.get_connection_tracker()
    resolver_group = RadioGroup(tracker.set_custom_resolver, tracker.get_custom_resolver())

    return Submenu('Connections', [
      MenuItem('Sorting...', self._show_sort_dialog),
      Submenu('Resolver', [
        RadioMenuItem('auto', resolver_group, None),
        [RadioMenuItem(opt, resolver_group, opt) for opt in connection.Resolver],
      ]),
    ])

  def _draw(self, subwindow):
    controller = tor_controller()
    interface = nyx_interface()
    entries = self._entries

    lines = list(itertools.chain.from_iterable([entry.get_lines() for entry in entries]))
    is_showing_details = self._show_details and lines
    details_offset = DETAILS_HEIGHT + 1 if is_showing_details else 0
    selected, scroll = self._scroller.selection(lines, subwindow.height - details_offset - 1)

    if interface.is_paused():
      current_time = self._pause_time
    elif not controller.is_alive():
      current_time = controller.connection_time()
    else:
      current_time = time.time()

    is_scrollbar_visible = len(lines) > subwindow.height - details_offset - 1
    scroll_offset = 2 if is_scrollbar_visible else 0

    _draw_title(subwindow, entries, self._show_details)

    if is_showing_details:
      _draw_details(subwindow, selected)

      # draw a 'T' pipe if connecting with the scrollbar

      if is_scrollbar_visible:
        subwindow._addch(1, DETAILS_HEIGHT + 1, curses.ACS_TTEE)

    if is_scrollbar_visible:
      subwindow.scrollbar(1 + details_offset, scroll, len(lines))

    for line_number in range(scroll, len(lines)):
      y = line_number + details_offset + 1 - scroll
      _draw_line(subwindow, scroll_offset, y, lines[line_number], lines[line_number] == selected, subwindow.width - scroll_offset, current_time)

      if y >= subwindow.height:
        break

  def _update(self):
    """
    Fetches the newest resolved connections.
    """

    global LAST_RETRIEVED_CIRCUITS, LAST_RETRIEVED_HS_CONF

    conn_resolver = nyx.tracker.get_connection_tracker()
    resolution_count = conn_resolver.run_counter()

    # when first starting up wait a bit for initial results

    if resolution_count == 0:
      start_time = time.time()

      while True:
        resolution_count = conn_resolver.run_counter()

        if resolution_count != 0:
          break
        elif time.time() - start_time > 5:
          break
        elif self._halt:
          return
        else:
          time.sleep(nyx.PAUSE_TIME)

    controller = tor_controller()
    LAST_RETRIEVED_CIRCUITS = controller.get_circuits([])
    LAST_RETRIEVED_HS_CONF = controller.get_hidden_service_conf({})

    if not conn_resolver.is_alive():
      return  # if we're not fetching connections then this is a no-op
    elif resolution_count == self._last_resource_fetch:
      return  # no new connections to process

    new_entries = [Entry.from_connection(conn) for conn in conn_resolver.get_value()]

    for circ in LAST_RETRIEVED_CIRCUITS:
      # Skips established single-hop circuits (these are for directory
      # fetches, not client circuits)

      if not (circ.status == 'BUILT' and len(circ.path) == 1):
        new_entries.append(Entry.from_circuit(circ))

    # update stats for client and exit connections

    for entry in new_entries:
      line = entry.get_lines()[0]

      # This loop is the lengthiest part of our update. If our thread's stopped
      # we should abort further work.

      if self._halt:
        return

      if entry.is_private() and line.connection.remote_address not in self._counted_connections:
        if entry.get_type() == Category.INBOUND and line.locale:
          self._client_locale_usage[line.locale] = self._client_locale_usage.get(line.locale, 0) + 1
        elif entry.get_type() == Category.EXIT:
          self._exit_port_usage[line.connection.remote_port] = self._exit_port_usage.get(line.connection.remote_port, 0) + 1

        self._counted_connections.add(line.connection.remote_address)

    self._entries = sorted(new_entries, key = lambda entry: [entry.sort_value(attr) for attr in self._sort_order])
    self._last_resource_fetch = resolution_count

    if CONFIG['resolve_processes']:
      local_ports, remote_ports = [], []

      for entry in new_entries:
        line = entry.get_lines()[0]

        if entry.get_type() in (Category.SOCKS, Category.CONTROL):
          local_ports.append(line.connection.remote_port)
        elif entry.get_type() == Category.HIDDEN:
          remote_ports.append(line.connection.local_port)

      nyx.tracker.get_port_usage_tracker().query(local_ports, remote_ports)

    # clear cache of anything that hasn't been referenced in the last five minutes

    now = time.time()
    to_clear = [k for k, v in ENTRY_CACHE_REFERENCED.items() if (now - v) >= 300]

    for entry in to_clear:
      for cache in (ENTRY_CACHE, ENTRY_CACHE_REFERENCED):
        try:
          del cache[entry]
        except KeyError:
          pass

    self.redraw()


def _draw_title(subwindow, entries, showing_details):
  """
  Panel title with the number of connections we presently have.
  """

  if showing_details:
    subwindow.addstr(0, 0, 'Connection Details:', HIGHLIGHT)
  elif not entries:
    subwindow.addstr(0, 0, 'Connections:', HIGHLIGHT)
  else:
    counts = collections.Counter([entry.get_type() for entry in entries])
    count_labels = ['%i %s' % (counts[category], category.lower()) for category in Category if counts[category]]
    subwindow.addstr(0, 0, 'Connections (%s):' % ', '.join(count_labels), HIGHLIGHT)


def _draw_line(subwindow, x, y, line, is_selected, width, current_time):
  attr = [CONFIG['attr.connection.category_color'].get(line.entry.get_type(), WHITE)]
  attr.append(HIGHLIGHT if is_selected else NORMAL)

  subwindow.addstr(x, y, ' ' * (width - x), *attr)

  if line.line_type == LineType.CIRCUIT:
    if line.circuit.path[-1][0] == line.fingerprint:
      prefix = (ord(' '), curses.ACS_LLCORNER, curses.ACS_HLINE, ord(' '))
    else:
      prefix = (ord(' '), curses.ACS_VLINE, ord(' '), ord(' '))

    for char in prefix:
      x = subwindow._addch(x, y, char)
  else:
    x += 1  # offset from edge

  x = _draw_address_column(subwindow, x, y, line, attr)
  x = _draw_line_details(subwindow, x + 2, y, line, width - 57 - 20, attr)
  _draw_right_column(subwindow, max(x, width - 18), y, line, current_time, attr)


def _draw_address_column(subwindow, x, y, line, attr):
  src = tor_controller().get_info('address', line.connection.local_address)

  if line.line_type == LineType.CONNECTION:
    src = '%s:%s' % (src, line.connection.local_port)

  if line.line_type == LineType.CIRCUIT_HEADER and line.circuit.status != 'BUILT':
    dst = 'Building...'
  else:
    dst = '<scrubbed>' if line.entry.is_private() else line.connection.remote_address
    dst += ':%s' % line.connection.remote_port

    if line.entry.get_type() == Category.EXIT:
      purpose = connection.port_usage(line.connection.remote_port)

      if purpose:
        dst += ' (%s)' % str_tools.crop(purpose, 26 - len(dst) - 3)
    elif not tor_controller().is_geoip_unavailable() and not line.entry.is_private():
      dst += ' (%s)' % (line.locale if line.locale else '??')

  src = '%-21s' % src
  dst = '%-21s' % dst if tor_controller().is_geoip_unavailable() else '%-26s' % dst

  if line.entry.get_type() in (Category.INBOUND, Category.SOCKS, Category.CONTROL):
    dst, src = src, dst

  if line.line_type == LineType.CIRCUIT:
    return subwindow.addstr(x, y, dst, *attr)
  else:
    return subwindow.addstr(x, y, '%s  -->  %s' % (src, dst), *attr)


def _draw_details(subwindow, selected):
  """
  Shows detailed information about the selected connection.
  """

  attr = (CONFIG['attr.connection.category_color'].get(selected.entry.get_type(), WHITE), BOLD)

  if selected.line_type == LineType.CIRCUIT_HEADER and selected.circuit.status != 'BUILT':
    subwindow.addstr(2, 1, 'Building Circuit...', *attr)
  else:
    address = '<scrubbed>' if selected.entry.is_private() else selected.connection.remote_address
    subwindow.addstr(2, 1, 'address: %s:%s' % (address, selected.connection.remote_port), *attr)
    subwindow.addstr(2, 2, 'locale: %s' % (selected.locale if selected.locale and not selected.entry.is_private() else '??'), *attr)

    matches = nyx.tracker.get_consensus_tracker().get_relay_fingerprints(selected.connection.remote_address)

    if not matches:
      subwindow.addstr(2, 3, 'No consensus data found', *attr)
    elif len(matches) == 1 or selected.connection.remote_port in matches:
      controller = tor_controller()
      fingerprint = list(matches.values())[0] if len(matches) == 1 else matches[selected.connection.remote_port]
      router_status_entry = controller.get_network_status(fingerprint, None)

      subwindow.addstr(15, 2, 'fingerprint: %s' % fingerprint, *attr)

      if router_status_entry:
        dir_port_label = 'dirport: %s' % router_status_entry.dir_port if router_status_entry.dir_port else ''
        subwindow.addstr(2, 3, 'nickname: %-25s orport: %-10s %s' % (router_status_entry.nickname, router_status_entry.or_port, dir_port_label), *attr)
        subwindow.addstr(2, 4, 'published: %s' % router_status_entry.published.strftime("%H:%M %m/%d/%Y"), *attr)
        subwindow.addstr(2, 5, 'flags: %s' % ', '.join(router_status_entry.flags), *attr)

        server_descriptor = controller.get_server_descriptor(fingerprint, None)

        if server_descriptor:
          policy_label = server_descriptor.exit_policy.summary() if server_descriptor.exit_policy else 'unknown'
          subwindow.addstr(2, 6, 'exit policy: %s' % policy_label, *attr)
          subwindow.addstr(38, 4, 'os: %-14s version: %s' % (server_descriptor.operating_system, server_descriptor.tor_version), *attr)

          if server_descriptor.contact:
            subwindow.addstr(2, 7, 'contact: %s' % server_descriptor.contact, *attr)
    else:
      subwindow.addstr(2, 3, 'Multiple matches, possible fingerprints are:', *attr)

      for i, port in enumerate(sorted(matches.keys())):
        is_last_line, remaining_relays = i == 3, len(matches) - i

        if not is_last_line or remaining_relays == 1:
          subwindow.addstr(2, 4 + i, '%i. or port: %-5s fingerprint: %s' % (i + 1, port, matches[port]), *attr)
        else:
          subwindow.addstr(2, 4 + i, '... %i more' % remaining_relays, *attr)

        if is_last_line:
          break

  subwindow.box(0, 0, subwindow.width, DETAILS_HEIGHT + 2)


def _draw_line_details(subwindow, x, y, line, width, attr):
  if line.line_type == LineType.CIRCUIT_HEADER:
    comp = ['Purpose: %s' % line.circuit.purpose.capitalize(), ', Circuit ID: %s' % line.circuit.id]
  elif line.entry.get_type() in (Category.SOCKS, Category.HIDDEN, Category.CONTROL):
    try:
      port = line.connection.local_port if line.entry.get_type() == Category.HIDDEN else line.connection.remote_port
      process = nyx.tracker.get_port_usage_tracker().fetch(port)
      comp = ['%s (%s)' % (process.name, process.pid) if process.pid else process.name]
    except nyx.tracker.UnresolvedResult:
      comp = ['resolving...']
    except nyx.tracker.UnknownApplication:
      comp = ['UNKNOWN']
  else:
    comp = ['%-40s' % (line.fingerprint if line.fingerprint else 'UNKNOWN'), '  ' + (line.nickname if line.nickname else 'UNKNOWN')]

  for entry in comp:
    if width >= len(entry):
      x = subwindow.addstr(x, y, entry, *attr)
    else:
      return x

  return x


def _draw_right_column(subwindow, x, y, line, current_time, attr):
  if line.line_type == LineType.CIRCUIT:
    circ_path = [fp for fp, _ in line.circuit.path]
    circ_index = circ_path.index(line.fingerprint)

    if circ_index == len(circ_path) - 1:
      placement_type = 'End' if line.circuit.status == 'BUILT' else 'Extending'
    elif circ_index == 0:
      placement_type = 'Guard'
    else:
      placement_type = 'Middle'

    subwindow.addstr(x + 4, y, '%i / %s' % (circ_index + 1, placement_type), *attr)
  else:
    x = subwindow.addstr(x, y, '+' if line.connection.is_legacy else ' ', *attr)
    x = subwindow.addstr(x, y, '%5s' % str_tools.time_label(current_time - line.connection.start_time, 1), *attr)
    x = subwindow.addstr(x, y, ' (', *attr)
    x = subwindow.addstr(x, y, line.entry.get_type().upper(), BOLD, *attr)
    x = subwindow.addstr(x, y, ')', *attr)
