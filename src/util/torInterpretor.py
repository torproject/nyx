"""
Provides an interactive interpretor for working with the Tor control port. This
adds usability features like IRC style interpretor commands and, when ran
directly, history and tab completion.
"""

import readline # simply importing this provides history to raw_input

from util import enum

TERM_COLORS = ("BLACK", "RED", "GREEN", "YELLOW", "BLUE", "MAGENTA", "CYAN", "WHITE")

Color = enum.Enum(*TERM_COLORS)
BgColor = enum.Enum(*["BG_" + color for color in TERM_COLORS])
Attr = enum.Enum("BOLD", "UNDERLINE", "HILIGHT")

FG_ENCODING = dict([(Color.values()[i], str(30 + i)) for i in range(8)])
BG_ENCODING = dict([(BgColor.values()[i], str(40 + i)) for i in range(8)])
ATTR_ENCODING = {Attr.BOLD: "1", Attr.UNDERLINE: "4", Attr.HILIGHT: "7"}

CSI = "\x1B[%sm"
RESET = CSI % "0"

def format(msg, *attr):
  """
  Simple terminal text formatting, using ANSI escape sequences from:
  https://secure.wikimedia.org/wikipedia/en/wiki/ANSI_escape_code#CSI_codes
  
  toolkits providing similar capabilities:
  * django.utils.termcolors
    https://code.djangoproject.com/browser/django/trunk/django/utils/termcolors.py
  
  * termcolor
    http://pypi.python.org/pypi/termcolor
  
  * colorama
    http://pypi.python.org/pypi/colorama
  
  Arguments:
    msg  - string to be formatted
    attr - text attributes, this can be Color, BgColor, or Attr enums and are
           case insensitive (so strings like "red" are fine)
  """
  
  encodings = []
  for textAttr in attr:
    textAttr, encoding = enum.toCamelCase(textAttr), None
    encoding = FG_ENCODING.get(textAttr, encoding)
    encoding = BG_ENCODING.get(textAttr, encoding)
    encoding = ATTR_ENCODING.get(textAttr, encoding)
    if encoding: encodings.append(encoding)
  
  if encodings:
    return (CSI % ";".join(encodings)) + msg + RESET
  else:
    raise IOError("BLARG! %s" % str(attr))
    return msg

def prompt():
  prompt = format(">>> ", Color.GREEN, Attr.BOLD)
  input = ""
  
  while input != "/quit":
    input = raw_input(prompt)
    print format("echoing back '%s'" % input, Color.BLUE)

