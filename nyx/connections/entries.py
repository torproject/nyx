"""
Interface for entries in the connection panel. These consist of two parts: the
entry itself (ie, Tor connection, client circuit, etc) and the lines it
consists of in the listing.
"""

import nyx.util.tracker

from nyx.util import tor_controller

from stem.control import Listener
from stem.util import conf

try:
  # added in python 3.2
  from functools import lru_cache
except ImportError:
  from stem.util.lru_cache import lru_cache

CONFIG = conf.config_dict('nyx', {
  'features.connection.showIps': True,
})


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
    import nyx.connections.conn_entry
    return [nyx.connections.conn_entry.ConnectionLine(self, self._connection)]

  @lru_cache()
  def get_type(self):
    from nyx.connections.conn_panel import Category
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
    from nyx.connections.conn_panel import Category

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
    from nyx.connections.circ_entry import CircHeaderLine, CircLine
    return [CircHeaderLine(self, self._circuit)] + [CircLine(self, self._circuit, fp) for fp, _ in self._circuit.path]

  def get_type(self):
    from nyx.connections.conn_panel import Category
    return Category.CIRCUIT

  def is_private(self):
    return False


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
