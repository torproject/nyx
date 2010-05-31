"""
Toolkit for common ui tasks when working with curses. This provides a quick and
easy method of providing the following interface components:
- preinitialized curses color attributes
- unit conversion for labels
"""

import sys
import curses

# colors curses can handle
COLOR_LIST = {"red": curses.COLOR_RED,        "green": curses.COLOR_GREEN,
              "yellow": curses.COLOR_YELLOW,  "blue": curses.COLOR_BLUE,
              "cyan": curses.COLOR_CYAN,      "magenta": curses.COLOR_MAGENTA,
              "black": curses.COLOR_BLACK,    "white": curses.COLOR_WHITE}

# mappings for getColor() - this uses the default terminal color scheme if
# color support is unavailable
COLOR_ATTR_INITIALIZED = False
COLOR_ATTR = dict([(color, 0) for color in COLOR_LIST])

# value tuples for label conversions (bytes / seconds, short label, long label)
SIZE_UNITS = [(1125899906842624.0, " PB", " Petabyte"), (1099511627776.0, " TB", " Terabyte"),
              (1073741824.0, " GB", " Gigabyte"),       (1048576.0, " MB", " Megabyte"),
              (1024.0, " KB", " Kilobyte"),             (1.0, " B", " Byte")]
TIME_UNITS = [(86400.0, "d", " day"),                   (3600.0, "h", " hour"),
              (60.0, "m", " minute"),                   (1.0, "s", " second")]

def getColor(color):
  """
  Provides attribute corresponding to a given text color. Supported colors
  include:
  red       green     yellow    blue
  cyan      magenta   black     white
  
  If color support isn't available or colors can't be initialized then this uses the 
  terminal's default coloring scheme.
  
  Arguments:
    color - name of the foreground color to be returned
  """
  
  if not COLOR_ATTR_INITIALIZED: _initColors()
  return COLOR_ATTR[color]

def cropStr(msg, size, minWordLen = 4, addEllipse = True):
  """
  Provides the msg constrained to the given length, truncating on word breaks.
  If the last words is long this truncates mid-word with an ellipse. If there
  isn't room for even a truncated single word (or one word plus the ellipse if
  inlcuding those) then this provides an empty string. Examples:
  
  cropStr("This is a looooong message", 17)
  "This is a looo..."
  
  cropStr("This is a looooong message", 12)
  "This is a..."
  
  cropStr("This is a looooong message", 3)
  ""
  
  Arguments:
    msg        - source text
    size       - room available for text
    minWordLen - minimum characters before which a word is dropped, requires
                 whole word if -1
    addEllipse - includes an ellipse when truncating if true (dropped if size
                 size is 
  """
  
  if minWordLen < 0: minWordLen = sys.maxint
  
  if len(msg) <= size: return msg
  else:
    msgWords = msg.split(" ")
    msgWords.reverse()
    
    returnWords = []
    sizeLeft = size - 3 if addEllipse else size
    
    # checks that there's room for at least one word
    if min(minWordLen, len(msgWords[-1])) > sizeLeft: return ""
    
    while sizeLeft > 0:
      nextWord = msgWords.pop()
      
      if len(nextWord) <= sizeLeft:
        returnWords.append(nextWord)
        sizeLeft -= (len(nextWord) + 1)
      elif minWordLen <= sizeLeft:
        returnWords.append(nextWord[:sizeLeft])
        sizeLeft = 0
      else: sizeLeft = 0
    
    returnMsg = " ".join(returnWords)
    if addEllipse: returnMsg += "..."
    return returnMsg

def getSizeLabel(bytes, decimal = 0, isLong = False):
  """
  Converts byte count into label in its most significant units, for instance
  7500 bytes would return "7 KB". If the isLong option is used this expands
  unit labels to be the properly pluralised full word (for instance 'Kilobytes'
  rather than 'KB'). Units go up through PB.
  
  Example Usage:
    getSizeLabel(2000000) = '1 MB'
    getSizeLabel(1050, 2) = '1.02 KB'
    getSizeLabel(1050, 3, True) = '1.025 Kilobytes'
  
  Arguments:
    bytes   - source number of bytes for conversion
    decimal - number of decimal digits to be included
    isLong  - expands units label
  """
  
  return _getLabel(SIZE_UNITS, bytes, decimal, isLong)

