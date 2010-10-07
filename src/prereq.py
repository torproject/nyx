"""
Provides a warning and error code if python version isn't compatible.
"""

import sys

if __name__ == '__main__':
  majorVersion = sys.version_info[0]
  minorVersion = sys.version_info[1]
  
  if majorVersion > 2:
    print("arm isn't compatible beyond the python 2.x series\n")
    sys.exit(1)
  elif majorVersion < 2 or minorVersion < 5:
    print("arm requires python version 2.5 or greater\n")
    sys.exit(1)
  
  try:
    import curses
  except ImportError:
    print("arm requires curses - try installing the python-curses package\n")
    sys.exit(1)

