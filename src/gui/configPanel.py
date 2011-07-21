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

class ConfContents(object):
  def __init__(self, container, model):
    self.container = []
    self.model = model

    for entry in container:
      self.append(entry)

  def append(self, entry):
    self.container.append(entry)
    gobject.idle_add(self.__model_append, entry)

  def __str__(self):
    return str(self.container)

  def __repr__(self):
    return str(self.container)

  def __getitem__(self, key):
    return self.container[key]

  def __setitem__(self, key, entry):
    self.container[key] = entry

    gobject.idle_add(self.__model_set, key, entry)

  def __len__(self):
    return len(self.container)

  def __create_row_from_entry(self, entry):
    option = entry.get(Field.OPTION)
    value = entry.get(Field.VALUE)
    summary = entry.get(Field.SUMMARY)
    desc = entry.get(Field.DESCRIPTION)
    row = (option, value, summary, '#368918', desc)

    return row

  def __model_append(self, entry):
    row = self.__create_row_from_entry(entry)
    self.model.append(row)

  def __model_set(self, key, entry):
    row = self.__create_row_from_entry(entry)
    self.model[key] = row

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

