"""
Helper module for getting Gtk+ theme colors.
"""

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

