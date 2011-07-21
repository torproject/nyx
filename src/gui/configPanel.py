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

class ConfContents(gtkTools.ListWrapper):
  def _create_row_from_entry(self, entry):
    option = entry.get(Field.OPTION)
    value = entry.get(Field.VALUE)
    summary = entry.get(Field.SUMMARY)
    desc = entry.get(Field.DESCRIPTION)
    row = (option, value, summary, '#368918', desc)

    return row

class ConfigPanel(CliConfigPanel):
  def __init__(self, builder):
    CliConfigPanel.__init__(self, None, State.TOR)

    self.builder = builder

    listStore = self.builder.get_object('liststore_config')
    self.confImportantContents = ConfContents(self.confImportantContents, listStore)

  def pack_widgets(self):
    treeView = self.builder.get_object('treeview_config')
    treeView.connect('cursor-changed', self.on_treeview_config_cursor_changed)

  def on_treeview_config_cursor_changed(self, widget, data=None):
    treeSelection = widget.get_selection()

    (model, iter) = treeSelection.get_selected()
    desc = model.get_value(iter, 4)

    textBuffer = self.builder.get_object('textbuffer_config_desc')
    textBuffer.set_text(desc)

