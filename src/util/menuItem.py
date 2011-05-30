"""
Menu Item class, used by the drop-down menus
"""

class MenuItem():
  """Contains title, callback handler and possible children"""

  def __init__(self, label=None, callback=None, children=[]):
    self._label = label
    self._callback = callback
    self._children = children

  def getLabel(self):
    return self._label

  def isLeaf(self):
    return self._children == []

  def isParent(self):
    return self._children != []

  def getChildren(self):
    return self._children

  def getChildrenCount(self):
    return len(self._children)

  def select(self):
    self._callback(self)

