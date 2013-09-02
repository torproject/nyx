"""
Menu item, representing an option in the drop-down menu.
"""

import cli.controller

class MenuItem():
  """
  Option in a drop-down menu.
  """
  
  def __init__(self, label, callback):
    self._label = label
    self._callback = callback
    self._parent = None
  
  def getLabel(self):
    """
    Provides a tuple of three strings representing the prefix, label, and
    suffix for this item.
    """
    
    return ("", self._label, "")
  
  def getParent(self):
    """
    Provides the Submenu we're contained within.
    """
    
    return self._parent
  
  def getHierarchy(self):
    """
    Provides a list with all of our parents, up to the root.
    """
    
    myHierarchy = [self]
    while myHierarchy[-1].getParent():
      myHierarchy.append(myHierarchy[-1].getParent())
    
    myHierarchy.reverse()
    return myHierarchy
  
  def getRoot(self):
    """
    Provides the base submenu we belong to.
    """
    
    if self._parent: return self._parent.getRoot()
    else: return self
  
  def select(self):
    """
    Performs the callback for the menu item, returning true if we should close
    the menu and false otherwise.
    """
    
    if self._callback:
      control = cli.controller.getController()
      control.setMsg()
      control.redraw()
      self._callback()
    return True
  
  def next(self):
    """
    Provides the next option for the submenu we're in, raising a ValueError
    if we don't have a parent.
    """
    
    return self._getSibling(1)
  
  def prev(self):
    """
    Provides the previous option for the submenu we're in, raising a ValueError
    if we don't have a parent.
    """
    
    return self._getSibling(-1)
  
  def _getSibling(self, offset):
    """
    Provides our sibling with a given index offset from us, raising a
    ValueError if we don't have a parent.
    
    Arguments:
      offset - index offset for the sibling to be returned
    """
    
    if self._parent:
      mySiblings = self._parent.getChildren()
      
      try:
        myIndex = mySiblings.index(self)
        return mySiblings[(myIndex + offset) % len(mySiblings)]
      except ValueError:
        # We expect a bidirectional references between submenus and their
        # children. If we don't have this then our menu's screwed up.
        
        msg = "The '%s' submenu doesn't contain '%s' (children: '%s')" % (self, self._parent, "', '".join(mySiblings))
        raise ValueError(msg)
    else: raise ValueError("Menu option '%s' doesn't have a parent" % self)
  
  def __str__(self):
    return self._label

class Submenu(MenuItem):
  """
  Menu item that lists other menu options.
  """
  
  def __init__(self, label):
    MenuItem.__init__(self, label, None)
    self._children = []
  
  def getLabel(self):
    """
    Provides our label with a ">" suffix to indicate that we have suboptions.
    """
    
    myLabel = MenuItem.getLabel(self)[1]
    return ("", myLabel, " >")
  
  def add(self, menuItem):
    """
    Adds the given menu item to our listing. This raises a ValueError if the
    item already has a parent.
    
    Arguments:
      menuItem - menu option to be added
    """
    
    if menuItem.getParent():
      raise ValueError("Menu option '%s' already has a parent" % menuItem)
    else:
      menuItem._parent = self
      self._children.append(menuItem)
  
  def getChildren(self):
    """
    Provides the menu and submenus we contain.
    """
    
    return list(self._children)
  
  def isEmpty(self):
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
  
  def __init__(self, action, selectedArg):
    self.action = action
    self.selectedArg = selectedArg

class SelectionMenuItem(MenuItem):
  """
  Menu item with an associated group which determines the selection. This is
  for the common single argument getter/setter pattern.
  """
  
  def __init__(self, label, group, arg):
    MenuItem.__init__(self, label, None)
    self._group = group
    self._arg = arg
  
  def isSelected(self):
    """
    True if we're the selected item, false otherwise.
    """
    
    return self._arg == self._group.selectedArg
  
  def getLabel(self):
    """
    Provides our label with a "[X]" prefix if selected and "[ ]" if not.
    """
    
    myLabel = MenuItem.getLabel(self)[1]
    myPrefix = "[X] " if self.isSelected() else "[ ] "
    return (myPrefix, myLabel, "")
  
  def select(self):
    """
    Performs the group's setter action with our argument.
    """
    
    if not self.isSelected():
      self._group.action(self._arg)
    
    return True

