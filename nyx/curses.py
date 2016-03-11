"""
Toolkit for working with curses. Curses earns its name, and this abstracts away
its usage providing us more easy to use high level functions. This abstraction
may also allow us to use libraries like `PDCurses <http://pdcurses.sourceforge.net/>`_
if we want Windows support in the future too.

**Module Overview:**

::

  curses_attr - curses encoded text attribute

  is_color_supported - checks if terminal supports color output
  get_color_override - provides color we override requests with
  set_color_override - sets color we override requests with

.. data:: Color (enum)

  Terminal colors.

  =========== ===========
  Color       Description
  =========== ===========
  **RED**     red color
  **GREEN**   green color
  **YELLOW**  yellow color
  **BLUE**    blue color
  **CYAN**    cyan color
  **MAGENTA** magenta color
  **BLACK**   black color
  **WHITE**   white color
  =========== ===========

.. data:: Attr (enum)

  Terminal text attributes.

  =================== ===========
  Attr                Description
  =================== ===========
  **NORMAL**          no text attributes
  **BOLD**            heavy typeface
  **UNDERLINE**       underlined text
  **HIGHLIGHT**       inverted foreground and background
  =================== ===========
"""

from __future__ import absolute_import

import curses

import stem.util.conf
import stem.util.enum

from nyx.util import msg, log

# Text colors and attributes. These are *very* commonly used so including
# shorter aliases (so they can be referenced as just GREEN or BOLD).

Color = stem.util.enum.Enum('RED', 'GREEN', 'YELLOW', 'BLUE', 'CYAN', 'MAGENTA', 'BLACK', 'WHITE')
RED, GREEN, YELLOW, BLUE, CYAN, MAGENTA, BLACK, WHITE = list(Color)

Attr = stem.util.enum.Enum('NORMAL', 'BOLD', 'UNDERLINE', 'HIGHLIGHT')
NORMAL, BOLD, UNDERLINE, HIGHLIGHT = list(Attr)

CURSES_COLORS = {
  Color.RED: curses.COLOR_RED,
  Color.GREEN: curses.COLOR_GREEN,
  Color.YELLOW: curses.COLOR_YELLOW,
  Color.BLUE: curses.COLOR_BLUE,
  Color.CYAN: curses.COLOR_CYAN,
  Color.MAGENTA: curses.COLOR_MAGENTA,
  Color.BLACK: curses.COLOR_BLACK,
  Color.WHITE: curses.COLOR_WHITE,
}

CURSES_ATTRIBUTES = {
  Attr.NORMAL: curses.A_NORMAL,
  Attr.BOLD: curses.A_BOLD,
  Attr.UNDERLINE: curses.A_UNDERLINE,
  Attr.HIGHLIGHT: curses.A_STANDOUT,
}

DEFAULT_COLOR_ATTR = dict([(color, 0) for color in Color])
COLOR_ATTR = None


def conf_handler(key, value):
  if key == 'features.colorOverride':
    if value not in Color and value != 'None':
      raise ValueError(msg('usage.unable_to_set_color_override', color = value))


CONFIG = stem.util.conf.config_dict('nyx', {
  'features.colorOverride': 'None',
  'features.colorInterface': True,
}, conf_handler)


def curses_attr(*attributes):
  """
  Provides encoding for the given curses text attributes.

  :param list attributes: curses text attributes and colors

  :returns: **int** that can be used with curses
  """

  encoded = curses.A_NORMAL

  for attr in attributes:
    if attr in Color:
      override = get_color_override()
      encoded |= _color_attr()[override if override else attr]
    elif attr in Attr:
      encoded |= CURSES_ATTRIBUTES[attr]
    else:
      raise ValueError("'%s' isn't a valid curses text attribute" % attr)

  return encoded


def is_color_supported():
  """
  Checks if curses currently supports rendering colors.

  :returns: **True** if colors can be rendered, **False** otherwise
  """

  return _color_attr() != DEFAULT_COLOR_ATTR


def get_color_override():
  """
  Provides the override color used by the interface.

  :returns: :data:`~nyx.curses.Color` for the color requrests will be
    overwritten with, **None** if no override is set
  """

  color_override = CONFIG.get('features.colorOverride', 'None')
  return None if color_override == 'None' else color_override


def set_color_override(color = None):
  """
  Overwrites all requests for color with the given color instead.

  :param nyx.curses.Color color: color to override all requests with, **None**
    if color requests shouldn't be overwritten

  :raises: **ValueError** if the color name is invalid
  """

  nyx_config = stem.util.conf.get_config('nyx')

  if color is None:
    nyx_config.set('features.colorOverride', 'None')
  elif color in Color:
    nyx_config.set('features.colorOverride', color)
  else:
    raise ValueError(msg('usage.unable_to_set_color_override', color = color))


def _color_attr():
  """
  Initializes color mappings usable by curses. This can only be done after
  calling curses.initscr().
  """

  global COLOR_ATTR

  if COLOR_ATTR is None:
    if not CONFIG['features.colorInterface']:
      COLOR_ATTR = DEFAULT_COLOR_ATTR
    elif curses.has_colors():
      color_attr = dict(DEFAULT_COLOR_ATTR)

      for color_pair, color_name in enumerate(CURSES_COLORS):
        foreground_color = CURSES_COLORS[color_name]
        background_color = -1  # allows for default (possibly transparent) background
        curses.init_pair(color_pair + 1, foreground_color, background_color)
        color_attr[color_name] = curses.color_pair(color_pair + 1)

      log.info('setup.color_support_available')
      COLOR_ATTR = color_attr
    else:
      log.info('setup.color_support_unavailable')
      COLOR_ATTR = DEFAULT_COLOR_ATTR

  return COLOR_ATTR
