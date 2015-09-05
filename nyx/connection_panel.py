"""
Listing of the currently established connections tor has made.
"""

import re
import time
import collections
import curses
import datetime
import itertools
import threading

import nyx.popups
import nyx.util.tracker
import nyx.util.ui_tools

from nyx.util import panel, tor_controller, ui_tools

from stem.control import Listener, State
from stem.util import conf, connection, enum, str_tools

try:
  # added in python 3.2
  from functools import lru_cache
except ImportError:
  from stem.util.lru_cache import lru_cache

# height of the detail panel content, not counting top and bottom border

DETAILS_HEIGHT = 7

# listing types

Listing = enum.Enum(('IP_ADDRESS', 'IP Address'), 'FINGERPRINT', 'NICKNAME')

EXIT_USAGE_WIDTH = 15
UPDATE_RATE = 5  # rate in seconds at which we refresh

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
SortAttr = enum.Enum('CATEGORY', 'UPTIME', 'LISTING', 'IP_ADDRESS', 'PORT', 'FINGERPRINT', 'NICKNAME', 'COUNTRY')

# static data for listing format
# <src>  -->  <dst>  <etc><padding>

LABEL_FORMAT = '%s  -->  %s  %s%s'
LABEL_MIN_PADDING = 2  # min space between listing label and following data


def conf_handler(key, value):
  if key == 'features.connection.listing_type':
    return conf.parse_enum(key, value, Listing)
  elif key == 'features.connection.order':
    return conf.parse_enum_csv(key, value[0], SortAttr, 3)


CONFIG = conf.config_dict('nyx', {
  'attr.connection.category_color': {},
  'attr.connection.sort_color': {},
  'features.connection.resolveApps': True,
  'features.connection.listing_type': Listing.IP_ADDRESS,
  'features.connection.order': [
    SortAttr.CATEGORY,
    SortAttr.LISTING,
    SortAttr.UPTIME],
  'features.connection.showIps': True,
}, conf_handler)


def to_unix_time(dt):
  return (dt - datetime.datetime(1970, 1, 1)).total_seconds()


class Entry(object):
  @staticmethod
  @lru_cache()
  def from_connection(connection):
    return ConnectionEntry(connection)

  @staticmethod
  @lru_cache()
  def from_circuit(circuit):
    return CircuitEntry(circuit)

  def get_lines(self):
    """
    Provides individual lines of connection information.

    :returns: **list** of **ConnectionLine** concerning this entry
    """

    raise NotImplementedError('should be implemented by subclasses')

  def get_type(self):
    """
    Provides our best guess at the type of connection this is.

    :returns: **Category** for the connection's type
    """

    raise NotImplementedError('should be implemented by subclasses')

  def is_private(self):
    """
    Checks if information about this endpoint should be scrubbed. Relaying
    etiquette (and wiretapping laws) say these are bad things to look at so
    DON'T CHANGE THIS UNLESS YOU HAVE A DAMN GOOD REASON!

    :returns: **bool** indicating if connection information is sensive or not
    """

    raise NotImplementedError('should be implemented by subclasses')


class ConnectionEntry(Entry):
  def __init__(self, connection):
    self._connection = connection

  @lru_cache()
  def get_lines(self):
    return [ConnectionLine(self, self._connection)]

  @lru_cache()
  def get_type(self):
    controller = tor_controller()

    if self._connection.local_port in controller.get_ports(Listener.OR, []):
      return Category.INBOUND
    elif self._connection.local_port in controller.get_ports(Listener.DIR, []):
      return Category.INBOUND
    elif self._connection.local_port in controller.get_ports(Listener.SOCKS, []):
      return Category.SOCKS
    elif self._connection.local_port in controller.get_ports(Listener.CONTROL, []):
      return Category.CONTROL

    for hs_config in controller.get_hidden_service_conf({}).values():
      if self._connection.remote_port == hs_config['HiddenServicePort']:
        return Category.HIDDEN

    fingerprint = nyx.util.tracker.get_consensus_tracker().get_relay_fingerprint(self._connection.remote_address, self._connection.remote_port)

    if fingerprint:
      for circ in controller.get_circuits([]):
        if circ.path[0][0] == fingerprint and circ.status == 'BUILT':
          # Tor builds one-hop circuits to retrieve directory information.
          # If longer this is likely a connection to a guard.

          return Category.DIRECTORY if len(circ.path) == 1 else Category.CIRCUIT
    else:
      # not a known relay, might be an exit connection

      exit_policy = controller.get_exit_policy(None)

      if exit_policy and exit_policy.can_exit_to(self._connection.remote_address, self._connection.remote_port):
        return Category.EXIT

    return Category.OUTBOUND

  @lru_cache()
  def is_private(self):
    if not CONFIG['features.connection.showIps']:
      return True

    if self.get_type() == Category.INBOUND:
      controller = tor_controller()

      if controller.is_user_traffic_allowed().inbound:
        return len(nyx.util.tracker.get_consensus_tracker().get_all_relay_fingerprints(self._connection.remote_address)) == 0
    elif self.get_type() == Category.EXIT:
      # DNS connections exiting us aren't private (since they're hitting our
      # resolvers). Everything else is.

      return not (self._connection.remote_port == 53 and self._connection.protocol == 'udp')

    return False  # for everything else this isn't a concern


