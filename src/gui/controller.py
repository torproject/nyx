from util import torTools

import gtk

class GuiController:
  def __init__(self):
    filename = 'src/gui/arm.xml'
    self.builder = gtk.Builder()
    self.builder.add_from_file(filename)
    self.builder.connect_signals(self)

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

def startGui():
  controller = GuiController()
  controller.run()

