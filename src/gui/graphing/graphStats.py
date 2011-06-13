"""
Base class for implementing graphing functionality.
"""

import random
import sys

from collections import deque

import gobject
import gtk

from TorCtl import TorCtl
from util import torTools

GRAPH_INTERVAL = 30

class GraphStats(TorCtl.PostEventListener):
  def __init__(self, widgets):
    TorCtl.PostEventListener.__init__(self)

    self.widgets = widgets

    self.data = {
        'primary'   : deque([0] * GRAPH_INTERVAL),
        'secondary' : deque([0] * GRAPH_INTERVAL)}

  def get_graph_data(self, name):
    packed_data = []

    for (index, value) in enumerate(self.data[name]):
      packed_data.append((index, value))

    return packed_data

  def is_graph_data_zero(self, name):
    data = self.data[name]
    return len(data) == map(int, data).count(0)

  def draw_graph(self, name):
    graph = self.widgets['graph_%s' % name]
    data = self.get_graph_data(name)

    if self.is_graph_data_zero(name):
      graph.seriess[0].data = []
    else:
      graph.seriess[0].data = data

      for (index, axis) in enumerate(graph.axiss):
        if axis.type != 'xaxis':
          graph.auto_set_yrange(index)

    graph.queue_draw()
    return True

  def _processEvent(self, primary, secondary):
    self.data['primary'].rotate(1)
    self.data['primary'][0] = primary
    self.data['secondary'].rotate(1)
    self.data['secondary'][0] = secondary

