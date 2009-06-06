arm (arm relay monitor) - Terminal status monitor for Tor relays.
Developed by Damian Johnson (www.atagar.com - atagar1@gmail.com)
All code under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

Description:
Command line application for monitoring Tor relays, providing real time status information such as the current configuration, bandwidth usage, message log, etc. This uses a curses interface much like 'top' does for system usage.

Requirements:
Python 2.5
TorCtl - This needs to be in your Python path. In Linux this can be done via:
  svn co https://tor-svn.freehaven.net/svn/torctl
  export PYTHONPATH=$PWD/torctl/trunk/python/
Tor is running with an available control port. This means either...
  ... starting Tor with '--controlport <PORT>'
  ... or including 'ControlPort <PORT>' in your torrc

This is started via 'arm' (use the '--help' argument for usage).

