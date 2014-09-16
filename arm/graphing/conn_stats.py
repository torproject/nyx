"""
Tracks stats concerning tor's current connections.
"""

import arm.util.tracker

from arm.graphing import graph_panel
from arm.util import tor_controller

from stem.control import Listener


class ConnStats(graph_panel.GraphStats):
  """
  Tracks number of connections, counting client and directory connections as
  outbound. Control connections are excluded from counts.
  """

  def __init__(self):
    graph_panel.GraphStats.__init__(self)

  def clone(self, new_copy=None):
    if not new_copy:
      new_copy = ConnStats()

    return graph_panel.GraphStats.clone(self, new_copy)

  def event_tick(self):
    """
    Fetches connection stats from cached information.
    """

    inbound_count, outbound_count = 0, 0

    controller = tor_controller()

    or_ports = controller.get_ports(Listener.OR)
    dir_ports = controller.get_ports(Listener.DIR)
    control_ports = controller.get_ports(Listener.CONTROL)

    for entry in arm.util.tracker.get_connection_tracker().get_value():
      local_port = entry.local_port

      if local_port in or_ports or local_port in dir_ports:
        inbound_count += 1
      elif local_port in control_ports:
        pass  # control connection
      else:
        outbound_count += 1

    self._process_event(inbound_count, outbound_count)

  def get_title(self, width):
    return 'Connection Count:'

  def get_header_label(self, width, is_primary):
    avg = (self.primary_total if is_primary else self.secondary_total) / max(1, self.tick)

    if is_primary:
      return 'Inbound (%s, avg: %s):' % (self.last_primary, avg)
    else:
      return 'Outbound (%s, avg: %s):' % (self.last_secondary, avg)
