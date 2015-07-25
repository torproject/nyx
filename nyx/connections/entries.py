"""
Interface for entries in the connection panel. These consist of two parts: the
entry itself (ie, Tor connection, client circuit, etc) and the lines it
consists of in the listing.
"""

import datetime

from stem.util import enum

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


def to_unix_time(dt):
  return (dt - datetime.datetime(1970, 1, 1)).total_seconds()


class ConnectionPanelEntry:
  """
  Common parent for connection panel entries. This consists of a list of lines
  in the panel listing. This caches results until the display indicates that
  they should be flushed.
  """

  def __init__(self):
    self.lines = []
    self.flush_cache = True

  @staticmethod
  def from_circuit(circ):
    import nyx.connections.circ_entry
    import nyx.connections.conn_entry
    import nyx.util.tracker

    # TODO: should be ConnectionPanelEntry rather than a ConnectionEntry, but
    # looks like that presently provides sorting

    entry = nyx.connections.conn_entry.ConnectionEntry(nyx.util.tracker.Connection(to_unix_time(circ.created), False, '127.0.0.1', 0, '127.0.0.1', 0, 'tcp'))
    entry.lines = [nyx.connections.circ_entry.CircHeaderLine(circ)]

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

      entry.lines.append(nyx.connections.circ_entry.CircLine(relay_ip, relay_port, relay_fingerprint, placement_label, to_unix_time(circ.created)))

    entry.lines[-1].is_last = True

    return entry

  def get_lines(self):
    """
    Provides the individual lines in the connection listing.
    """

    if self.flush_cache:
      self.lines = self._get_lines(self.lines)
      self.flush_cache = False

    return self.lines

  def _get_lines(self, old_results):
    # implementation of get_lines

    for line in old_results:
      line.reset_display()

    return old_results

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

    Arguments:
      attr        - list of SortAttr values for the field being sorted on
      listing_type - ListingType enumeration for the attribute we're listing
                    entries by
    """

    if attr == SortAttr.LISTING:
      if listing_type == ListingType.IP_ADDRESS:
        # uses the IP address as the primary value, and port as secondary
        sort_value = self.get_sort_value(SortAttr.IP_ADDRESS, listing_type) * PORT_COUNT
        sort_value += self.get_sort_value(SortAttr.PORT, listing_type)
        return sort_value
      elif listing_type == ListingType.FINGERPRINT:
        return self.get_sort_value(SortAttr.FINGERPRINT, listing_type)
      elif listing_type == ListingType.NICKNAME:
        return self.get_sort_value(SortAttr.NICKNAME, listing_type)

    return ''

  def reset_display(self):
    """
    Flushes cached display results.
    """

    self.flush_cache = True


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

  def reset_display(self):
    """
    Flushes cached display results.
    """

    self._listing_cache_args = (None, None)
    self._details_cache_args = None
