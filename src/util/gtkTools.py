"""
Helper module for getting Gtk+ theme colors.
"""

import gobject
import gtk

COLOR_MAP = {
  'normal' : ('fg', gtk.STATE_NORMAL),
  'active' : ('fg', gtk.STATE_ACTIVE),
  'insensitive' : ('fg', gtk.STATE_INSENSITIVE),
}

class Theme:
  def __init__(self):
    self.colors = {}

    widget = gtk.Button()

    for (key, (prop, state)) in COLOR_MAP.items():
      self.colors[key] = getattr(widget.style, prop)[state]

class ListWrapper(object):
  def __init__(self, container, model=None):
    self.container = []
    self.model = model

    for value in container:
      self.append(value)

  def append(self, value):
    self.container.append(value)
    gobject.idle_add(self._model_append, value)

  def empty(self):
    self.container = []
    gobject.idle_add(self._model_clear)

  def __str__(self):
    return str(self.container)

  def __repr__(self):
    return str(self.container)

  def __len__(self):
    return len(self.container)

  def __iadd__(self, other):
    for value in other:
      self.append(value)

  def __delitem__(self, key):
    del self.container[key]

    gobject.idle_add(self._model_del, key)

  def __getitem__(self, key):
    return self.container[key]

  def __setitem__(self, key, value):
    self.container[key] = value

    gobject.idle_add(self._model_set, key, value)

  def _model_append(self, value):
    if not self.model:
      return

    row = self._create_row_from_value(value)
    self.model.append(row)

  def _model_clear(self):
    if not self.model:
      return

    self.model.clear()

  def _model_del(self, key):
    if not self.model:
      return

    treeIter = self.model.get_iter(key)
    self.model.remove(treeIter)

  def _model_set(self, key, value):
    if not self.model:
      return

    row = self._create_row_from_value(value)
    self.model[key] = row

  def _create_row_from_value(self, value):
    raise NotImplementedError("Subclass must implement abstract method")

class TreeWrapper(ListWrapper):
  def _model_append(self, value):
    if not self.model:
      return

    row = self._create_row_from_value(value)
    self.model.append(None, row)

def response_to_dialog(entry, dialog, response):
  dialog.response(response)

def input_size(prompt, default=None):
  dialog = gtk.MessageDialog(None,
      gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT,
      gtk.MESSAGE_QUESTION,
      gtk.BUTTONS_OK_CANCEL,
      None)

  dialog.set_markup(prompt)

  hBox = gtk.HBox()

  dialog.vbox.pack_end(hBox, True, True, 0)

  spinButton = gtk.SpinButton(None)
  spinButton.connect('activate', response_to_dialog, dialog, gtk.RESPONSE_OK)

  spinButton.set_increments(1, 10)
  spinButton.set_range(0, 1024)

  hBox.pack_start(spinButton, True, True, 0)

  comboBox = gtk.combo_box_new_text()

  comboBox.append_text("B")
  comboBox.append_text("KB")
  comboBox.append_text("MB")
  comboBox.append_text("GB")
  comboBox.append_text("TB")
  comboBox.append_text("PB")

  hBox.pack_end(comboBox, False, False, 0)

  if default:
    value, units = default.split()

    spinButton.set_value(float(value))

    model = comboBox.get_model()
    modelUnits = [row[0] for row in model]
    index = modelUnits.index(units)
    comboBox.set_active(index)

  dialog.show_all()
  response = dialog.run()

  value = spinButton.get_value_as_int()

  model = comboBox.get_model()
  active = comboBox.get_active()
  (units,) = model[active]

  dialog.destroy()

  return "%d %s" % (value, units) if response == gtk.RESPONSE_OK else None

def input_time(prompt, default=None):
  dialog = gtk.MessageDialog(None,
      gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT,
      gtk.MESSAGE_QUESTION,
      gtk.BUTTONS_OK_CANCEL,
      None)

  dialog.set_markup(prompt)

  hBox = gtk.HBox()

  dialog.vbox.pack_end(hBox, True, True, 0)

  spinButton = gtk.SpinButton(None)
  spinButton.connect('activate', response_to_dialog, dialog, gtk.RESPONSE_OK)

  spinButton.set_increments(1, 10)
  spinButton.set_range(0, 1024)

  hBox.pack_start(spinButton, True, True, 0)

  comboBox = gtk.combo_box_new_text()

  comboBox.append_text("seconds")
  comboBox.append_text("minutes")
  comboBox.append_text("hours")
  comboBox.append_text("days")

  hBox.pack_end(comboBox, False, False, 0)

  if default:
    if default[-1:] != 's':
      default = default + 's'

    value, units = default.split()

    spinButton.set_value(float(value))

    model = comboBox.get_model()
    modelUnits = [row[0] for row in model]
    index = modelUnits.index(units)
    comboBox.set_active(index)

  dialog.show_all()
  response = dialog.run()

  value = spinButton.get_value_as_int()

  model = comboBox.get_model()
  active = comboBox.get_active()
  (units,) = model[active]

  dialog.destroy()

  return "%d %s" % (value, units) if response == gtk.RESPONSE_OK else None

