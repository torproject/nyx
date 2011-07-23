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
  def __init__(self, container, model):
    self.container = []
    self.model = model

    for entry in container:
      self.append(entry)

  def append(self, entry):
    self.container.append(entry)
    gobject.idle_add(self.__model_append, entry)

  def empty(self):
    self.container = []
    gobject.idle_add(self.__model_clear)

  def __str__(self):
    return str(self.container)

  def __repr__(self):
    return str(self.container)

  def __len__(self):
    return len(self.container)

  def __iadd__(self, other):
    for entry in other:
      self.append(entry)

  def __delitem__(self, key):
    del self.container[key]

    gobject.idle_add(self.__model_del, key)

  def __getitem__(self, key):
    return self.container[key]

  def __setitem__(self, key, entry):
    self.container[key] = entry

    gobject.idle_add(self.__model_set, key, entry)

  def __model_append(self, entry):
    row = self._create_row_from_entry(entry)
    self.model.append(row)

  def __model_clear(self):
    self.model.clear()

  def __model_del(self, key):
    treeIter = self.model.get_iter(key)
    self.model.remove(treeIter)

  def __model_set(self, key, entry):
    row = self._create_row_from_entry(entry)
    self.model[key] = row

  def _create_row_from_entry(self, entry):
    raise NotImplementedError("Subclass must implement abstract method")