class CircuitEntry(Entry):
  def __init__(self, circuit):
    self._circuit = circuit

  @lru_cache()
  def get_lines(self):
    return [CircHeaderLine(self, self._circuit)] + [CircLine(self, self._circuit, fp) for fp, _ in self._circuit.path]

  def get_type(self):
    return Category.CIRCUIT

  def is_private(self):
    return False


class ConnectionLine(object):
  """
  Display component of the ConnectionEntry.
  """

  def __init__(self, entry, conn, include_port = True):
    self._entry = entry
    self.connection = conn

    # includes the port or expanded ip address field when displaying listing
    # information if true

    self.include_port = include_port

  def get_listing_prefix(self):
    """
    Provides a list of characters to be appended before the listing entry.
    """

    return ()

  def get_locale(self, default = None):
    """
    Provides the two letter country code for the remote endpoint.
    """

    return tor_controller().get_info('ip-to-country/%s' % self.connection.remote_address, default)

  def get_fingerprint(self, default = None):
    """
    Provides the fingerprint of this relay.
    """

    if self._entry.get_type() in (Category.OUTBOUND, Category.CIRCUIT, Category.DIRECTORY, Category.EXIT):
      my_fingerprint = nyx.util.tracker.get_consensus_tracker().get_relay_fingerprint(self.connection.remote_address, self.connection.remote_port)
      return my_fingerprint if my_fingerprint else default
    else:
      return default  # inbound connections don't have an ORPort we can resolve

  def get_nickname(self, default = None):
    """
    Provides the nickname of this relay.
    """

    nickname = nyx.util.tracker.get_consensus_tracker().get_relay_nickname(self.get_fingerprint())
    return nickname if nickname else default

  def get_listing_entry(self, width, current_time, listing_type):
    """
    Provides the tuple list for this connection's listing. Lines are composed
    of the following components:
      <src>  -->  <dst>     <etc>     <uptime> (<type>)

    Listing.IP_ADDRESS:
      src - <internal addr:port> --> <external addr:port>
      dst - <destination addr:port>
      etc - <fingerprint> <nickname>

    Listing.FINGERPRINT:
      src - localhost
      dst - <destination fingerprint>
      etc - <nickname> <destination addr:port>

    Listing.NICKNAME:
      src - <source nickname>
      dst - <destination nickname>
      etc - <fingerprint> <destination addr:port>

    Arguments:
      width       - maximum length of the line
      current_time - unix timestamp for what the results should consider to be
                    the current time
      listing_type - primary attribute we're listing connections by
    """

    # fetch our (most likely cached) display entry for the listing

    my_listing = self._get_listing_entry(width, listing_type)

    # fill in the current uptime and return the results

    time_prefix = '+' if self.connection.is_legacy else ' '

    time_label = time_prefix + '%5s' % str_tools.time_label(current_time - self.connection.start_time, 1)
    my_listing[2] = (time_label, my_listing[2][1])

    return my_listing

  @lru_cache()
  def _get_listing_entry(self, width, listing_type):
    entry_type = self._entry.get_type()

    # Lines are split into the following components in reverse:
    # init gap - " "
    # content  - "<src>  -->  <dst>     <etc>     "
    # time     - "<uptime>"
    # preType  - " ("
    # category - "<type>"
    # postType - ")   "

    line_format = nyx.util.ui_tools.get_color(CONFIG['attr.connection.category_color'].get(entry_type, 'white'))

    draw_entry = [(' ', line_format),
                  (self._get_listing_content(width - 19, listing_type), line_format),
                  ('      ', line_format),
                  (' (', line_format),
                  (entry_type.upper(), line_format | curses.A_BOLD),
                  (')' + ' ' * (9 - len(entry_type)), line_format)]

    return draw_entry

  @lru_cache()
  def get_details(self, width):
    """
    Provides details on the connection, correlated against available consensus
    data.

    Arguments:
      width - available space to display in
    """

    detail_format = (curses.A_BOLD, CONFIG['attr.connection.category_color'].get(self._entry.get_type(), 'white'))
    return [(line, detail_format) for line in self._get_detail_content(width)]

  def get_etc_content(self, width, listing_type):
    """
    Provides the optional content for the connection.

    Arguments:
      width       - maximum length of the line
      listing_type - primary attribute we're listing connections by
    """

    # for applications show the command/pid

    if self._entry.get_type() in (Category.SOCKS, Category.HIDDEN, Category.CONTROL):
      port = self.connection.local_port if self._entry.get_type() == Category.HIDDEN else self.connection.remote_port

      try:
        process = nyx.util.tracker.get_port_usage_tracker().fetch(port)
        display_label = '%s (%s)' % (process.name, process.pid) if process.pid else process.name
      except nyx.util.tracker.UnresolvedResult:
        display_label = 'resolving...'
      except nyx.util.tracker.UnknownApplication:
        display_label = 'UNKNOWN'

      if len(display_label) < width:
        return ('%%-%is' % width) % display_label
      else:
        return ''

    # for everything else display connection/consensus information

    destination_address = self.get_destination_label(26, include_locale = True)
    etc, used_space = '', 0

    if listing_type == Listing.IP_ADDRESS:
      if width > used_space + 42:
        # show fingerprint (column width: 42 characters)

        etc += '%-40s  ' % self.get_fingerprint('UNKNOWN')
        used_space += 42

      if width > used_space + 10:
        # show nickname (column width: remainder)

        nickname_space = width - used_space
        nickname_label = str_tools.crop(self.get_nickname('UNKNOWN'), nickname_space, 0)
        etc += ('%%-%is  ' % nickname_space) % nickname_label
        used_space += nickname_space + 2
    elif listing_type == Listing.FINGERPRINT:
      if width > used_space + 17:
        # show nickname (column width: min 17 characters, consumes any remaining space)

        nickname_space = width - used_space - 2

        # if there's room then also show a column with the destination
        # ip/port/locale (column width: 28 characters)

        is_locale_included = width > used_space + 45

        if is_locale_included:
          nickname_space -= 28

        nickname_label = str_tools.crop(self.get_nickname('UNKNOWN'), nickname_space, 0)
        etc += ('%%-%is  ' % nickname_space) % nickname_label
        used_space += nickname_space + 2

        if is_locale_included:
          etc += '%-26s  ' % destination_address
          used_space += 28
    else:
      if width > used_space + 42:
        # show fingerprint (column width: 42 characters)
        etc += '%-40s  ' % self.get_fingerprint('UNKNOWN')
        used_space += 42

      if width > used_space + 28:
        # show destination ip/port/locale (column width: 28 characters)
        etc += '%-26s  ' % destination_address
        used_space += 28

    return ('%%-%is' % width) % etc

  def _get_listing_content(self, width, listing_type):
    """
    Provides the source, destination, and extra info for our listing.

    Arguments:
      width       - maximum length of the line
      listing_type - primary attribute we're listing connections by
    """

    controller = tor_controller()
    my_type = self._entry.get_type()
    destination_address = self.get_destination_label(26, include_locale = True)

    # The required widths are the sum of the following:
    # - room for LABEL_FORMAT and LABEL_MIN_PADDING (11 characters)
    # - base data for the listing
    # - that extra field plus any previous

    used_space = len(LABEL_FORMAT % tuple([''] * 4)) + LABEL_MIN_PADDING
    local_port = ':%s' % self.connection.local_port if self.include_port else ''

    src, dst, etc = '', '', ''

    if listing_type == Listing.IP_ADDRESS:
      my_external_address = controller.get_info('address', self.connection.local_address)

      # Show our external address if it's going through tor.

      if my_type not in (Category.SOCKS, Category.HIDDEN, Category.CONTROL):
        src_address = my_external_address + local_port
      else:
        src_address = self.connection.local_address + local_port

      if my_type in (Category.SOCKS, Category.CONTROL):
        # Like inbound connections these need their source and destination to
        # be swapped. However, this only applies when listing by IP (their
        # fingerprint and nickname are both for us). Reversing the fields here
        # to keep the same column alignments.

        src = '%-21s' % destination_address
        dst = '%-26s' % src_address
      else:
        src = '%-21s' % src_address  # ip:port = max of 21 characters
        dst = '%-26s' % destination_address  # ip:port (xx) = max of 26 characters

      used_space += len(src) + len(dst)  # base data requires 47 characters

      etc = self.get_etc_content(width - used_space, listing_type)
      used_space += len(etc)
    elif listing_type == Listing.FINGERPRINT:
      src = 'localhost'
      dst = '%-40s' % ('localhost' if my_type == Category.CONTROL else self.get_fingerprint('UNKNOWN'))

      used_space += len(src) + len(dst)  # base data requires 49 characters

      etc = self.get_etc_content(width - used_space, listing_type)
      used_space += len(etc)
    else:
      # base data requires 50 min characters
      src = controller.get_conf('nickname', 'UNKNOWN')
      dst = controller.get_conf('nickname', 'UNKNOWN') if my_type == Category.CONTROL else self.get_nickname('UNKNOWN')

      min_base_space = 50

      etc = self.get_etc_content(width - used_space - min_base_space, listing_type)
      used_space += len(etc)

      base_space = width - used_space
      used_space = width  # prevents padding at the end

      if len(src) + len(dst) > base_space:
        src = str_tools.crop(src, base_space / 3)
        dst = str_tools.crop(dst, base_space - len(src))

      # pads dst entry to its max space

      dst = ('%%-%is' % (base_space - len(src))) % dst

    if my_type == Category.INBOUND:
      src, dst = dst, src

    padding = ' ' * (width - used_space + LABEL_MIN_PADDING)

    return LABEL_FORMAT % (src, dst, etc, padding)

  def _get_detail_content(self, width):
    """
    Provides a list with detailed information for this connection.

    Arguments:
      width - max length of lines
    """

    lines = [''] * 7
    lines[0] = 'address: %s' % self.get_destination_label(width - 11)
    lines[1] = 'locale: %s' % ('??' if self._entry.is_private() else self.get_locale('??'))

    # Remaining data concerns the consensus results, with three possible cases:
    # - if there's a single match then display its details
    # - if there's multiple potential relays then list all of the combinations
    #   of ORPorts / Fingerprints
    # - if no consensus data is available then say so (probably a client or
    #   exit connection)

    fingerprint = self.get_fingerprint()
    controller = tor_controller()

    if fingerprint:
      lines[1] = '%-13sfingerprint: %s' % (lines[1], fingerprint)  # append fingerprint to second line

      router_status_entry = controller.get_network_status(fingerprint, None)
      server_descriptor = controller.get_server_descriptor(fingerprint, None)

      if router_status_entry:
        dir_port_label = 'dirport: %s' % router_status_entry.dir_port if router_status_entry.dir_port else ''
        lines[2] = 'nickname: %-25s orport: %-10s %s' % (router_status_entry.nickname, router_status_entry.or_port, dir_port_label)
        lines[3] = 'published: %s' % router_status_entry.published.strftime("%H:%M %m/%d/%Y")
        lines[4] = 'flags: %s' % ', '.join(router_status_entry.flags)

      if server_descriptor:
        policy_label = server_descriptor.exit_policy.summary() if server_descriptor.exit_policy else 'unknown'
        lines[5] = 'exit policy: %s' % policy_label
        lines[3] = '%-35s os: %-14s version: %s' % (lines[3], server_descriptor.operating_system, server_descriptor.tor_version)

        if server_descriptor.contact:
          lines[6] = 'contact: %s' % server_descriptor.contact
    else:
      all_matches = nyx.util.tracker.get_consensus_tracker().get_all_relay_fingerprints(self.connection.remote_address)

      if all_matches:
        # multiple matches
        lines[2] = 'Multiple matches, possible fingerprints are:'

        for i in range(len(all_matches)):
          is_last_line = i == 3

          relay_port, relay_fingerprint = all_matches[i]
          line_text = '%i. or port: %-5s fingerprint: %s' % (i + 1, relay_port, relay_fingerprint)

          # if there's multiple lines remaining at the end then give a count

          remaining_relays = len(all_matches) - i

          if is_last_line and remaining_relays > 1:
            line_text = '... %i more' % remaining_relays

          lines[3 + i] = line_text

          if is_last_line:
            break
      else:
        # no consensus entry for this ip address
        lines[2] = 'No consensus data found'

    # crops any lines that are too long

    for i in range(len(lines)):
      lines[i] = str_tools.crop(lines[i], width - 2)

    return lines

  def get_destination_label(self, max_length, include_locale = False):
    """
    Provides a short description of the destination. This is made up of two
    components, the base <ip addr>:<port> and an extra piece of information in
    parentheses. The IP address is scrubbed from private connections.

    Extra information is...
    - the port's purpose for exit connections
    - the locale, the address isn't private and isn't on the local network
    - nothing otherwise

    Arguments:
      max_length       - maximum length of the string returned
      include_locale   - possibly includes the locale
    """

    # destination of the connection

    address_label = '<scrubbed>' if self._entry.is_private() else self.connection.remote_address
    port_label = ':%s' % self.connection.remote_port
    destination_address = address_label + port_label

    # Only append the extra info if there's at least a couple characters of
    # space (this is what's needed for the country codes).

    if len(destination_address) + 5 <= max_length:
      space_available = max_length - len(destination_address) - 3

      if self._entry.get_type() == Category.EXIT:
        purpose = connection.port_usage(self.connection.remote_port)

        if purpose:
          # BitTorrent is a common protocol to truncate, so just use "Torrent"
          # if there's not enough room.

          if len(purpose) > space_available and purpose == 'BitTorrent':
            purpose = 'Torrent'

          # crops with a hyphen if too long

          purpose = str_tools.crop(purpose, space_available, ending = str_tools.Ending.HYPHEN)

          destination_address += ' (%s)' % purpose
      elif not connection.is_private_address(self.connection.remote_address):
        extra_info = []

        if include_locale and not tor_controller().is_geoip_unavailable():
          foreign_locale = self.get_locale('??')
          extra_info.append(foreign_locale)
          space_available -= len(foreign_locale) + 2

        if extra_info:
          destination_address += ' (%s)' % ', '.join(extra_info)

    return destination_address[:max_length]


