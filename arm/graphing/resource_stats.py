"""
Tracks the system resource usage (cpu and memory) of the tor process.
"""

import arm.util.tracker

from arm.graphing import graph_panel

from stem.util import str_tools


class ResourceStats(graph_panel.GraphStats):
  """
  System resource usage tracker.
  """

  def __init__(self):
    graph_panel.GraphStats.__init__(self)
    self._last_counter = None

  def clone(self, new_copy=None):
    if not new_copy:
      new_copy = ResourceStats()

    return graph_panel.GraphStats.clone(self, new_copy)

  def get_title(self, width):
    return 'System Resources:'

  def primary_header(self, width):
    avg = self.primary_total / max(1, self.tick)
    return 'CPU (%0.1f%%, avg: %0.1f%%):' % (self.last_primary, avg)

  def secondary_header(self, width):
    # memory sizes are converted from MB to B before generating labels

    usage_label = str_tools.size_label(self.last_secondary * 1048576, 1)

    avg = self.secondary_total / max(1, self.tick)
    avg_label = str_tools.size_label(avg * 1048576, 1)

    return 'Memory (%s, avg: %s):' % (usage_label, avg_label)

  def event_tick(self):
    """
    Fetch the cached measurement of resource usage from the ResourceTracker.
    """

    resource_tracker = arm.util.tracker.get_resource_tracker()

    if resource_tracker and resource_tracker.run_counter() != self._last_counter:
      resources = resource_tracker.get_value()
      primary = resources.cpu_sample * 100  # decimal percentage to whole numbers
      secondary = resources.memory_bytes / 1048576  # translate size to MB so axis labels are short

      self._last_counter = resource_tracker.run_counter()
      self._process_event(primary, secondary)
