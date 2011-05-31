"""
Menu Item class, used by the drop-down menus
"""

class MenuItem():
  """Contains title, callback handler and possible children"""

  def __init__(self, label=None, callback=None, children=[], enabled=None):
    self._label = label
    self._callback = callback
    self._children = children
    self._enabled = enabled

  def getLabel(self):
    return self._label

  def isLeaf(self):
    return self._children == []

  def isParent(self):
    return self._children != []

  def isEnabled(self):
    if self._enabled == None:
      return True
    elif hasattr(self._enabled, '__call__'):
      return self._enabled()
    else:
      return self._enabled

  def getChildren(self):
    return self._children

  def getChildrenCount(self):
    return len(self._children)

  def select(self):
    self._callback(self)

