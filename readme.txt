arm (arm relay monitor) - Terminal status monitor for Tor relays.
Developed by Damian Johnson (www.atagar.com - atagar1@gmail.com)
All code under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

Description:
Command line application for monitoring Tor relays, providing real time status information such as the current configuration, bandwidth usage, message log, etc. This uses a curses interface much like 'top' does for system usage.

Requirements:
Python 2.5
TorCtl - This needs to be in your Python path. In Linux this can be done via:
  svn co https://tor-svn.freehaven.net/svn/torctl
  export PYTHONPATH=$PWD/trunk/python/
Tor is running with an available control port. This means either...
  ... starting Tor with '--controlport <PORT>'
  ... or including 'ControlPort <PORT>' in your torrc

This is started via arm.py (use the '--help' argument for usage).

Current Issues:
- The monitor's resilient to having it's width changed (down to five cells or so), but not its height. The problem is that curses moves and resizes vertically displaced subwindows so if the terminal's shrank, it won't grow back when restored. The Python curses bindings lack support for moving, resizing, or deleting subwindows so I'm at a bit of a loss for how to fix this. Shot an email to the Python users list but no bites so far...

- Currently TorCtl seems to like to provide log messages to the terminal, for instance when authentication fails it says:
atagar@odin:~/Desktop/tormoni$ python tormoni.py
NOTICE [ Wed May 13 13:10:13 2009 ]: Tor closed control connection. Exiting event thread.
Connection failed: 515 Authentication failed: Wrong length on authentication cookie.

The first message is from TorCtl and the second is mine. Tried remapping stderr but no luck. It's occasionally noisy with a TypeError when shutting down and messages seem capable of disrupting curses, overwriting displays. Planning on checking with Mike about this one.

- Cookie authentication fails roughly a quarter of the time. Matt had a suggestion about an alternative method of authentication that seems to be working so far, but since it's an intermittent problem I'll hold my breath a little while before calling this one solved.

