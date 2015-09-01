"""
Connection panel entries for client circuits. This includes a header entry
followed by an entry for each hop in the circuit. For instance:

89.188.20.246:42667    -->  217.172.182.26 (de)       General / Built     8.6m (CIRCUIT)
|  85.8.28.4 (se)               98FBC3B2B93897A78CDD797EF549E6B62C9A8523    1 / Guard
|  91.121.204.76 (fr)           546387D93F8D40CFF8842BB9D3A8EC477CEDA984    2 / Middle
+- 217.172.182.26 (de)          5CFA9EA136C0EA0AC096E5CEA7EB674F1207CF86    3 / Exit
"""

import curses
import datetime

import nyx.util.tracker
import nyx.util.ui_tools
import nyx.connection_panel

from nyx.connections import conn_entry

from stem.util import str_tools

try:
  # added in python 3.2
  from functools import lru_cache
except ImportError:
  from stem.util.lru_cache import lru_cache


def to_unix_time(dt):
  return (dt - datetime.datetime(1970, 1, 1)).total_seconds()


class CircHeaderLine(conn_entry.ConnectionLine):
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

    conn_entry.ConnectionLine.__init__(self, entry, nyx.util.tracker.Connection(to_unix_time(circ.created), False, '127.0.0.1', 0, exit_address, exit_port, 'tcp'), False, False)
    self.circuit = circ

  def get_fingerprint(self, default = None):
    return self._remote_fingerprint if self._remote_fingerprint else conn_entry.ConnectionLine.get_fingerprint(self, default)

  def get_destination_label(self, max_length, include_locale = False):
    if not self.is_built:
      return 'Building...'

    return conn_entry.ConnectionLine.get_destination_label(self, max_length, include_locale)

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
      detail_format = (curses.A_BOLD, nyx.connection_panel.CATEGORY_COLOR[self._entry.get_type()])
      return [('Building Circuit...', detail_format)]
    else:
      return conn_entry.ConnectionLine.get_details(self, width)


class CircLine(conn_entry.ConnectionLine):
  """
  An individual hop in a circuit. This overwrites the displayed listing, but
  otherwise makes use of the ConnectionLine attributes (for the detail display,
  caching, etc).
  """

  def __init__(self, entry, circ, fingerprint):
    relay_ip, relay_port = nyx.util.tracker.get_consensus_tracker().get_relay_address(fingerprint, ('192.168.0.1', 0))
    conn_entry.ConnectionLine.__init__(self, entry, nyx.util.tracker.Connection(to_unix_time(circ.created), False, '127.0.0.1', 0, relay_ip, relay_port, 'tcp'), False)
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
    line_format = nyx.util.ui_tools.get_color(nyx.connection_panel.CATEGORY_COLOR[self._entry.get_type()])

    # The required widths are the sum of the following:
    # initial space (1 character)
    # bracketing (3 characters)
    # placement_label (14 characters)
    # gap between etc and placement label (5 characters)

    baseline_space = 14 + 5

    dst, etc = '', ''

    if listing_type == nyx.connection_panel.Listing.IP_ADDRESS:
      # dst width is derived as:
      # src (21) + dst (26) + divider (7) + right gap (2) - bracket (3) = 53 char

      dst = '%-53s' % self.get_destination_label(53, include_locale = True)

      # fills the nickname into the empty space here

      dst = '%s%-25s   ' % (dst[:25], str_tools.crop(self.get_nickname('UNKNOWN'), 25, 0))

      etc = self.get_etc_content(width - baseline_space - len(dst), listing_type)
    elif listing_type == nyx.connection_panel.Listing.FINGERPRINT:
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
