"""
Tor curses monitoring application.
"""

__version__ = '1.4.6-dev'
__release_date__ = 'April 28, 2011'
__author__ = 'Damian Johnson'
__contact__ = 'atagar@torproject.org'
__url__ = 'http://www.atagar.com/arm/'
__license__ = 'GPLv3'

__all__ = [
  'arguments',
  'config_panel',
  'connection_panel',
  'controller',
  'header_panel',
  'log_panel',
  'popups',
  'starter',
  'torrc_panel',
]

import distutils.spawn
import sys


def main():
  try:
    import nyx.starter
    nyx.starter.main()
  except ImportError as exc:
    if exc.message == 'No module named stem':
      if distutils.spawn.find_executable('pip') is not None:
        advice = ", try running 'sudo pip install stem'"
      elif distutils.spawn.find_executable('apt-get') is not None:
        advice = ", try running 'sudo apt-get install python-stem'"
      else:
        advice = ', you can find it at https://stem.torproject.org/download.html'

      print('nyx requires stem' + advice)
    elif exc.message == 'No module named curses':
      if distutils.spawn.find_executable('apt-get') is not None:
        advice = ", try running 'sudo apt-get install python-curses'"
      else:
        advice = ''  # not sure what to do for other platforms

      print('nyx requires curses' + advice)
    else:
      print('Unable to start nyx: %s' % exc)

    sys.exit(1)
