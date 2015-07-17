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

from nyx.connections import entries, conn_entry

from stem.util import str_tools


def to_unix_time(dt):
  return (dt - datetime.datetime(1970, 1, 1)).total_seconds()


class CircEntry(conn_entry.ConnectionEntry):
  def __init__(self, circ):
    conn_entry.ConnectionEntry.__init__(self, nyx.util.tracker.Connection(to_unix_time(circ.created), False, '127.0.0.1', 0, '127.0.0.1', 0, 'tcp'))

    self._circuit = circ
    self.circuit_id = circ.id
    self.status = circ.status

    # drops to lowercase except the first letter

    purpose = circ.purpose

    if len(purpose) >= 2:
      purpose = purpose[0].upper() + purpose[1:].lower()

    self.lines = [CircHeaderLine(circ)]

    # Overwrites attributes of the initial line to make it more fitting as the
    # header for our listing.

    self.lines[0].base_type = conn_entry.Category.CIRCUIT

    self.update(circ.status, [entry[0] for entry in circ.path])

  def update(self, status, path):
    """
    Our status and path can change over time if the circuit is still in the
    process of being built. Updates these attributes of our relay.

    Arguments:
      status - new status of the circuit
      path   - list of fingerprints for the series of relays involved in the
               circuit
    """

    self.status = status
    self.lines = [self.lines[0]]

    if status == 'BUILT' and not self.lines[0].is_built:
      exit_ip, exit_port = nyx.util.tracker.get_consensus_tracker().get_relay_address(path[-1], ('192.168.0.1', 0))
      self.lines[0].set_exit(exit_ip, exit_port, path[-1])

    for i in range(len(path)):
      relay_fingerprint = path[i]
      relay_ip, relay_port = nyx.util.tracker.get_consensus_tracker().get_relay_address(relay_fingerprint, ('192.168.0.1', 0))

      if i == len(path) - 1:
        if status == 'BUILT':
          placement_type = 'Exit'
        else:
          placement_type = 'Extending'
      elif i == 0:
        placement_type = 'Guard'
      else:
        placement_type = 'Middle'

      placement_label = '%i / %s' % (i + 1, placement_type)

      self.lines.append(CircLine(relay_ip, relay_port, relay_fingerprint, placement_label, to_unix_time(self._circuit.created)))

    self.lines[-1].is_last = True


class CircHeaderLine(conn_entry.ConnectionLine):
  """
  Initial line of a client entry. This has the same basic format as connection
  lines except that its etc field has circuit attributes.
  """

  def __init__(self, circ):
    conn_entry.ConnectionLine.__init__(self, nyx.util.tracker.Connection(to_unix_time(circ.created), False, '127.0.0.1', 0, '0.0.0.0', 0, 'tcp'), False, False)
    self.circuit_id = circ.id
    self.purpose = circ.purpose
    self.is_built = False
    self._timestamp = to_unix_time(circ.created)

  def set_exit(self, exit_address, exit_port, exit_fingerprint):
    conn_entry.ConnectionLine.__init__(self, nyx.util.tracker.Connection(self._timestamp, False, '127.0.0.1', 0, exit_address, exit_port, 'tcp'), False, False)
    self.is_built = True
    self.foreign.fingerprint_overwrite = exit_fingerprint

  def get_type(self):
    return conn_entry.Category.CIRCUIT

  def get_destination_label(self, max_length, include_locale = False):
    if not self.is_built:
      return 'Building...'

    return conn_entry.ConnectionLine.get_destination_label(self, max_length, include_locale)

  def get_etc_content(self, width, listing_type):
    """
    Attempts to provide all circuit related stats. Anything that can't be
    shown completely (not enough room) is dropped.
    """

    etc_attr = ['Purpose: %s' % self.purpose, 'Circuit ID: %s' % self.circuit_id]

    for i in range(len(etc_attr), -1, -1):
      etc_label = ', '.join(etc_attr[:i])

      if len(etc_label) <= width:
        return ('%%-%is' % width) % etc_label

    return ''

  def get_details(self, width):
    if not self.is_built:
      detail_format = (curses.A_BOLD, conn_entry.CATEGORY_COLOR[self.get_type()])
      return [('Building Circuit...', detail_format)]
    else:
      return conn_entry.ConnectionLine.get_details(self, width)


class CircLine(conn_entry.ConnectionLine):
  """
  An individual hop in a circuit. This overwrites the displayed listing, but
  otherwise makes use of the ConnectionLine attributes (for the detail display,
  caching, etc).
  """

  def __init__(self, remote_address, remote_port, remote_fingerprint, placement_label, timestamp):
    conn_entry.ConnectionLine.__init__(self, nyx.util.tracker.Connection(timestamp, False, '127.0.0.1', 0, remote_address, remote_port, 'tcp'))
    self.foreign.fingerprint_overwrite = remote_fingerprint
    self.placement_label = placement_label
    self.include_port = False

    # determines the sort of left hand bracketing we use

    self.is_last = False

  def get_type(self):
    return conn_entry.Category.CIRCUIT

  def get_listing_prefix(self):
    if self.is_last:
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

    return entries.ConnectionPanelLine.get_listing_entry(self, width, current_time, listing_type)

  def _get_listing_entry(self, width, current_time, listing_type):
    line_format = nyx.util.ui_tools.get_color(conn_entry.CATEGORY_COLOR[self.get_type()])

    # The required widths are the sum of the following:
    # initial space (1 character)
    # bracketing (3 characters)
    # placement_label (14 characters)
    # gap between etc and placement label (5 characters)

    baseline_space = 14 + 5

    dst, etc = '', ''

    if listing_type == entries.ListingType.IP_ADDRESS:
      # dst width is derived as:
      # src (21) + dst (26) + divider (7) + right gap (2) - bracket (3) = 53 char

      dst = '%-53s' % self.get_destination_label(53, include_locale = True)

      # fills the nickname into the empty space here

      dst = '%s%-25s   ' % (dst[:25], str_tools.crop(self.foreign.get_nickname('UNKNOWN'), 25, 0))

      etc = self.get_etc_content(width - baseline_space - len(dst), listing_type)
    elif listing_type == entries.ListingType.FINGERPRINT:
      # dst width is derived as:
      # src (9) + dst (40) + divider (7) + right gap (2) - bracket (3) = 55 char

      dst = '%-55s' % self.foreign.get_fingerprint('UNKNOWN')
      etc = self.get_etc_content(width - baseline_space - len(dst), listing_type)
    else:
      # min space for the nickname is 56 characters

      etc = self.get_etc_content(width - baseline_space - 56, listing_type)
      dst_layout = '%%-%is' % (width - baseline_space - len(etc))
      dst = dst_layout % self.foreign.get_nickname('UNKNOWN')

    return ((dst + etc, line_format),
            (' ' * (width - baseline_space - len(dst) - len(etc) + 5), line_format),
            ('%-14s' % self.placement_label, line_format))
