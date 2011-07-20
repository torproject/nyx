"""
Configuration panel.
"""

import random
import sys
import time

import gobject
import gtk

from cli.configPanel import (ConfigPanel as CliConfigPanel, Field, State)
from util import connections, gtkTools, sysTools, torTools, uiTools
from TorCtl import TorCtl

class ConfigPanel(CliConfigPanel):
  def __init__(self, builder):
    CliConfigPanel.__init__(self, None, State.TOR)

    self.builder = builder

    gobject.idle_add(self._fill_entries)
    gobject.timeout_add(3000, self._timeout_fill_entries)

  def pack_widgets(self):
    treeView = self.builder.get_object('treeview_config')
    treeView.connect('cursor-changed', self.on_treeview_config_cursor_changed)

  def _timeout_fill_entries(self):
    self._fill_entries()

    return True

  def _fill_entries(self):
    self.valsLock.acquire()

    listStore = self.builder.get_object('liststore_config')
    listStore.clear()

    for entry in self._getConfigOptions():
      option = entry.get(Field.OPTION)
      value = entry.get(Field.VALUE)
      summary = entry.get(Field.SUMMARY)
      desc = entry.get(Field.DESCRIPTION)
      row = (option, value, summary, '#368918', desc)
      listStore.append(row)

    self.valsLock.release()

  def on_treeview_config_cursor_changed(self, widget, data=None):
    treeSelection = widget.get_selection()

    (model, iter) = treeSelection.get_selected()
    desc = model.get_value(iter, 4)

    textBuffer = self.builder.get_object('textbuffer_config_desc')
    textBuffer.set_text(desc)
    print desc

