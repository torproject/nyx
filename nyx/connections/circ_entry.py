"""
Connection panel entries for client circuits. This includes a header entry
followed by an entry for each hop in the circuit. For instance:

89.188.20.246:42667    -->  217.172.182.26 (de)       General / Built     8.6m (CIRCUIT)
|  85.8.28.4 (se)               98FBC3B2B93897A78CDD797EF549E6B62C9A8523    1 / Guard
|  91.121.204.76 (fr)           546387D93F8D40CFF8842BB9D3A8EC477CEDA984    2 / Middle
+- 217.172.182.26 (de)          5CFA9EA136C0EA0AC096E5CEA7EB674F1207CF86    3 / Exit
"""

import curses

from nyx.connections import entries, conn_entry
from nyx.util import tor_controller

from stem.util import str_tools

ADDRESS_LOOKUP_CACHE = {}


class CircEntry(conn_entry.ConnectionEntry):
  def __init__(self, circuit_id, status, purpose, path):
    conn_entry.ConnectionEntry.__init__(self, "127.0.0.1", "0", "127.0.0.1", "0")

    self.circuit_id = circuit_id
    self.status = status

    # drops to lowercase except the first letter

    if len(purpose) >= 2:
      purpose = purpose[0].upper() + purpose[1:].lower()

    self.lines = [CircHeaderLine(self.circuit_id, purpose)]

    # Overwrites attributes of the initial line to make it more fitting as the
    # header for our listing.

    self.lines[0].base_type = conn_entry.Category.CIRCUIT

    self.update(status, path)

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
    controller = tor_controller()

    if status == "BUILT" and not self.lines[0].is_built:
      exit_ip, exit_port = get_relay_address(controller, path[-1], ("192.168.0.1", "0"))
      self.lines[0].set_exit(exit_ip, exit_port, path[-1])

    for i in range(len(path)):
      relay_fingerprint = path[i]
      relay_ip, relay_port = get_relay_address(controller, relay_fingerprint, ("192.168.0.1", "0"))

      if i == len(path) - 1:
        if status == "BUILT":
          placement_type = "Exit"
        else:
          placement_type = "Extending"
      elif i == 0:
        placement_type = "Guard"
      else:
        placement_type = "Middle"

      placement_label = "%i / %s" % (i + 1, placement_type)

      self.lines.append(CircLine(relay_ip, relay_port, relay_fingerprint, placement_label))

    self.lines[-1].is_last = True


class CircHeaderLine(conn_entry.ConnectionLine):
  """
  Initial line of a client entry. This has the same basic format as connection
  lines except that its etc field has circuit attributes.
  """

  def __init__(self, circuit_id, purpose):
    conn_entry.ConnectionLine.__init__(self, "127.0.0.1", "0", "0.0.0.0", "0", False, False)
    self.circuit_id = circuit_id
    self.purpose = purpose
    self.is_built = False

  def set_exit(self, exit_address, exit_port, exit_fingerprint):
    conn_entry.ConnectionLine.__init__(self, "127.0.0.1", "0", exit_address, exit_port, False, False)
    self.is_built = True
    self.foreign.fingerprint_overwrite = exit_fingerprint

  def get_type(self):
    return conn_entry.Category.CIRCUIT

  def get_destination_label(self, max_length, include_locale=False, include_hostname=False):
    if not self.is_built:
      return "Building..."

    return conn_entry.ConnectionLine.get_destination_label(self, max_length, include_locale, include_hostname)

  def get_etc_content(self, width, listing_type):
    """
    Attempts to provide all circuit related stats. Anything that can't be
    shown completely (not enough room) is dropped.
    """

    etc_attr = ["Purpose: %s" % self.purpose, "Circuit ID: %s" % self.circuit_id]

    for i in range(len(etc_attr), -1, -1):
      etc_label = ", ".join(etc_attr[:i])

      if len(etc_label) <= width:
        return ("%%-%is" % width) % etc_label

    return ""

  def get_details(self, width):
    if not self.is_built:
      detail_format = (curses.A_BOLD, conn_entry.CATEGORY_COLOR[self.get_type()])
      return [("Building Circuit...", detail_format)]
    else:
      return conn_entry.ConnectionLine.get_details(self, width)


class CircLine(conn_entry.ConnectionLine):
  """
  An individual hop in a circuit. This overwrites the displayed listing, but
  otherwise makes use of the ConnectionLine attributes (for the detail display,
  caching, etc).
  """

  def __init__(self, remote_address, remote_port, remote_fingerprint, placement_label):
    conn_entry.ConnectionLine.__init__(self, "127.0.0.1", "0", remote_address, remote_port)
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
    line_format = conn_entry.CATEGORY_COLOR[self.get_type()]

    # The required widths are the sum of the following:
    # initial space (1 character)
    # bracketing (3 characters)
    # placement_label (14 characters)
    # gap between etc and placement label (5 characters)

    baseline_space = 14 + 5

    dst, etc = "", ""

    if listing_type == entries.ListingType.IP_ADDRESS:
      # TODO: include hostname when that's available
      # dst width is derived as:
      # src (21) + dst (26) + divider (7) + right gap (2) - bracket (3) = 53 char

      dst = "%-53s" % self.get_destination_label(53, include_locale = True)

      # fills the nickname into the empty space here

      dst = "%s%-25s   " % (dst[:25], str_tools.crop(self.foreign.get_nickname(), 25, 0))

      etc = self.get_etc_content(width - baseline_space - len(dst), listing_type)
    elif listing_type == entries.ListingType.HOSTNAME:
      # min space for the hostname is 40 characters

      etc = self.get_etc_content(width - baseline_space - 40, listing_type)
      dst_layout = "%%-%is" % (width - baseline_space - len(etc))
      dst = dst_layout % self.foreign.get_hostname(self.foreign.get_address())
    elif listing_type == entries.ListingType.FINGERPRINT:
      # dst width is derived as:
      # src (9) + dst (40) + divider (7) + right gap (2) - bracket (3) = 55 char

      dst = "%-55s" % self.foreign.get_fingerprint()
      etc = self.get_etc_content(width - baseline_space - len(dst), listing_type)
    else:
      # min space for the nickname is 56 characters

      etc = self.get_etc_content(width - baseline_space - 56, listing_type)
      dst_layout = "%%-%is" % (width - baseline_space - len(etc))
      dst = dst_layout % self.foreign.get_nickname()

    return ((dst + etc, line_format),
            (" " * (width - baseline_space - len(dst) - len(etc) + 5), line_format),
            ("%-14s" % self.placement_label, line_format))


def get_relay_address(controller, relay_fingerprint, default = None):
  """
  Provides the (IP Address, ORPort) tuple for a given relay. If the lookup
  fails then this returns the default.

  Arguments:
    relay_fingerprint - fingerprint of the relay
  """

  result = default

  if controller.is_alive():
    # query the address if it isn't yet cached
    if relay_fingerprint not in ADDRESS_LOOKUP_CACHE:
      if relay_fingerprint == controller.get_info("fingerprint", None):
        # this is us, simply check the config
        my_address = controller.get_info("address", None)
        my_or_port = controller.get_conf("ORPort", None)

        if my_address and my_or_port:
          ADDRESS_LOOKUP_CACHE[relay_fingerprint] = (my_address, my_or_port)
      else:
        # check the consensus for the relay
        relay = controller.get_network_status(relay_fingerprint, None)

        if relay:
          ADDRESS_LOOKUP_CACHE[relay_fingerprint] = (relay.address, relay.or_port)

    result = ADDRESS_LOOKUP_CACHE.get(relay_fingerprint, default)

  return result