def getTimeLabel(seconds, decimal = 0, isLong = False):
  """
  Converts seconds into a time label truncated to its most significant units,
  for instance 7500 seconds would return "2h". Units go up through days.
  
  This defaults to presenting single character labels, but if the isLong option
  is used this expands labels to be the full word (space included and properly
  pluralised). For instance, "4h" would be "4 hours" and "1m" would become
  "1 minute".
  
  Example Usage:
    getTimeLabel(10000) = '2h'
    getTimeLabel(61, 1, True) = '1.0 minute'
    getTimeLabel(61, 2, True) = '1.01 minutes'
  
  Arguments:
    seconds - source number of seconds for conversion
    decimal - number of decimal digits to be included
    isLong  - expands units label
  """
  
  return _getLabel(TIME_UNITS, seconds, decimal, isLong)

def getTimeLabels(seconds, isLong = False):
  """
  Provides a list containing label conversions for each time unit, starting
  with its most significant units on down. Any counts that evaluate to zero are
  omitted.
  
  Example Usage:
    getTimeLabels(400) = ['6m', '40s']
    getTimeLabels(3640, True) = ['1 hour', '40 seconds']
  
  Arguments:
    seconds - source number of seconds for conversion
    isLong  - expands units label
  """
  
  timeLabels = []
  
  for countPerUnit, shortLabel, longLabel in TIME_UNITS:
    if seconds >= countPerUnit:
      timeLabels.append(_getLabel(TIME_UNITS, seconds, 0, isLong))
      seconds %= countPerUnit
  
  return timeLabels

def _getLabel(units, count, decimal, isLong):
  """
  Provides label corresponding to units of the highest significance in the
  provided set. This rounds down (ie, integer truncation after visible units).
  
  Arguments:
    units   - type of units to be used for conversion, a tuple containing
              (countPerUnit, shortLabel, longLabel)
    count   - number of base units being converted
    decimal - decimal precision of label
    isLong  - uses the long label if true, short label otherwise
  """
  
  format = "%%.%if" % decimal
  if count < 1:
    unitsLabel = units[-1][2] + "s" if isLong else units[-1][1]
    return "%s%s" % (format % count, unitsLabel)
  
  for countPerUnit, shortLabel, longLabel in units:
    if count >= countPerUnit:
      if count * 10 ** decimal % countPerUnit * 10 ** decimal == 0:
        # even division, keep it simple
        countLabel = format % (count / countPerUnit)
      else:
        # unfortunately the %f formatting has no method of rounding down, so
        # reducing value to only concern the digits that are visible - note
        # that this doesn't work with miniscule values (starts breaking down at
        # around eight decimal places) or edge cases when working with powers
        # of two
        croppedCount = count - (count % (countPerUnit / (10 ** decimal)))
        countLabel = format % (croppedCount / countPerUnit)
      
      if isLong:
        # plural if any of the visible units make it greater than one (for
        # instance 1.0003 is plural but 1.000 isn't)
        if decimal > 0: isPlural = count >= (countPerUnit + countPerUnit / (10 ** decimal))
        else: isPlural = count >= countPerUnit * 2
        return countLabel + longLabel + ("s" if isPlural else "")
      else: return countLabel + shortLabel

def _initColors():
  """
  Initializes color mappings usable by curses. This can only be done after
  calling curses.initscr().
  """
  
  global COLOR_ATTR_INITIALIZED
  if not COLOR_ATTR_INITIALIZED:
    try: hasColorSupport = curses.has_colors()
    except curses.error: return # initscr hasn't been called yet
    
    # initializes color mappings if color support is available
    COLOR_ATTR_INITIALIZED = True
    if hasColorSupport:
      colorpair = 0
      
      for colorName in COLOR_LIST:
        fgColor = COLOR_LIST[colorName]
        bgColor = -1 # allows for default (possibly transparent) background
        colorpair += 1
        curses.init_pair(colorpair, fgColor, bgColor)
        COLOR_ATTR[colorName] = curses.color_pair(colorpair)

