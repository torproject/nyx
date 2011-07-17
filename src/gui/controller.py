import thread
import time

import gobject
import gtk

from connections import connPanel
from gui.graphing import bandwidthStats
from gui import generalPanel, logPanel
from util import log, torTools

gobject.threads_init()

class GuiController:
  def __init__(self):
    filename = 'src/gui/arm.xml'

    self.builder = gtk.Builder()
    self.builder.add_from_file(filename)
    self.builder.connect_signals(self)

    panelClasses = (logPanel.LogPanel,
              bandwidthStats.BandwidthStats,
              connPanel.ConnectionPanel,
              generalPanel.GeneralPanel)
    self.panels = {}

    for panelClass in panelClasses:
      self.panels[panelClass] = panelClass(self.builder)
      self.panels[panelClass].pack_widgets()

  def run(self):
    window = self.builder.get_object('window_main')

    window.show_all()
    gtk.main()

  def on_action_about_activate(self, widget, data=None):
    dialog = self.builder.get_object('aboutdialog')
    dialog.run()

  def on_aboutdialog_response(self, widget, responseid, data=None):
    dialog = self.builder.get_object('aboutdialog')
    dialog.hide()

  def on_action_quit_activate(self, widget, data=None):
    gtk.main_quit()

  def on_window_main_delete_event(self, widget, data=None):
    gtk.main_quit()

def start_gui():
  controller = GuiController()
  controller.run()

