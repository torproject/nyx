"""
Basic enumeration, providing ordered types for collections. These can be
constructed as simple type listings, ie:
>>> insects = Enum("ANT", "WASP", "LADYBUG", "FIREFLY")
>>> insects.ANT
'Ant'
>>> insects.values()
['Ant', 'Wasp', 'Ladybug', 'Firefly']

with overwritten string counterparts:
>>> pets = Enum(("DOG", "Skippy"), "CAT", ("FISH", "Nemo"))
>>> pets.DOG
'Skippy'
>>> pets.CAT
'Cat'

or with entirely custom string components as an unordered enum with:
>>> pets = LEnum(DOG="Skippy", CAT="Kitty", FISH="Nemo")
>>> pets.CAT
'Kitty'
"""

def toCamelCase(label):
  """
  Converts the given string to camel case, ie:
  >>> toCamelCase("I_LIKE_PEPPERJACK!")
  'I Like Pepperjack!'
  
  Arguments:
    label - input string to be converted
  """
  
  words = []
  for entry in label.split("_"):
    if len(entry) == 0: words.append("")
    elif len(entry) == 1: words.append(entry.upper())
    else: words.append(entry[0].upper() + entry[1:].lower())
  
  return " ".join(words)

class Enum:
  """
  Basic enumeration.
  """
  
  def __init__(self, *args):
    self.orderedValues = []
    
    for entry in args:
      if isinstance(entry, str):
        key, val = entry, toCamelCase(entry)
      elif isinstance(entry, tuple) and len(entry) == 2:
        key, val = entry
      else: raise ValueError("Unrecognized input: %s" % args)
      
      self.__dict__[key] = val
      self.orderedValues.append(val)
  
  def values(self):
    """
    Provides an ordered listing of the enumerations in this set.
    """
    
    return list(self.orderedValues)
  
  def indexOf(self, value):
    """
    Provides the index of the given value in the collection. This raises a
    ValueError if no such element exists.
    
    Arguments:
      value - entry to be looked up
    """
    
    return self.orderedValues.index(value)
  
  def next(self, value):
    """
    Provides the next enumeration after the given value, raising a ValueError
    if no such enum exists.
    
    Arguments:
      value - enumeration for which to get the next entry
    """
    
    if not value in self.orderedValues:
      raise ValueError("No such enumeration exists: %s (options: %s)" % (value, ", ".join(self.orderedValues)))
    
    nextIndex = (self.orderedValues.index(value) + 1) % len(self.orderedValues)
    return self.orderedValues[nextIndex]
  
  def previous(self, value):
    """
    Provides the previous enumeration before the given value, raising a
    ValueError if no such enum exists.
    
    Arguments:
      value - enumeration for which to get the previous entry
    """
    
    if not value in self.orderedValues:
      raise ValueError("No such enumeration exists: %s (options: %s)" % (value, ", ".join(self.orderedValues)))
    
    prevIndex = (self.orderedValues.index(value) - 1) % len(self.orderedValues)
    return self.orderedValues[prevIndex]

class LEnum(Enum):
  """
  Enumeration that accepts custom string mappings.
  """
  
  def __init__(self, **args):
    Enum.__init__(self)
    self.__dict__.update(args)
    self.orderedValues = sorted(args.values())

