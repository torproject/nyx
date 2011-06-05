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

    col = self.builder.get_object('treeview_sticky').get_column(0)
    cell = gtk.CellRendererText()
    col.pack_start(cell)
    col.add_attribute(cell, 'text', 0)

    col = self.builder.get_object('treeview_sticky').get_column(1)
    cell = gtk.CellRendererText()
    col.pack_start(cell)
    col.add_attribute(cell, 'markup', 1)
    col.add_attribute(cell, 'foreground', 2)

    col = self.builder.get_object('treeview_conn').get_column(0)
    cell = gtk.CellRendererText()
    col.pack_start(cell)
    col.add_attribute(cell, 'markup', 0)
    col.add_attribute(cell, 'foreground', 4)

    col = self.builder.get_object('treeview_conn').get_column(1)
    cell = gtk.CellRendererText()
    col.pack_start(cell)
    col.add_attribute(cell, 'markup', 1)
    col.add_attribute(cell, 'foreground', 4)

    col = self.builder.get_object('treeview_conn').get_column(2)
    cell = gtk.CellRendererText()
    col.pack_start(cell)
    col.add_attribute(cell, 'markup', 2)
    col.add_attribute(cell, 'foreground', 4)

    col = self.builder.get_object('treeview_conn').get_column(3)
    cell = gtk.CellRendererText()
    col.pack_start(cell)
    col.add_attribute(cell, 'markup', 3)
    col.add_attribute(cell, 'foreground', 4)

    col = self.builder.get_object('treeview_config').get_column(0)
    cell = gtk.CellRendererText()
    col.pack_start(cell)
    col.add_attribute(cell, 'markup', 0)
    col.add_attribute(cell, 'foreground', 3)

    col = self.builder.get_object('treeview_config').get_column(1)
    cell = gtk.CellRendererText()
    col.pack_start(cell)
    col.add_attribute(cell, 'markup', 1)
    col.add_attribute(cell, 'foreground', 3)

    col = self.builder.get_object('treeview_config').get_column(2)
    cell = gtk.CellRendererText()
    col.pack_start(cell)
    col.add_attribute(cell, 'markup', 2)
    col.add_attribute(cell, 'foreground', 3)

    textview = self.builder.get_object('textview_log')
    textbuffer = self.builder.get_object('textbuffer_log')
    textview.set_buffer(textbuffer)

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

