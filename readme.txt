arm (arm relay monitor) - Terminal status monitor for Tor relays.
Developed by Damian Johnson (www.atagar.com - atagar1@gmail.com)
All code under the GPL v3 (http://www.gnu.org/licenses/gpl.html)

Description:
Command line application for monitoring Tor relays, providing real time status information such as the current configuration, bandwidth usage, message log, current connections, etc. This uses a curses interface much like 'top' does for system usage.

Requirements:
Python 2.5
TorCtl (retrieved in svn checkout)
Common *nix commands including: ps, pidof, host, and netstat
Tor is running with an available control port. This means either...
  ... starting Tor with '--controlport <PORT>'
  ... or including 'ControlPort <PORT>' in your torrc

This is started via 'arm' (use the '--help' argument for usage).

Warning: The second page (connections) provides the hostnames of Tor relays you're connected to. This means reverse DNS lookups which, if monitored, could leak your current connections to an eavesdropper. You can disable lookups with 'r' (see the page's help for the current status).

