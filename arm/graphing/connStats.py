"""
Tracks stats concerning tor's current connections.
"""

import arm.util.tracker

from arm.graphing import graphPanel
from arm.util import torTools

from stem.control import State


class ConnStats(graphPanel.GraphStats):
  """
  Tracks number of connections, counting client and directory connections as
  outbound. Control connections are excluded from counts.
  """

  def __init__(self):
    graphPanel.GraphStats.__init__(self)

    # listens for tor reload (sighup) events which can reset the ports tor uses

    conn = torTools.get_conn()
    self.or_port, self.dir_port, self.control_port = "0", "0", "0"
    self.reset_listener(conn.get_controller(), State.INIT, None)  # initialize port values
    conn.add_status_listener(self.reset_listener)

  def clone(self, new_copy=None):
    if not new_copy:
      new_copy = ConnStats()

    return graphPanel.GraphStats.clone(self, new_copy)

  def reset_listener(self, controller, event_type, _):
    if event_type in (State.INIT, State.RESET):
      self.or_port = controller.get_conf("ORPort", "0")
      self.dir_port = controller.get_conf("DirPort", "0")
      self.control_port = controller.get_conf("ControlPort", "0")

  def event_tick(self):
    """
    Fetches connection stats from cached information.
    """

    inbound_count, outbound_count = 0, 0

    for entry in arm.util.tracker.get_connection_tracker().get_connections():
      local_port = entry.local_port

      if local_port in (self.or_port, self.dir_port):
        inbound_count += 1
      elif local_port == self.control_port:
        pass  # control connection
      else:
        outbound_count += 1

    self._process_event(inbound_count, outbound_count)

  def get_title(self, width):
    return "Connection Count:"

  def get_header_label(self, width, is_primary):
    avg = (self.primary_total if is_primary else self.secondary_total) / max(1, self.tick)

    if is_primary:
      return "Inbound (%s, avg: %s):" % (self.last_primary, avg)
    else:
      return "Outbound (%s, avg: %s):" % (self.last_secondary, avg)

  def get_refresh_rate(self):
    return 5
