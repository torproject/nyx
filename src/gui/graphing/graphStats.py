"""
Base class for implementing graphing functionality.
"""

import random
import sys

from collections import deque

import gobject
import gtk

from TorCtl import TorCtl
from util import uiTools, torTools

GRAPH_INTERVAL = 30

class GraphStats(TorCtl.PostEventListener):
  def __init__(self, builder, widgets):
    TorCtl.PostEventListener.__init__(self)

    self.builder = builder
    self.widgets = widgets

    self.data = {
        'primary'   : deque([0.0] * GRAPH_INTERVAL),
        'secondary' : deque([0.0] * GRAPH_INTERVAL)}

    self.total = {'primary': 0.0,  'secondary' : 0.0}
    self.ticks = {'primary': 0,  'secondary' : 0}

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

  def update_labels(self, name):
    avg = 0

    try:
      avg = self.total[name] / float(self.ticks[name])
    except ZeroDivisionError:
      pass

    msg = "avg: %s/s, total: %s" % (uiTools.getSizeLabel(avg, 2, isBytes=False),
                                    uiTools.getSizeLabel(self.total[name], 2))
    label = self.builder.get_object('label_graph_%s_bottom' % name)
    label.set_text(msg)

    return True

  def update_header(self, name, msg):
    label = self.builder.get_object('label_graph_%s_top' % name)
    label.set_text(msg)

  def _processEvent(self, primary, secondary):
    values = {'primary' : primary, 'secondary' : secondary}

    for name in ('primary', 'secondary'):
      # right shift and store kbytes in left-most location
      self.data[name].rotate(1)
      self.data[name][0] = values[name] / 1024

      self.total[name] = self.total[name] + values[name]

      if values[name] > 0:
        self.ticks[name] = self.ticks[name] + 1

