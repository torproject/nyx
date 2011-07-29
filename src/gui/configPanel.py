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

def input_conf_value_size(option):
  prompt = "Enter value for %s" % option
  return gtkTools.input_size(prompt)

def input_conf_value_list(option):
  prompt = "Enter value for %s" % option
  return gtkTools.input_list(prompt)

def input_conf_value_text(option):
  prompt = "Enter value for %s" % option
  return gtkTools.input_text(prompt)

def input_conf_value_boolean(option):
  prompt = "Select value for %s" % option
  return "1" if gtkTools.input_boolean(prompt) else "0"

class ConfContents(gtkTools.ListWrapper):
  def _create_row_from_value(self, entry):
    option = entry.get(Field.OPTION)
    value = entry.get(Field.VALUE)
    summary = entry.get(Field.SUMMARY)
    desc = entry.get(Field.DESCRIPTION)
    row = (option, value, summary, '#368918', desc)

    return row

class ConfigPanel(object, CliConfigPanel):
  def __init__(self, builder):
    CliConfigPanel.__init__(self, None, State.TOR)

    self.builder = builder

    listStore = self.builder.get_object('liststore_config')
    self._wrappedConfImportantContents = ConfContents(self.confImportantContents, listStore)

  @property
  def confImportantContents(self):
    if hasattr(self, '_wrappedConfImportantContents'):
      return self._wrappedConfImportantContents.container
    else:
      return []

  @confImportantContents.setter
  def confImportantContents(self, value):
    if hasattr(self, '_wrappedConfImportantContents'):
      self._wrappedConfImportantContents.empty()
      for entry in value:
        self._wrappedConfImportantContents.append(entry)
    else:
      self._wrappedConfImportantContents = ConfContents(value)

  def pack_widgets(self):
    treeView = self.builder.get_object('treeview_config')

    treeView.connect('cursor-changed', self.on_treeview_config_cursor_changed)
    treeView.connect('row-activated', self.on_treeview_config_row_activated)

  def on_treeview_config_cursor_changed(self, treeView, data=None):
    treeSelection = treeView.get_selection()

    (model, iter) = treeSelection.get_selected()
    desc = model.get_value(iter, 4)

    textBuffer = self.builder.get_object('textbuffer_config_desc')
    textBuffer.set_text(desc)

  def on_treeview_config_row_activated(self, treeView, path, column):
    (index,) = path

    entry = self._wrappedConfImportantContents[index]
    configOption = entry.fields[Field.OPTION]
    configType = entry.fields[Field.TYPE]
    newValue = None

    if configType == 'DataSize':
      newValue = input_conf_value_size(configOption)
    elif configType == 'LineList':
      newValue = input_conf_value_list(configOption)
    elif configType == 'Boolean':
      newValue = input_conf_value_boolean(configOption)
    else:
      newValue = input_conf_value_text(configOption)

    if newValue:
      try:
        torTools.getConn().setOption(configOption, newValue)
      except TorCtl.ErrorReply, err:
        gtkTools.showError(str(err))

    self._wrappedConfImportantContents[index] = entry

