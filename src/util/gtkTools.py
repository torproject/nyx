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

