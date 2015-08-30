"""
Interface for entries in the connection panel. These consist of two parts: the
entry itself (ie, Tor connection, client circuit, etc) and the lines it
consists of in the listing.
"""

import nyx.util.tracker

from nyx.util import tor_controller

from stem.control import Listener
from stem.util import conf, enum

try:
  # added in python 3.2
  from functools import lru_cache
except ImportError:
  from stem.util.lru_cache import lru_cache

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

CONFIG = conf.config_dict('nyx', {
  'features.connection.showIps': True,
})


class Entry(object):
  def __init__(self, connection_type):
    self._lines = []
    self._connection_type = connection_type

  @staticmethod
  def from_connection(conn):
    import nyx.connections.conn_entry

    entry = Entry(get_type(conn))
    entry._lines = [nyx.connections.conn_entry.ConnectionLine(entry, conn)]
    return entry

  @staticmethod
  def from_circuit(circ):
    import nyx.connections.circ_entry
    import nyx.connections.conn_entry

    entry = Entry(nyx.connections.conn_entry.Category.CIRCUIT)
    entry._lines = [nyx.connections.circ_entry.CircHeaderLine(entry, circ)]

    for fingerprint, _ in circ.path:
      entry._lines.append(nyx.connections.circ_entry.CircLine(entry, circ, fingerprint))

    return entry

  def get_type(self):
    """
    Provides our best guess at the type of connection this is.

    :returns: **Category** for the connection's type
    """

    return self._connection_type

  @lru_cache()
  def is_private(self):
    """
    Checks if information about this endpoint should be scrubbed. Relaying
    etiquette (and wiretapping laws) say these are bad things to look at so
    DON'T CHANGE THIS UNLESS YOU HAVE A DAMN GOOD REASON!

    :returns: **bool** indicating if connection information is sensive or not
    """

    import nyx.connections.conn_entry

    if not CONFIG['features.connection.showIps']:
      return True

    connection = self._lines[0].connection

    if self.get_type() == nyx.connections.conn_entry.Category.INBOUND:
      controller = tor_controller()

      if controller.is_user_traffic_allowed().inbound:
        return len(nyx.util.tracker.get_consensus_tracker().get_all_relay_fingerprints(connection.remote_address)) == 0
    elif self.get_type() == nyx.connections.conn_entry.Category.EXIT:
      # DNS connections exiting us aren't private (since they're hitting our
      # resolvers). Everything else is.

      return connection.remote_port != 53 or connection.protocol != 'udp'

    return False  # for everything else this isn't a concern

  def get_lines(self):
    """
    Provides individual lines of connection information.

    :returns: **list** of **ConnectionLine** concerning this entry
    """

    return self._lines

  @lru_cache()
  def get_sort_value(self, attr):
    """
    Value for sorting by a given attribute.

    :param SortAtt attr: attribute to be sorted by

    :returns: comparable object by the given attribute
    """

    connection_line = self._lines[0]

    if attr == SortAttr.IP_ADDRESS:
      if self.is_private():
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
      import nyx.connections.conn_entry
      return nyx.connections.conn_entry.Category.index_of(self.get_type())
    elif attr == SortAttr.UPTIME:
      return connection_line.connection.start_time
    elif attr == SortAttr.COUNTRY:
      return '' if self.is_private() else connection_line.get_locale('')
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
  from nyx.connections.conn_entry import Category
  controller = tor_controller()

  if connection.local_port in controller.get_ports(Listener.OR, []):
    return Category.INBOUND
  elif connection.local_port in controller.get_ports(Listener.DIR, []):
    return Category.INBOUND
  elif connection.local_port in controller.get_ports(Listener.SOCKS, []):
    return Category.SOCKS
  elif connection.local_port in controller.get_ports(Listener.CONTROL, []):
    return Category.CONTROL

  for hs_config in controller.get_hidden_service_conf({}).values():
    if connection.remote_port == hs_config['HiddenServicePort']:
      return Category.HIDDEN

  fingerprint = nyx.util.tracker.get_consensus_tracker().get_relay_fingerprint(connection.remote_address, connection.remote_port)

  if fingerprint:
    for circ in controller.get_circuits([]):
      if circ.path[0][0] == fingerprint and circ.status == 'BUILT':
        # Tor builds one-hop circuits to retrieve directory information.
        # If longer this is likely a connection to a guard.

        return Category.DIRECTORY if len(circ.path) == 1 else Category.CIRCUIT
  else:
    # not a known relay, might be an exit connection

    exit_policy = controller.get_exit_policy(None)

    if exit_policy and exit_policy.can_exit_to(connection.remote_address, connection.remote_port):
      return Category.EXIT

  return Category.OUTBOUND
