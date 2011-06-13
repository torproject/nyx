import gobject
import gtk

from util import torTools
from gui.graphing import graphStats, bandwidthStats

from cagraph.ca_graph import CaGraph
from cagraph.axis.xaxis import CaGraphXAxis
from cagraph.axis.yaxis import CaGraphYAxis
from cagraph.ca_graph_grid import CaGraphGrid
from cagraph.series.area import CaGraphSeriesArea

gobject.threads_init()

class GuiController:
  def __init__(self):
    filename = 'src/gui/arm.xml'

    self.builder = gtk.Builder()
    self.builder.add_from_file(filename)
    self.builder.connect_signals(self)

    # for custom widgets not present in builder xml
    self.widgets = {}

    self._pack_graph_widget('primary')
    self._pack_graph_widget('secondary')

    self.bwStats = bandwidthStats.BandwidthStats(self.widgets)

    gobject.timeout_add(1000, self.bwStats.draw_graph, 'primary')
    gobject.timeout_add(1000, self.bwStats.draw_graph, 'secondary')

  def run(self):
    window = self.builder.get_object('window_main')

    textbuffer = self.builder.get_object('textbuffer_log')
    conn = torTools.getConn()
    torPid = conn.getMyPid()
    textbuffer.set_text("Tor pid: %s" % torPid)

    window.show_all()
    gtk.main()

  def on_window_main_delete_event(self, widget, data=None):
    gtk.main_quit()

  def _pack_graph_widget(self, name):
    graph = CaGraph()
    placeholder = self.builder.get_object('placeholder_graph_%s' % name)
    placeholder.pack_start(graph)

    xaxis = CaGraphXAxis(graph)
    yaxis = CaGraphYAxis(graph)

    xaxis.min = 0
    xaxis.max = graphStats.GRAPH_INTERVAL - 1
    xaxis.axis_style.draw_labels = False

    graph.axiss.append(xaxis)
    graph.axiss.append(yaxis)

    series = CaGraphSeriesArea(graph, 0, 1)

    line_colors = {'primary' : (1.0, 0.0, 1.0, 1.0), 'secondary' : (0.0, 1.0, 0.0, 1.0)}
    fill_colors = {'primary' : (1.0, 0.0, 1.0, 0.3), 'secondary' : (0.0, 1.0, 0.0, 0.3)}
    series.style.line_color = line_colors[name]
    series.style.fill_color = fill_colors[name]

    graph.seriess.append(series)
    graph.grid = CaGraphGrid(graph, 0, 1)

    self.widgets['graph_%s' % name] = graph

def startGui():
  controller = GuiController()
  controller.run()

