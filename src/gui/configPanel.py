"""
Configuration panel.
"""

import random
import sys
import time

import gobject
import gtk

from cli.configPanel import (ConfigPanel as CliConfigPanel, Field, State)
from util import connections, gtkTools, sysTools, torConfig, torTools, uiTools
from TorCtl import TorCtl

CATEGORY_COLOR = {torConfig.Category.GENERAL: "#307809",
                  torConfig.Category.CLIENT: "#2F305C",
                  torConfig.Category.RELAY: "#848144",
                  torConfig.Category.DIRECTORY: "#9F2254",
                  torConfig.Category.AUTHORITY: "#B3141B",
                  torConfig.Category.HIDDEN_SERVICE: "#3A8427",
                  torConfig.Category.TESTING: "#222222",
                  torConfig.Category.UNKNOWN: "#111111"}

def input_conf_value_size(option, oldValue):
  prompt = "Enter value for %s" % option
  return gtkTools.input_size(prompt, oldValue)

def input_conf_value_time(option, oldValue):
  prompt = "Enter value for %s" % option
  return gtkTools.input_time(prompt, oldValue)

def input_conf_value_int(option, oldValue):
  prompt = "Enter value for %s" % option
  return gtkTools.input_int(prompt, oldValue)

def input_conf_value_list(option, oldValue, csv=False):
  prompt = "Enter value for %s" % option
  return gtkTools.input_list(prompt, oldValue, csv)

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
    isDefault = entry.get(Field.IS_DEFAULT)
    value = entry.get(Field.VALUE)
    configType = entry.get(Field.TYPE)
    summary = entry.get(Field.SUMMARY)
    desc = " ".join(entry.get(Field.DESCRIPTION).split())
    argUsage = entry.get(Field.ARG_USAGE)
    category = entry.get(Field.CATEGORY)

    descText = "%s (%s Option)" % (option, category)
    descText += "\nValue: %s (%s%s, usage: %s)" % (value, "default, " if isDefault else "", configType, argUsage)
    descText += "\nDescription: %s" % (desc)

    row = (option, value, summary, CATEGORY_COLOR[category], descText)

    return row

class ConfigPanel(object, CliConfigPanel):
  def __init__(self, builder):
    CliConfigPanel.__init__(self, None, State.TOR)

    self.builder = builder

    listStore = self.builder.get_object('liststore_config')
    self._wrappedConfContents = ConfContents(self.confContents, listStore)

  @property
  def confContents(self):
    if hasattr(self, '_wrappedConfContents'):
      return self._wrappedConfContents.container
    else:
      return []

  @confContents.setter
  def confContents(self, value):
    if hasattr(self, '_wrappedConfContents'):
      self._wrappedConfContents.empty()
      for entry in value:
        self._wrappedConfContents.append(entry)
    else:
      self._wrappedConfContents = ConfContents(value)

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

    entry = self._wrappedConfContents[index]
    configOption = entry.get(Field.OPTION)
    configType = entry.get(Field.TYPE)
    argUsage = entry.get(Field.ARG_USAGE)
    oldValue = entry.get(Field.VALUE) if entry.get(Field.VALUE) != '<none>' else None
    newValue = None

    if configType == 'DataSize':
      newValue = input_conf_value_size(configOption, oldValue)
    elif configType == 'TimeInterval':
      newValue = input_conf_value_time(configOption, oldValue)
    elif configType == 'Integer':
      newValue = input_conf_value_int(configOption, oldValue)
    elif configType == 'String':
      newValue = input_conf_value_string(configOption, oldValue)
    elif configType == 'LineList':
      newValue = input_conf_value_list(configOption, oldValue, csv=False)
    elif configType == 'RouterList' or configType == 'CommaList':
      newValue = input_conf_value_list(configOption, oldValue, csv=True)
    elif configType == 'Boolean':
      newValue = input_conf_value_bool(configOption, oldValue)
    elif configType == 'Filename':
      if 'DIR' in argUsage:
        newValue = input_conf_value_dir(configOption, oldValue)
      else:
        newValue = input_conf_value_filename(configOption, oldValue)
    else:
      newValue = input_conf_value_string(configOption, oldValue)

    if newValue and newValue != oldValue:
      try:
        torTools.getConn().setOption(configOption, newValue)
      except TorCtl.ErrorReply, err:
        gtkTools.showError(str(err))

    self._wrappedConfContents[index] = entry

