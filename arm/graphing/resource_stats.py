"""
Tracks the system resource usage (cpu and memory) of the tor process.
"""

import arm.util.tracker

from arm.graphing import graph_panel
from arm.util import tor_controller

from stem.util import str_tools


class ResourceStats(graph_panel.GraphStats):
  """
  System resource usage tracker.
  """

  def __init__(self):
    graph_panel.GraphStats.__init__(self)
    self.query_pid = tor_controller().get_pid(None)
    self.last_counter = None

  def clone(self, new_copy=None):
    if not new_copy:
      new_copy = ResourceStats()

    return graph_panel.GraphStats.clone(self, new_copy)

  def get_title(self, width):
    return "System Resources:"

  def get_header_label(self, width, is_primary):
    avg = (self.primary_total if is_primary else self.secondary_total) / max(1, self.tick)
    last_amount = self.last_primary if is_primary else self.last_secondary

    if is_primary:
      return "CPU (%0.1f%%, avg: %0.1f%%):" % (last_amount, avg)
    else:
      # memory sizes are converted from MB to B before generating labels

      usage_label = str_tools.get_size_label(last_amount * 1048576, 1)
      avg_label = str_tools.get_size_label(avg * 1048576, 1)

      return "Memory (%s, avg: %s):" % (usage_label, avg_label)

  def event_tick(self):
    """
    Fetch the cached measurement of resource usage from the ResourceTracker.
    """

    primary, secondary = 0, 0

    if self.query_pid:
      resource_tracker = arm.util.tracker.get_resource_tracker()

      if resource_tracker and resource_tracker.run_counter() != self.last_counter:
        resources = resource_tracker.get_resource_usage()
        self.last_counter = resource_tracker.run_counter()
        primary = resources.cpu_sample * 100  # decimal percentage to whole numbers
        secondary = resources.memory_bytes / 1048576  # translate size to MB so axis labels are short
        self.run_count = resource_tracker.run_counter()

    self._process_event(primary, secondary)