class CircHeaderLine(ConnectionLine):
  """
  Initial line of a client entry. This has the same basic format as connection
  lines except that its etc field has circuit attributes.
  """

  def __init__(self, entry, circ):
    if circ.status == 'BUILT':
      self._remote_fingerprint = circ.path[-1][0]
      exit_address, exit_port = nyx.util.tracker.get_consensus_tracker().get_relay_address(self._remote_fingerprint, ('192.168.0.1', 0))
      self.is_built = True
    else:
      exit_address, exit_port = '0.0.0.0', 0
      self.is_built = False
      self._remote_fingerprint = None

    ConnectionLine.__init__(self, entry, nyx.util.tracker.Connection(to_unix_time(circ.created), False, '127.0.0.1', 0, exit_address, exit_port, 'tcp'), include_port = False)
    self.circuit = circ

  def get_fingerprint(self, default = None):
    return self._remote_fingerprint if self._remote_fingerprint else ConnectionLine.get_fingerprint(self, default)

  def get_destination_label(self, max_length, include_locale = False):
    if not self.is_built:
      return 'Building...'

    return ConnectionLine.get_destination_label(self, max_length, include_locale)

  def get_etc_content(self, width, listing_type):
    """
    Attempts to provide all circuit related stats. Anything that can't be
    shown completely (not enough room) is dropped.
    """

    etc_attr = ['Purpose: %s' % self.circuit.purpose.capitalize(), 'Circuit ID: %s' % self.circuit.id]

    for i in range(len(etc_attr), -1, -1):
      etc_label = ', '.join(etc_attr[:i])

      if len(etc_label) <= width:
        return ('%%-%is' % width) % etc_label

    return ''

  @lru_cache()
  def get_details(self, width):
    if not self.is_built:
      detail_format = (curses.A_BOLD, CONFIG['attr.connection.category_color'].get(self._entry.get_type(), 'white'))
      return [('Building Circuit...', detail_format)]
    else:
      return ConnectionLine.get_details(self, width)


