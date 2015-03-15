"""
Menu item, representing an option in the drop-down menu.
"""

import seth.controller


class MenuItem():
  """
  Option in a drop-down menu.
  """

  def __init__(self, label, callback):
    self._label = label
    self._callback = callback
    self._parent = None

  def get_label(self):
    """
    Provides a tuple of three strings representing the prefix, label, and
    suffix for this item.
    """

    return ("", self._label, "")

  def get_parent(self):
    """
    Provides the Submenu we're contained within.
    """

    return self._parent

  def get_hierarchy(self):
    """
    Provides a list with all of our parents, up to the root.
    """

    my_hierarchy = [self]
    while my_hierarchy[-1].get_parent():
      my_hierarchy.append(my_hierarchy[-1].get_parent())

    my_hierarchy.reverse()
    return my_hierarchy

  def get_root(self):
    """
    Provides the base submenu we belong to.
    """

    if self._parent:
      return self._parent.get_root()
    else:
      return self

  def select(self):
    """
    Performs the callback for the menu item, returning true if we should close
    the menu and false otherwise.
    """

    if self._callback:
      control = seth.controller.get_controller()
      control.set_msg()
      control.redraw()
      self._callback()
    return True

  def next(self):
    """
    Provides the next option for the submenu we're in, raising a ValueError
    if we don't have a parent.
    """

    return self._get_sibling(1)

  def prev(self):
    """
    Provides the previous option for the submenu we're in, raising a ValueError
    if we don't have a parent.
    """

    return self._get_sibling(-1)

  def _get_sibling(self, offset):
    """
    Provides our sibling with a given index offset from us, raising a
    ValueError if we don't have a parent.

    Arguments:
      offset - index offset for the sibling to be returned
    """

    if self._parent:
      my_siblings = self._parent.get_children()

      try:
        my_index = my_siblings.index(self)
        return my_siblings[(my_index + offset) % len(my_siblings)]
      except ValueError:
        # We expect a bidirectional references between submenus and their
        # children. If we don't have this then our menu's screwed up.

        msg = "The '%s' submenu doesn't contain '%s' (children: '%s')" % (self, self._parent, "', '".join(my_siblings))
        raise ValueError(msg)
    else:
      raise ValueError("Menu option '%s' doesn't have a parent" % self)

  def __str__(self):
    return self._label


class Submenu(MenuItem):
  """
  Menu item that lists other menu options.
  """

  def __init__(self, label):
    MenuItem.__init__(self, label, None)
    self._children = []

  def get_label(self):
    """
    Provides our label with a ">" suffix to indicate that we have suboptions.
    """

    my_label = MenuItem.get_label(self)[1]
    return ("", my_label, " >")

  def add(self, menu_item):
    """
    Adds the given menu item to our listing. This raises a ValueError if the
    item already has a parent.

    Arguments:
      menu_item - menu option to be added
    """

    if menu_item.get_parent():
      raise ValueError("Menu option '%s' already has a parent" % menu_item)
    else:
      menu_item._parent = self
      self._children.append(menu_item)

  def get_children(self):
    """
    Provides the menu and submenus we contain.
    """

    return list(self._children)

  def is_empty(self):
    """
    True if we have no children, false otherwise.
    """

    return not bool(self._children)

  def select(self):
    return False


class SelectionGroup():
  """
  Radio button groups that SelectionMenuItems can belong to.
  """

  def __init__(self, action, selected_arg):
    self.action = action
    self.selected_arg = selected_arg


class SelectionMenuItem(MenuItem):
  """
  Menu item with an associated group which determines the selection. This is
  for the common single argument getter/setter pattern.
  """

  def __init__(self, label, group, arg):
    MenuItem.__init__(self, label, None)
    self._group = group
    self._arg = arg

  def is_selected(self):
    """
    True if we're the selected item, false otherwise.
    """

    return self._arg == self._group.selected_arg

  def get_label(self):
    """
    Provides our label with a "[X]" prefix if selected and "[ ]" if not.
    """

    my_label = MenuItem.get_label(self)[1]
    my_prefix = "[X] " if self.is_selected() else "[ ] "
    return (my_prefix, my_label, "")

  def select(self):
    """
    Performs the group's setter action with our argument.
    """

    if not self.is_selected():
      self._group.action(self._arg)

    return True