def input_int(prompt, default=None, csvResponse=False):
  dialog = gtk.MessageDialog(None,
      gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT,
      gtk.MESSAGE_QUESTION,
      gtk.BUTTONS_OK_CANCEL,
      None)

  dialog.set_markup(prompt)

  spinButton = gtk.SpinButton(None)
  spinButton.connect('activate', response_to_dialog, dialog, gtk.RESPONSE_OK)

  spinButton.set_increments(1, 10)
  spinButton.set_range(0, 65535)

  dialog.vbox.pack_end(spinButton, True, True, 0)

  if default:
    spinButton.set_value(float(default))

  dialog.show_all()
  response = dialog.run()

  value = spinButton.get_value_as_int()

  dialog.destroy()

  return "%d" % (value) if response == gtk.RESPONSE_OK else None

def input_string(prompt, default=None):
  dialog = gtk.MessageDialog(None,
      gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT,
      gtk.MESSAGE_QUESTION,
      gtk.BUTTONS_OK_CANCEL,
      None)

  dialog.set_markup(prompt)

  entry = gtk.Entry()
  entry.connect('activate', response_to_dialog, dialog, gtk.RESPONSE_OK)

  dialog.vbox.pack_end(entry, True, True, 0)

  if default:
    entry.set_text(default)

  dialog.show_all()
  response = dialog.run()

  text = entry.get_text()
  dialog.destroy()

  return text if response == gtk.RESPONSE_OK else None

def input_list(prompt, default, csv=False):
  def on_add_button_clicked(widget, listStore):
    newValue = input_string("Enter new value:")

    if newValue:
      row = (newValue,)
      listStore.append(row)

  def on_delete_button_clicked(widget, treeView):
    selection = treeView.get_selection()
    model, selectionIter = selection.get_selected()

    if (selectionIter):
      model.remove(selectionIter)

  dialog = gtk.MessageDialog(None,
      gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT,
      gtk.MESSAGE_QUESTION,
      gtk.BUTTONS_OK_CANCEL,
      None)

  dialog.set_markup(prompt)

  hBox = gtk.HBox()

  dialog.vbox.pack_start(hBox, False, False, 0)

  addButton = gtk.Button(stock=gtk.STOCK_ADD)

  hBox.pack_start(addButton, False, False, 0)

  deleteButton = gtk.Button(stock=gtk.STOCK_DELETE)

  hBox.pack_start(deleteButton, False, False, 0)

  scrolledWindow = gtk.ScrolledWindow()

  dialog.vbox.pack_end(scrolledWindow, True, True, 0)

  listStore = gtk.ListStore(str)
  treeView = gtk.TreeView(listStore)
  treeViewColumn = gtk.TreeViewColumn("Value")
  cellRenderer = gtk.CellRendererText()

  treeViewColumn.pack_start(cellRenderer, True)
  treeViewColumn.add_attribute(cellRenderer, 'text', 0)
  treeView.append_column(treeViewColumn)

  scrolledWindow.add(treeView)

  addButton.connect('clicked', on_add_button_clicked, listStore)
  deleteButton.connect('clicked', on_delete_button_clicked, treeView)

  separator = "," if csv else " "
  if default:
    for value in default.split(separator):
      row = (value,)
      listStore.append(row)

  dialog.show_all()
  response = dialog.run()

  dialog.destroy()

  if not response == gtk.RESPONSE_OK:
    return

  return None if len(listStore) == 0 else separator.join([row[0] for row in listStore])

def input_bool(prompt, default=None):
  dialog = gtk.MessageDialog(None,
      gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT,
      gtk.MESSAGE_QUESTION,
      gtk.BUTTONS_OK_CANCEL,
      None)

  dialog.set_markup(prompt)

  hbox = gtk.HBox()
  buttonTrue = gtk.RadioButton(None, "True")
  buttonFalse = gtk.RadioButton(buttonTrue, "False")
  hbox.pack_start(buttonTrue, True, True, 0)
  hbox.pack_start(buttonFalse, True, True, 0)

  dialog.vbox.pack_end(hbox, True, True, 0)

  if not default == None:
    if default == 'True':
      buttonTrue.set_active(True)
    elif default == 'False':
      buttonFalse.set_active(True)

  dialog.show_all()
  response = dialog.run()

  choice = None

  if buttonTrue.get_active():
    choice = True
  elif buttonFalse.get_active():
    choice = False

  dialog.destroy()

  return choice if response == gtk.RESPONSE_OK else None

def input_dir(prompt, default):
  dialog = gtk.FileChooserDialog(prompt,
                               None,
                               gtk.FILE_CHOOSER_ACTION_SELECT_FOLDER,
                               (gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL,
                                gtk.STOCK_OPEN, gtk.RESPONSE_OK))
  dialog.set_default_response(gtk.RESPONSE_OK)

  if default:
    dialog.set_filename(default)

  dialog.show_all()
  response = dialog.run()

  filename = dialog.get_filename()

  dialog.destroy()

  return filename if response == gtk.RESPONSE_OK else None

def input_filename(prompt, default):
  dialog = gtk.FileChooserDialog(prompt,
                               None,
                               gtk.FILE_CHOOSER_ACTION_SAVE,
                               (gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL,
                                gtk.STOCK_OPEN, gtk.RESPONSE_OK))
  dialog.set_default_response(gtk.RESPONSE_OK)

  if default:
    dialog.set_filename(default)

  dialog.show_all()
  response = dialog.run()

  filename = dialog.get_filename()

  dialog.destroy()

  return filename if response == gtk.RESPONSE_OK else None

def showError(msg):
  dialog = gtk.MessageDialog(None,
      gtk.DIALOG_MODAL | gtk.DIALOG_DESTROY_WITH_PARENT,
      gtk.MESSAGE_ERROR,
      gtk.BUTTONS_OK,
      msg)

  dialog.run()
  dialog.destroy()