class CircLine(ConnectionLine):
  """
  An individual hop in a circuit. This overwrites the displayed listing, but
  otherwise makes use of the ConnectionLine attributes (for the detail display,
  caching, etc).
  """

  def __init__(self, entry, circ, fingerprint):
    relay_ip, relay_port = nyx.util.tracker.get_consensus_tracker().get_relay_address(fingerprint, ('192.168.0.1', 0))
    ConnectionLine.__init__(self, entry, nyx.util.tracker.Connection(to_unix_time(circ.created), False, '127.0.0.1', 0, relay_ip, relay_port, 'tcp'), include_port = False)
    self._fingerprint = fingerprint
    self._is_last = False

    circ_path = [path_entry[0] for path_entry in circ.path]
    circ_index = circ_path.index(fingerprint)

    if circ_index == len(circ_path) - 1:
      placement_type = 'Exit' if circ.status == 'BUILT' else 'Extending'
      self._is_last = True
    elif circ_index == 0:
      placement_type = 'Guard'
    else:
      placement_type = 'Middle'

    self.placement_label = '%i / %s' % (circ_index + 1, placement_type)

  def get_fingerprint(self, default = None):
    self._fingerprint

  def get_listing_prefix(self):
    if self._is_last:
      return (ord(' '), curses.ACS_LLCORNER, curses.ACS_HLINE, ord(' '))
    else:
      return (ord(' '), curses.ACS_VLINE, ord(' '), ord(' '))

  def get_listing_entry(self, width, current_time, listing_type):
    """
    Provides the [(msg, attr)...] listing for this relay in the circuilt
    listing. Lines are composed of the following components:
      <bracket> <dst> <etc> <placement label>

    The dst and etc entries largely match their ConnectionEntry counterparts.

    Arguments:
      width       - maximum length of the line
      current_time - the current unix time (ignored)
      listing_type - primary attribute we're listing connections by
    """

    return self._get_listing_entry(width, listing_type)

  @lru_cache()
  def _get_listing_entry(self, width, listing_type):
    line_format = nyx.util.ui_tools.get_color(CONFIG['attr.connection.category_color'].get(self._entry.get_type(), 'white'))

    # The required widths are the sum of the following:
    # initial space (1 character)
    # bracketing (3 characters)
    # placement_label (14 characters)
    # gap between etc and placement label (5 characters)

    baseline_space = 14 + 5

    dst, etc = '', ''

    if listing_type == Listing.IP_ADDRESS:
      # dst width is derived as:
      # src (21) + dst (26) + divider (7) + right gap (2) - bracket (3) = 53 char

      dst = '%-53s' % self.get_destination_label(53, include_locale = True)

      # fills the nickname into the empty space here

      dst = '%s%-25s   ' % (dst[:25], str_tools.crop(self.get_nickname('UNKNOWN'), 25, 0))

      etc = self.get_etc_content(width - baseline_space - len(dst), listing_type)
    elif listing_type == Listing.FINGERPRINT:
      # dst width is derived as:
      # src (9) + dst (40) + divider (7) + right gap (2) - bracket (3) = 55 char

      dst = '%-55s' % self.get_fingerprint('UNKNOWN')
      etc = self.get_etc_content(width - baseline_space - len(dst), listing_type)
    else:
      # min space for the nickname is 56 characters

      etc = self.get_etc_content(width - baseline_space - 56, listing_type)
      dst_layout = '%%-%is' % (width - baseline_space - len(etc))
      dst = dst_layout % self.get_nickname('UNKNOWN')

    return ((dst + etc, line_format),
            (' ' * (width - baseline_space - len(dst) - len(etc) + 5), line_format),
            ('%-14s' % self.placement_label, line_format))


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

    # mark the initially exitsing connection uptimes as being estimates

    for entry in self._entries:
      if isinstance(entry, ConnectionEntry):
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

        ordering_keys = [SortAttr.keys()[SortAttr.index_of(v)] for v in ordering]
        nyx_config.set('features.connection.order', ', '.join(ordering_keys))

      def sort_value(entry, attr):
        if attr == SortAttr.LISTING:
          if self.get_listing_type() == Listing.IP_ADDRESS:
            attr = SortAttr.IP_ADDRESS
          elif self.get_listing_type() == Listing.FINGERPRINT:
            attr = SortAttr.FINGERPRINT
          elif self.get_listing_type() == Listing.NICKNAME:
            attr = SortAttr.NICKNAME

        connection_line = entry.get_lines()[0]

        if attr == SortAttr.IP_ADDRESS:
          if entry.is_private():
            return 255 ** 4  # orders at the end

          ip_value = 0

          for octet in connection_line.connection.remote_address.split('.'):
            ip_value = ip_value * 255 + int(octet)

          return ip_value * 65536 + connection_line.connection.remote_port
        elif attr == SortAttr.PORT:
          return connection_line.connection.remote_port
        elif attr == SortAttr.FINGERPRINT:
          return connection_line.get_fingerprint('UNKNOWN')
        elif attr == SortAttr.NICKNAME:
          return connection_line.get_nickname('z' * 20)
        elif attr == SortAttr.CATEGORY:
          return Category.index_of(entry.get_type())
        elif attr == SortAttr.UPTIME:
          return connection_line.connection.start_time
        elif attr == SortAttr.COUNTRY:
          return '' if entry.is_private() else connection_line.get_locale('')
        else:
          return ''

      self._entries.sort(key = lambda i: [sort_value(i, attr) for attr in CONFIG['features.connection.order']])
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

      if SortAttr.LISTING in CONFIG['features.connection.order']:
        self.set_sort_order()

  def show_sort_dialog(self):
    """
    Provides the sort dialog for our connections.
    """

    # set ordering for connection options

    title_label = 'Connection Ordering:'
    options = list(SortAttr)
    old_selection = CONFIG['features.connection.order']
    option_colors = dict([(attr, CONFIG['attr.connection.sort_color'].get(attr, 'white')) for attr in options])
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
        options = list(Listing)

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

          color = CONFIG['attr.connection.category_color'].get(selection.get_type(), 'white')
          fingerprint = selection.get_fingerprint()
          is_close_key = lambda key: key.is_selection() or key.match('d') or key.match('left') or key.match('right')
          key = nyx.popups.show_descriptor_popup(fingerprint, color, self.max_x, is_close_key)

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
      counts = collections.Counter([entry.get_type() for entry in entries])
      count_labels = ['%i %s' % (counts[category], category.lower()) for category in Category if counts[category]]
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
    current_resolution_count = conn_resolver.run_counter()

    if not conn_resolver.is_alive():
      return  # if we're not fetching connections then this is a no-op
    elif current_resolution_count == self._last_resource_fetch:
      return  # no new connections to process

    new_entries = [Entry.from_connection(conn) for conn in conn_resolver.get_value()]

    for circ in tor_controller().get_circuits([]):
      # Skips established single-hop circuits (these are for directory
      # fetches, not client circuits)

      if not (circ.status == 'BUILT' and len(circ.path) == 1):
        new_entries.append(Entry.from_circuit(circ))

    with self._vals_lock:
      # update stats for client and exit connections
      # TODO: this is counting connections each time they're resolved - totally broken :(

      for entry in new_entries:
        entry_line = entry.get_lines()[0]

        if entry.is_private():
          if entry.get_type() == Category.INBOUND:
            client_locale = entry_line.get_locale(None)

            if client_locale:
              self._client_locale_usage[client_locale] = self._client_locale_usage.get(client_locale, 0) + 1
          elif entry.get_type() == Category.EXIT:
            exit_port = entry_line.connection.remote_port
            self._exit_port_usage[exit_port] = self._exit_port_usage.get(exit_port, 0) + 1

      self._entries, self._entry_lines = new_entries, list(itertools.chain.from_iterable([entry.get_lines() for entry in new_entries]))

      self.set_sort_order()
      self._last_resource_fetch = current_resolution_count

    if CONFIG['features.connection.resolveApps']:
      local_ports, remote_ports = [], []

      for entry in new_entries:
        line = entry.get_lines()[0]

        if entry.get_type() in (Category.SOCKS, Category.CONTROL):
          local_ports.append(line.connection.remote_port)
        elif entry.get_type() == Category.HIDDEN:
          remote_ports.append(line.connection.local_port)

      nyx.util.tracker.get_port_usage_tracker().query(local_ports, remote_ports)
