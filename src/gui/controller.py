import gobject
import gtk

import thread
import time

from util import log, torTools
from gui import logPanel
from gui.graphing import bandwidthStats

gobject.threads_init()

class GuiController:
  def __init__(self):
    filename = 'src/gui/arm.xml'

    self.builder = gtk.Builder()
    self.builder.add_from_file(filename)
    self.builder.connect_signals(self)

    self.bwStats = bandwidthStats.BandwidthStats(self.builder)
    self.bwStats.pack_widgets()

    self.logPanel = logPanel.LogPanel(self.builder)
    self.logPanel.pack_widgets()

    log.log(log.DEBUG, "Hello world!")

  def run(self):
    window = self.builder.get_object('window_main')

    window.show_all()
    gtk.main()

  def on_window_main_delete_event(self, widget, data=None):
    gtk.main_quit()

def startGui():
  controller = GuiController()
  controller.run()

