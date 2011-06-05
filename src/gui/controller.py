from util import torTools

import gtk

class GuiController:
  def __init__(self):
    filename = 'src/gui/arm.xml'
    self.builder = gtk.Builder()
    self.builder.add_from_file(filename)
    self.builder.connect_signals(self)
    self._pack_formatted_cells_into_cols()

  def run(self):
    window = self.builder.get_object('window_main')

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

  def _pack_formatted_cells_into_cols(self):
    self._pack_formatted_cell_into_col(treeview='treeview_sticky', col=0, markup=0)
    self._pack_formatted_cell_into_col(treeview='treeview_sticky', col=1, markup=1, foreground=2)
    self._pack_formatted_cell_into_col(treeview='treeview_conn', col=0, markup=0, foreground=4)
    self._pack_formatted_cell_into_col(treeview='treeview_conn', col=1, markup=1, foreground=4)
    self._pack_formatted_cell_into_col(treeview='treeview_conn', col=2, markup=2, foreground=4)
    self._pack_formatted_cell_into_col(treeview='treeview_conn', col=3, markup=3, foreground=4)
    self._pack_formatted_cell_into_col(treeview='treeview_config', col=0, markup=0, foreground=3)
    self._pack_formatted_cell_into_col(treeview='treeview_config', col=1, markup=1, foreground=3)
    self._pack_formatted_cell_into_col(treeview='treeview_config', col=2, markup=2, foreground=3)

  def _pack_formatted_cell_into_col(self, treeview, col, markup, foreground=-1, background=-1):
    col = self.builder.get_object(treeview).get_column(col)
    cell = gtk.CellRendererText()
    col.pack_start(cell)
    col.add_attribute(cell, 'markup', markup)
    if foreground != -1:
      col.add_attribute(cell, 'foreground', foreground)
    if background != -1:
      col.add_attribute(cell, 'background', background)

def startGui():
  controller = GuiController()
  controller.run()

