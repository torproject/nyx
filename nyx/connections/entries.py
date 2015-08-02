"""
Interface for entries in the connection panel. These consist of two parts: the
entry itself (ie, Tor connection, client circuit, etc) and the lines it
consists of in the listing.
"""

import datetime

from nyx.util import tor_controller

from stem.control import Listener
from stem.util import conf, enum

# attributes we can list entries by

ListingType = enum.Enum(('IP_ADDRESS', 'IP Address'), 'FINGERPRINT', 'NICKNAME')

SortAttr = enum.Enum('CATEGORY', 'UPTIME', 'LISTING', 'IP_ADDRESS', 'PORT', 'FINGERPRINT', 'NICKNAME', 'COUNTRY')

SORT_COLORS = {
  SortAttr.CATEGORY: 'red',
  SortAttr.UPTIME: 'yellow',
  SortAttr.LISTING: 'green',
  SortAttr.IP_ADDRESS: 'blue',
  SortAttr.PORT: 'blue',
  SortAttr.FINGERPRINT: 'cyan',
  SortAttr.NICKNAME: 'cyan',
  SortAttr.COUNTRY: 'blue',
}

# maximum number of ports a system can have

PORT_COUNT = 65536

# sort value for scrubbed ip addresses

SCRUBBED_IP_VAL = 255 ** 4
ADDRESS_CACHE = {}

CONFIG = conf.config_dict('nyx', {
  'features.connection.showIps': True,
})


def to_unix_time(dt):
  return (dt - datetime.datetime(1970, 1, 1)).total_seconds()


def address_to_int(address):
  if address not in ADDRESS_CACHE:
    ip_value = 0

    for comp in address.split('.'):
      ip_value *= 255
      ip_value += int(comp)

    ADDRESS_CACHE[address] = ip_value

  return ADDRESS_CACHE[address]


class ConnectionPanelEntry:
  def __init__(self, connection_type, start_time):
    self.lines = []
    self._connection_type = connection_type
    self._start_time = start_time

  @staticmethod
  def from_connection(conn):
    import nyx.connections.conn_entry

    entry = ConnectionPanelEntry(get_type(conn), conn.start_time)
    entry.lines = [nyx.connections.conn_entry.ConnectionLine(entry, conn)]
    return entry

  @staticmethod
  def from_circuit(circ):
    import nyx.connections.circ_entry
    import nyx.connections.conn_entry
    import nyx.util.tracker

    entry = ConnectionPanelEntry(nyx.connections.conn_entry.Category.CIRCUIT, to_unix_time(circ.created))
    entry.lines = [nyx.connections.circ_entry.CircHeaderLine(entry, circ)]

    path = [path_entry[0] for path_entry in circ.path]

    if circ.status == 'BUILT':
      exit_ip, exit_port = nyx.util.tracker.get_consensus_tracker().get_relay_address(path[-1], ('192.168.0.1', 0))
      entry.lines[0].set_exit(exit_ip, exit_port, path[-1])

    for i, relay_fingerprint in enumerate(path):
      relay_ip, relay_port = nyx.util.tracker.get_consensus_tracker().get_relay_address(relay_fingerprint, ('192.168.0.1', 0))

      if i == len(path) - 1:
        placement_type = 'Exit' if circ.status == 'BUILT' else 'Extending'
      elif i == 0:
        placement_type = 'Guard'
      else:
        placement_type = 'Middle'

      placement_label = '%i / %s' % (i + 1, placement_type)

      entry.lines.append(nyx.connections.circ_entry.CircLine(entry, relay_ip, relay_port, relay_fingerprint, placement_label, to_unix_time(circ.created)))

    entry.lines[-1].is_last = True

    return entry

  def get_type(self):
    """
    Provides our best guess at the current type of the connection. This
    depends on consensus results, our current client circuits, etc.
    """

    return self._connection_type

  def is_private(self):
    """
    Returns true if the endpoint is private, possibly belonging to a client
    connection or exit traffic.

    This is used to scrub private information from the interface. Relaying
    etiquette (and wiretapping laws) say these are bad things to look at so
    DON'T CHANGE THIS UNLESS YOU HAVE A DAMN GOOD REASON!
    """

    import nyx.connections.conn_entry
    import nyx.util.tracker

    if not CONFIG['features.connection.showIps']:
      return True

    if self.get_type() == nyx.connections.conn_entry.Category.INBOUND:
      controller = tor_controller()

      if controller.is_user_traffic_allowed().inbound:
        return len(nyx.util.tracker.get_consensus_tracker().get_all_relay_fingerprints(self.connection.remote_address)) == 0
    elif self.get_type() == nyx.connections.conn_entry.Category.EXIT:
      # DNS connections exiting us aren't private (since they're hitting our
      # resolvers). Everything else, however, is.

      return self.connection.remote_port != 53 or self.connection.protocol != 'udp'

    # for everything else this isn't a concern

    return False

  def get_lines(self):
    """
    Provides the individual lines in the connection listing.
    """

    return self.lines

  def get_sort_values(self, sort_attrs, listing_type):
    """
    Provides the value used in comparisons to sort based on the given
    attribute.

    Arguments:
      sort_attrs   - list of SortAttr values for the field being sorted on
      listing_type - ListingType enumeration for the attribute we're listing
                    entries by
    """

    return [self.get_sort_value(attr, listing_type) for attr in sort_attrs]

  def get_sort_value(self, attr, listing_type):
    """
    Provides the value of a single attribute used for sorting purposes.
    """

    connection_line = self.lines[0]

    if attr == SortAttr.IP_ADDRESS:
      if self.is_private():
        return SCRUBBED_IP_VAL  # orders at the end

      return address_to_int(connection_line.connection.remote_address)
    elif attr == SortAttr.PORT:
      return connection_line.connection.remote_port
    elif attr == SortAttr.FINGERPRINT:
      return connection_line.get_fingerprint('UNKNOWN')
    elif attr == SortAttr.NICKNAME:
      my_nickname = connection_line.get_nickname()

      if my_nickname:
        return my_nickname.lower()
      else:
        return 'z' * 20  # orders at the end
    elif attr == SortAttr.CATEGORY:
      import nyx.connections.conn_entry
      return nyx.connections.conn_entry.Category.index_of(self.get_type())
    elif attr == SortAttr.UPTIME:
      return self._start_time
    elif attr == SortAttr.COUNTRY:
      if connection_line.connection.is_private_address(self.lines[0].connection.remote_address):
        return ''
      else:
        return connection_line.get_locale('')
    elif attr == SortAttr.LISTING:
      if listing_type == ListingType.IP_ADDRESS:
        # uses the IP address as the primary value, and port as secondary
        sort_value = self.get_sort_value(SortAttr.IP_ADDRESS, listing_type) * PORT_COUNT
        sort_value += self.get_sort_value(SortAttr.PORT, listing_type)
        return sort_value
      elif listing_type == ListingType.FINGERPRINT:
        return self.get_sort_value(SortAttr.FINGERPRINT, listing_type)
      elif listing_type == ListingType.NICKNAME:
        return self.get_sort_value(SortAttr.NICKNAME, listing_type)
    else:
      return ''


