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

def input_conf_value_size(option, oldValue):
  prompt = "Enter value for %s" % option
  return gtkTools.input_size(prompt, oldValue)

def input_conf_value_int(option, oldValue):
  prompt = "Enter value for %s" % option
  return gtkTools.input_int(prompt, oldValue)

def input_conf_value_list(option, oldValue):
  prompt = "Enter value for %s" % option
  return gtkTools.input_list(prompt, oldValue)

def input_conf_value_string(option, oldValue):
  prompt = "Enter value for %s" % option
  return gtkTools.input_string(prompt, oldValue)

def input_conf_value_bool(option, oldValue):
  prompt = "Select value for %s" % option

  newValue = gtkTools.input_bool(prompt, oldValue)

  if newValue == None:
    return

  return "1" if newValue else "0"

def input_conf_value_dir(option, oldValue):
  prompt = "Select value for %s" % option
  return gtkTools.input_dir(prompt, oldValue)

def input_conf_value_filename(option, oldValue):
  prompt = "Select value for %s" % option
  return gtkTools.input_filename(prompt, oldValue)

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
    configOption = entry.get(Field.OPTION)
    configType = entry.get(Field.TYPE)
    oldValue = entry.get(Field.VALUE)
    newValue = None

    if configType == 'DataSize':
      newValue = input_conf_value_size(configOption, oldValue)
    elif configType == 'Integer':
      newValue = input_conf_value_int(configOption, oldValue)
    elif configType == 'String':
      newValue = input_conf_value_string(configOption, oldValue)
    elif configType == 'LineList':
      newValue = input_conf_value_list(configOption, oldValue)
    elif configType == 'Boolean':
      newValue = input_conf_value_bool(configOption, oldValue)
    elif configType == 'Filename':
      if 'Directory' in configOption:
        newValue = input_conf_value_dir(configOption, oldValue)
      else:
        newValue = input_conf_value_filename(configOption, oldValue)
    else:
      newValue = input_conf_value_string(configOption, oldValue)

    if newValue:
      try:
        torTools.getConn().setOption(configOption, newValue)
      except TorCtl.ErrorReply, err:
        gtkTools.showError(str(err))

    self._wrappedConfImportantContents[index] = entry