class ConnectionPanelLine:
  """
  Individual line in the connection panel listing.
  """

  def __init__(self):
    # cache for displayed information
    self._listing_cache = None
    self._listing_cache_args = (None, None)

    self._details_cache = None
    self._details_cache_args = None

    self._descriptor_cache = None
    self._descriptor_cache_args = None

  def get_listing_prefix(self):
    """
    Provides a list of characters to be appended before the listing entry.
    """

    return ()

  def get_listing_entry(self, width, current_time, listing_type):
    """
    Provides a [(msg, attr)...] tuple list for contents to be displayed in the
    connection panel listing.

    Arguments:
      width       - available space to display in
      current_time - unix timestamp for what the results should consider to be
                    the current time (this may be ignored due to caching)
      listing_type - ListingType enumeration for the highest priority content
                    to be displayed
    """

    if self._listing_cache_args != (width, listing_type):
      self._listing_cache = self._get_listing_entry(width, current_time, listing_type)
      self._listing_cache_args = (width, listing_type)

    return self._listing_cache

  def _get_listing_entry(self, width, current_time, listing_type):
    # implementation of get_listing_entry
    return None

  def get_details(self, width):
    """
    Provides a list of [(msg, attr)...] tuple listings with detailed
    information for this connection.

    Arguments:
      width - available space to display in
    """

    if self._details_cache_args != width:
      self._details_cache = self._get_details(width)
      self._details_cache_args = width

    return self._details_cache

  def _get_details(self, width):
    # implementation of get_details
    return []


def get_type(connection):
  import nyx.connections.conn_entry
  import nyx.util.tracker

  controller = tor_controller()

  my_hidden_service_ports = []  # ports belonging to our hidden service configuation

  for hs_config in controller.get_hidden_service_conf({}).values():
    my_hidden_service_ports += [entry[2] for entry in hs_config['HiddenServicePort']]

  if connection.local_port in controller.get_ports(Listener.OR, []):
    return nyx.connections.conn_entry.Category.INBOUND
  elif connection.local_port in controller.get_ports(Listener.DIR, []):
    return nyx.connections.conn_entry.Category.INBOUND
  elif connection.local_port in controller.get_ports(Listener.SOCKS, []):
    return nyx.connections.conn_entry.Category.SOCKS
  elif connection.remote_port in my_hidden_service_ports:
    return nyx.connections.conn_entry.Category.HIDDEN
  elif connection.local_port in controller.get_ports(Listener.CONTROL, []):
    return nyx.connections.conn_entry.Category.CONTROL
  else:
    destination_fingerprint = nyx.util.tracker.get_consensus_tracker().get_relay_fingerprint(connection.remote_address, connection.remote_port)

    if not destination_fingerprint:
      # Not a known relay. This might be an exit connection.

      exit_policy = controller.get_exit_policy(None)

      if exit_policy and exit_policy.can_exit_to(connection.remote_address, connection.remote_port):
        return nyx.connections.conn_entry.Category.EXIT
    else:
      for circ in controller.get_circuits([]):
        if circ.path[0][0] == destination_fingerprint and circ.status == 'BUILT':
          # Tor builds one-hop circuits to retrieve directory information.
          # If longer this is likely a connection to a guard.

          return nyx.connections.conn_entry.Category.DIRECTORY if len(circ.path) == 1 else nyx.connections.conn_entry.Category.CIRCUIT

    return nyx.connections.conn_entry.Category.OUTBOUND
