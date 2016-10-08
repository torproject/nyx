Change Log
==========

The following is a log of all user-facing changes to Nyx, both released and
unreleased. For a monthly report on work being done see my `development log
<http://blog.atagar.com/>`_.

* :ref:`versioning`
* :ref:`unreleased`
* :ref:`version_1.4.5`
* :ref:`version_1.4.4`
* :ref:`version_1.4.3`
* :ref:`version_1.4.2`
* :ref:`version_1.4.1`
* :ref:`version_1.4.0`
* :ref:`version_1.3.7`
* :ref:`version_1.3.6`
* :ref:`version_1.3.5`
* :ref:`version_1.3.4`
* :ref:`version_1.3.3`
* :ref:`version_1.3.2`
* :ref:`version_1.3.1`
* :ref:`version_1.3.0`
* :ref:`version_1.2.2`
* :ref:`version_1.2.1`
* :ref:`version_1.2.0`
* :ref:`version_1.1.3`
* :ref:`version_1.1.2`
* :ref:`version_1.1.1`
* :ref:`version_1.1.0`

.. _versioning:

Versioning
----------

As of the 2.x release Nyx uses `semantic versioning <http://semver.org/>`_,
which means that **versions consist of three numbers** (such as '**1.2.4**').
These are used to convey the kind of backward compatibility a release has...

 * The first value is the **major version**. This changes infrequently, and
   indicates that backward incompatible changes have been made (such as the
   removal of deprecated functions).

 * The second value is the **minor version**. This is the most common kind of
   release, and denotes that the improvements are backward compatible.

 * The third value is the **patch version**. When a Nyx release has a major
   issue another release is made which fixes just that problem. These do not
   contain substantial improvements or new features. This value is sometimes
   left off to indicate all releases with a given major/minor version.

Prior to version 2.x nyx did not follow any particular versioning scheme.

.. _unreleased:

Unreleased
----------

The following are only available within Nyx's `git repository
<download.html>`_.

From a user perspective little has changed, but this release is nothing less
than a complete rewrite of our codebase. This adds long overdue **support for
python 3.x**, test coverage, and migrate from TorCtl to `Stem
<https://stem.torproject.org/>`_.

Python 2.5 is no longer supported, but hopefully by now nobody will miss it. ;)

 * **Startup**

  * Startup is several seconds faster when ran for the first time

 * **Graph**

  * Graph prepopulation no longer requires shifting to 15 minute intervals

 * **Connections**

  * Connections are now shown despite DisableDebuggerAttachment
  * Support for showing IPv6 connections

 * **Logging**

  * Order of magnitude faster log deduplication

 * **Curses**

  * Interface continues to update while awaiting user input

.. _version_1.4.5:

Version 1.4.5 (April 28th, 2012)
--------------------------------

Software isn't perfect and Nyx is no exception. This is a bugfix release that
corrects most issues that users have reported over the last several months.
This did not include new features, but did have several changes that were
important for continued interoperability with tor.

 * **Startup**

  * Check auth cookie is 32 bytes before reading (:trac:`4305`)
  * Crash when tor log file contains leap year dates (:trac:`5265`)
  * Crash when using unrecognized authentication methods like 'SAFECOOKIE'

 * **Logging**

  * Path issue when saving snapshot of the logs (`issue <http://bugs.debian.org/cgi-bin/bugreport.cgi?bug=646080>`_)

 * **Connections**

  * Notify when DisableDebuggerAttachment prevents connection lookups
  * Better validation of circuit-staus output (:trac:`5267`)
  * Help information for 'enter' mislabeled (:trac:`4621`)
  * Circuits failed to show when connection information was unavailable

 * **Torrc**

  * Validation was case sensitive (:trac:`4601`)
  * Misleading DirReqStatistics warnings with new tor versions (:trac:`4237`)

 * **Curses**

  * Major terminal glitches related to the import of the readline module
  * Config option to work around ACS failures

 * **Cross-Platform Support**

  * **OSX/BSD:** support for pwd lookups (:trac:`4236`)
  * **OSX/BSD:** ps checks couldn't detect tor process
  * **OpenBSD:** only use lsof for connecion lookups
  * **Linux:** proc utils didn't account for big-endian architectures (:trac:`4777`)
  * **Debian:** misleading warning about default Logging value (:trac:`4602`)
  * **RedHat:** specify python verion in rpm dependencies

.. _version_1.4.4:

Version 1.4.4 (September 25th, 2011)
------------------------------------

Besides the normal bug fixes and minor features, this release introduces the
**control interpreter**. This is a new prompt that gives raw control port
access with tab completion, history scrollback, and irc-style command.

 * **Startup**

  * ControlSocket support (:trac:`3638`)
  * Notify when tor or nyx are running as root
  * Take chroot into consideration for auth cookie path
  * Don't start wizard when there's a tor process running, even if we can't connect to it
  * Try all authentication methods rather than just the first (:trac:`3958`)

 * **Graph**

  * Crash when pausing if we showed accounting stats

 * **Logging**

  * Skip reading from malformed tor log files
  * Unable to log GUARD events

 * **Connections**

  * Added dialogs with exit usage by port and guard/bridge usage by locale
  * Crash when shutting down while relay addresses are resolved
  * Crash when CIRC event occured while caching attached relays

 * **Configuration Editor**

  * Optional system wide torrc integration (:trac:`3629`)
  * We wrote a blank torrc when 'GETINFO config-text' was unavailable
  * Hotkey for saving the torrc conflicted with the relay setup wizard
  * Crash when pressing 'enter' if never attached to tor

 * **Wizard**

  * Quit wizard when the user presses 'q' rather than just esc (:trac:`3995`)

 * **Curses**

  * Force manual redraw when user presses ctrl+L (:trac:`2830`)
  * Quitting could cause unclean curses shutdown
  * Periodically redraw content to prevent terminal issues from persisting

 * **Website and Manual**

  * Moved downloads to archive.torproject.org for ssl
  * Incorrect nyxrc path in man page

 * **Hotfix release** (September 29th, 2011) corrected the following...

  * Crash when esc was pressed in the interpreter prompt (:trac:`4098`)
  * Deduplicationg couple common log messages (:trac:`4096`)
  * Ctrl+L redraw wasn't always being triggered (:trac:`2830`)
  * Dropped gtk/cagraph requirements

.. _version_1.4.3:

Version 1.4.3 (July 16th, 2011)
-------------------------------

This completes the codebase refactoring that's been a year in the works and
provides numerous performance and usability improvements. Most notably a
**setup wizard for new relays** and **menu interface**. This release also
includes gui prototype, performance improvements, and support for Mac OSX.

 * **Startup**

  * Renamed our process from "python src/starter.py" to "nyx"
  * Moved connection negotiation into torctl (:trac:`3409`)
  * Avoid excessive torctl memory allocation, lowering memory usage by 2.5 MB (12%) (:trac:`3406`)
  * More descriptive controller password prompt
  * Crash when a sighup crashes tor (:trac:`1329`)
  * Crash from unjoined threads during shutdown
  * Crash when pressing ctrl+c due to improper daemon shutdown
  * Crash when using the --debug argument with old tor versions
  * Crash when tor's socks port was used rather than the control port (:trac:`2580`)

 * **Header**

  * Requests a new identity when the user presses 'n'
  * Option to reconnect when tor's restarted
  * Provides file descriptor usage when tor is running out
  * Dropped file descriptor popup (both unused and inaccurate)
  * Indicate when tor's shut down in client mode

 * **Graph**

  * Pre-populates total bandwidth uploaded/downloaded
  * More intuitive mode toggling for resizing the graph
  * Intermediate graph bounds inaccurate or missing

 * **Connections**

  * Reintroduced descriptor popup
  * Provide nickname for circuit connections
  * Shut down torctl zombie connections to the control port (:trac:`2812`)
  * Misparsed circuit paths for tor versions prior to 0.2.2.1
  * Crash when pressing enter on a blank connection page (:trac:`3128`)
  * Crash when querying locales if geoip information was unavailable

 * **Configuration Editor**

  * Using SAVECONF rather than writing torrc directly
  * Edited config entries didn't display new value
  * Using extra horizontal space for the configuration values
  * Fallback configuration descriptions weren't being installed
  * Misparsed config option types for old tor versions

 * **Torrc**

  * Validation false positives for autogenerated Nickname values

 * **Curses**

  * Option to exclude panels from the interface
  * Option to override all displayed color
  * Speeding nyx's startup time from 0.84s to 0.14s (83% improvement by fetching connections in background)
  * Speeding nyx's shutdown time form ~1s to instantaneous (:trac:`2412`)
  * Display was cropped by an extra cell
  * Closing all message prompts when a key is pressed
  * Crash when cropping whitespace-only strings

 * **Manual**

  * Hardcoded home path rather than ~

 * **Website**

  * Moved nyx's codebase to git, with helper scripts to replace svn:externals and export

 * **Cross-Platform Support**

  * **OSX:** tor's pid couldn't be resolved, breaking much of nyx
  * **OSX:** only use lsof for connecion lookups

.. _version_1.4.2:

Version 1.4.2 (April 4th, 2011)
-------------------------------

This release re-implements the connection panel. Besides maintainability, this
includes several features like circuit paths, application connections, and
better type identification.

 * **Startup**

  * Faster startup by lazy loading 'address => fingerprint' mappings
  * Dropped warning suggesting users set FetchUselessDescriptors
  * Failed connection attempts caused zombie connections (:trac:`2812`)
  * nyxrc option 'startup.dataDirectory' didn't work
  * Crash when using python 2.5 due to missing bin built-in
  * Crash when family entries have a trailing comma (:trac:`2414`)
  * Crash from uncaught OSError when making directories failed
  * Crash joining with torctl thread during shutdown
  * Crash citing 'syshook' during shutdown

 * **Header**

  * Displayed wrong address if changed since first started (:trac:`2776`)

 * **Graph**

  * Dropping use of the state file for bandwidth totals due to having just a day's worth of data

 * **Connections**

  * Listing active circuits
  * Identifying connection applications (firefox, vidalia, etc)
  * Identifying common port usage for exit connections
  * Display 'local -> internal -> external' address when there's room
  * Address order inverted for SOCKS and CONTROL connections
  * Better identifying client and directory connections
  * Better disambiguating multiple relays with the same address
  * Better space utilization for a variety of screen sizes
  * Detail popup no longer freezes the rest of the display
  * Detail popup now uses the full screen width and is dynamically resizable
  * Take DirServer and AlternateDirAuthority into account to determine authorities
  * Didn't recognize 172.* address as a private IP range
  * Renamed the 'APPLICATION' type to 'SOCKS'
  * Crash due to unknown relay nicknames

 * **Configuration Editor**

  * Hiding infrequently used config options by default
  * Better caching, reducing CPU use when scrolling by 40%

 * **Torrc**

  * Validation requires 'GETINFO config-text' from Tor verison 0.2.2.7 (:trac:`2501`)
  * Line numbers for torrc issues were off by one
  * Allowed sorting by 'is default' attribute

 * **Manual**

  * Instructions for setting up authentication in the readme

 * **Cross-Platform Support**

  * **BSD:** broken resolver availability checks caused connections to not show up for several seconds

 * **Hotfix release** (April 4th, 2011) - crash when parsing multiple spaces in the HiddenServicePort
 * **Hotfix release** (April 6th, 2011) - installing missed new files
 * **Hotfix release** (April 13th, 2011) - crash when requesting our flags failed

.. _version_1.4.1:

Version 1.4.1 (January 7th, 2011)
---------------------------------

Platform specific enhancements including BSD compatibility and greatly improved
performance on Linux.

 * **Startup**

  * '--debug' argument for dumping debugging information
  * Centralizing nyx resources in ~/.nyx
  * Expanding relative authentication cookie paths
  * Startup forked rather than execed our process
  * Crash with invlid paths including spaces and dashes
  * Crash when text input fields shown with python 2.5

 * **Header**

  * Displaying nyx's cpu usage
  * Updating uptime each second
  * More accurate measurement of tor cpu usage

 * **Logging**

  * No date dividers when scrollbars not present

 * **Connections**

  * Labeling use of our socks port as client connections
  * Provide UDP connections to include DNS lookups
  * Some resolvers failed when pid was unavailable
  * Dropping locale for internal connections
  * Skipping internal -> external address translation for private addresses
  * Initially shown connections often lacked the pid
  * Connection resolution failed when tor ran under a different name
  * Crash when presenting an undefined nickname

 * **Configuration Editor**

  * Summary descriptions of config options
  * Fallback manual information when tor's man page is unavailable
  * Crash when querying hidden service parameters

 * **Torrc**

  * Reloading torrc contents when there's a sighup
  * Validation false positives when GETCONF response has spaces

 * **Cross-Platform Support**

  * **Linux:** retrieving process information directly from proc, dramatically improving performance
  * **BSD:** pid resolution via pgrep and sockstat
  * **BSD:** connection resolution via sockstat, procstat, and lsof
  * **BSD:** auto-detecting path prefixes for FreeBSD jails

 * **Hotfix release** (January 11th, 2011) corrected the following...

  * Including platform, python version, and nyx/tor configurations in debug dumps
  * Crash when initial ps lookup fails

 * **Hotfix release** (January 12th, 2011) - properly parse ps results with decimal seconds
 * **Hotfix release** (January 15th, 2011) - adding --docPath argument to help Gentoo ebuilds (`issue <https://bugs.gentoo.org/349792>`_)

.. _version_1.4.0:

Version 1.4.0 (November 27th, 2010)
-----------------------------------

**New page to manage tor's configuration**, along with several revisions in
preparation for being included in Debian.

 * **Startup**

  * Moved installation location to /usr/share/nyx
  * Replaced deb/rpm build resources with helper scripts
  * Removing autogenerated egg file from deb build
  * Including dh_pysupport flag to recognize private python module
  * Dropping references to the controller password after startup
  * Continued running in a broken state after ctrl+c due to non-daemon threads

 * **Logging**

  * Added scrollbar and scrolling by displayed content rather than line numbers
  * Disabling deduplications for long logs to avoid freezing interface
  * Crash when displaying empty torrc contents

 * **Torrc**

  * Validation notice when tor's present configuration doesn't match the torrc
  * Validation notice when torrc entry matches its default value
  * Validation didn't recognize 'second' and 'byte' arguments
  * Parsing multiline torrc entries supported in tor 0.2.2.17
  * Buggy scrolling when comments were stripped

 * **Curses**

  * Popups more resilient to the interface being resized
  * Using curses.textpad to add support in text fields for arrow keys, emacs keybindings, etc
  * Rounding error determining our scrollbar size

 * **Manual**

  * Incorrect man path for the sample nyxrc

 * **Hotfix release** (November 30th, 2010) - installer crashed creating temporary directory for compressed man page

.. _version_1.3.7:

Version 1.3.7 (October 6th, 2010)
---------------------------------

Expanded log panel, installer, and deb/rpm builds.

 * **Startup**

  * Installation and removal scripts
  * Configurable path prefix for chroot jails
  * Using PidFile to get the pid if available
  * Dump stacktrace to /tmp when exceptions are raised while redrawing
  * Crash if ORPort left unset

 * **Header**

  * Caching for static GETINFO parameter
  * Drop irrelevant information when not running as a relay

 * **Graph**

  * Incremental y-axis measurements
  * Option for graph resizing
  * Measuring transfer rates in bits by default
  * Use update interval that matches tor's state file when prepopulating
  * Skip bandwidth prepopulation if not running as a relay
  * Properly update bandwidth stats during sighup
  * Race condition between heartbeat and first BW event
  * Crash when displayed in especially wide screens

 * **Logging**

  * Dividers for the date, bordering events from the same day
  * Deduplicating log entries
  * Option to clear the event log
  * Option for saving logged events, either as a snapshot or persistently
  * Support cropping events based on time
  * Redrawing with each event when at debug runlevel caused high cpu usage
  * Notice if tor supports event types that nyx doesn't
  * Better consolidation of identical runlevel labels
  * Performance improvements for log preopulation, caching, etc
  * Merging tor and nyx events by timestamp when prepopulating
  * Regex filtering broken for multiline log entries
  * Drop brackets if no events are being logged

 * **Connections**

  * Disabling DNS resolution to prevent leaking information to our resolvers
  * Failed to handle family entries identified by nickname

 * **Torrc**

  * Failed to parse torrc files with tabs
  * Remapping torrc aliases so GETCONF calls don't fail
  * Checking torrc logging types was case sensitive
  * Crash when ExitPolicy was undefined

 * **Curses**

  * Jumping to start/end of scrolling area when pressing home or end
  * Refreshing after popups to make the interface more responsive

 * **Manual**

  * Created man page

 * **Cross-Platform Support**

  * **Linux:** scripts and resources for making debs and rpms
  * **Debian:** change debian arch from any to all

 * **Hotfix release** (October 7th, 2010) - crash with TypeError in the graph panel

.. _version_1.3.6:

Version 1.3.6 (June 7th, 2010)
------------------------------

Performance improvements and a few nice features. This improves the refresh
rate (coinciding with a drop of cpu usage) from 30ms to 4ms, an 87%
improvement.

 * **Startup**

  * Faster quitting by no longer waiting on sleeping threads
  * Caching commonly fetched relay information (fingerprint, descriptor, etc)
  * Systems util to standardize usage, add caching, prevent stdout leakage, etc
  * Optionally fetch settings from a nyxrc file
  * Wrapper for TorCtl providing singleton accessor and better API
  * Drop support for the '-p' argument for security reasons
  * Crash if torctl reports TorCtlClosed before the first refresh

 * **Header**

  * Support reattaching when tor's stopped then restarted
  * Notify when tor's disconnected
  * Better handling of tiny displays
  * Better caching and background updating

 * **Graph**

  * Prepopulate bandwidth information from stat file when available
  * Provide observed and measured bandwidth stats
  * Option to restrict graph bounds to local minima and maxima
  * Account for MaxAdvertisedBandwidth in the effective bandwidth rate
  * Better caching and reduced redraw rate

 * **Connections**

  * Suspend connection resolution when tor's stopped
  * Don't initialize while in blind mode
  * ss resolution didn't specifying use of numeric ports
  * Issue defaulting connection resolver to one we predetermined to be available
  * Crash when trying to resolve addresses without network connectivity
  * Crash due to unjoined connection resolution thread when quitting

.. _version_1.3.5:

Version 1.3.5 (April 8th, 2010)
-------------------------------

Handful of small fixes amid codebase refactoring.

 * **Startup**

  * Issue resets via RELOAD signal rather than SIGHUP
  * Crash due to unexpected None values when calling GETCONF

 * **Logging**

  * Panel sometimes drew itself before properly positioned while starting up

 * **Connections**

  * Added lsof and ss connection resolvers
  * Option for selecting mode of resolution
  * Reduce connection resolution rate if calls are burdensome
  * Optional dns resolution via socket module (disabled by default due to worse performance)

 * **Curses**

  * Crash when use_default_colors() fails
  * Help keys weren't consistently bolded

.. _version_1.3.4:

Version 1.3.4 (March 7th, 2010)
-------------------------------

Bugfix bundle for a handful of issues.

 * **Startup**

  * Crash when user pressed ctrl+c due to uncaught KeyboardInterrupt

 * **Header**

  * Multi-line exit policies weren't interpreted correctly

 * **Connections**

  * Crash when consensus couldn't be retrieved

 * **Torrc**

  * Display bug when stripping comments if torrc is longer than the screen
  * Stripping didn't include inline comments
  * Validation failed for some CSV values like ExitPolicy

 * **Cross-Platform Support**

  * **Debian:** file descriptor limit estimation incorrect

 * **Hotfix release** (March 9th, 2010) - crash while starting up processing family connections
 * **Hotfix release** (April 7th, 2010) - sensitive data not scrubbed for inbound connections

.. _version_1.3.3:

Version 1.3.3 (February 27th, 2010)
-----------------------------------

Handful of issues brought up on irc, most notably scrubbing the interface of
sensitive information.

 * **Startup**

  * Checking for curses built-ins before starting up

 * **Graph**

  * Added precision for bandwidth cap and burst
  * Not resized properly during a sighup

 * **Connections**

  * Scrubbing sensitive client/exit information to address privacy concerns
  * Showing external address rather than local nat

 * **Manual**

  * Providing file descriptions in the README
  * Crash due to missing sockset and torctl imports

.. _version_1.3.2:

Version 1.3.2 (February 14th, 2010)
-----------------------------------

Small bugfix bundle.

 * **Header**

  * Couple system commands weren't suppressing stderr
  * Didn't account for ORListenAddress in the address we displayed

 * **Graph**

  * Mishandling DST for accounting's 'Time to reset'

 * **Manual**

  * Include copy of the GPL

 * **Curses**

  * Crash when too small for scrollbars to be drawn

.. _version_1.3.1:

Version 1.3.1 (February 7th, 2010)
----------------------------------

Small bugfix bundle, mostly focused on improving initialization.

 * **Startup**

  * Use PROTOCOLINFO to autodetect supported authentication and cookie location
  * Added the '--blind' argument to prevent connection lookups
  * Added the '--event' argument to select events to log by character flags

 * **Logging**

  * Condense event labels for runlevel ranges

.. _version_1.3.0:

Version 1.3.0 (November 29th, 2009)
-----------------------------------

Small bugfix bundle.

 * **Startup**

  * Commands can be invoked directly from the help popup
  * Suppress torctl startup issues from going to stdout

 * **Header**

  * Truncating version if too long
  * Error messaging when file descriptor dialog fails

 * **Connections**

  * Offset glitch when scrollbar is visible
  * Drop family entries if control port connection is closed

.. _version_1.2.2:

Version 1.2.2 (November 8th, 2009)
----------------------------------

Small bugfix bundle before starting a new job.

 * **Header**

  * File descriptor popup providing stats and a scrollable listing
  * Crash when cleaning up hostname cache

 * **Connections**

  * Include family relays in the connection listing
  * Stretching connection lines to fill the full screen

 * **Torrc**

  * Warning if torrc fails to load
  * Validation usually weren't detecting duplicates

.. _version_1.2.1:

Version 1.2.1 (October 21st, 2009)
----------------------------------

Torrc validation, improved event logging, and more.

 * **Startup**

  * Crash due to improperly closing torctl when quitting
  * Crash due to uncaught TorCtlClosed exceptions

 * **Header**

  * Notice when control port is closed
  * Progress bar when resolving a batch of hostnames
  * Information left inaccurate after sighup

 * **Connections**

  * Incorrect connection counts when paused
  * Noisy netstat and geoip failures when tor quit
  * Sorting broken when unpaused

 * **Torrc**

  * Verify that the torrc matches tor's actual state
  * Check for torrc entries that are irrelevant due to being duplicates

 * **Logging**

  * Support logging nyx and torctl events
  * Only prepopulate events from this tor instance
  * Limit number of prepopulated entries to prevent long startup time

.. _version_1.2.0:

Version 1.2.0 (October 16th, 2009)
----------------------------------

Small bugfix bundle.

 * **Startup**

  * Ask for confirmation when quitting

 * **Logging**

  * Prepopulation using tor's log file
  * Support multi-line log messages

 * **Connections**

  * Connection times became inaccurate when paused or not visible
  * Crash due to connection cache when paused

.. _version_1.1.3:

Version 1.1.3 (September 28th, 2009)
------------------------------------

Small bugfix bundle.

 * **Startup**

  * Fall back to ps to determine tor's pid

 * **Connections**

  * Query connections in the background rather than as part of rendering

 * **Torrc**

  * Expand relative torrc paths

.. _version_1.1.2:

Version 1.1.2 (September 27th, 2009)
------------------------------------

Small bugfix bundle.

 * **Graph**

  * Reloading static information after SIGHUP

 * **Manual**

  * Added a changelog

 * **Cross-Platform Support**

  * **OSX/BSD:** crash when system calls failed

.. _version_1.1.1:

Version 1.1.1 (September 23rd, 2009)
------------------------------------

Small bugfix bundle.

 * **Startup**

  * Notify if python version is incompatible
  * Added the '--version' argument to help with bug reports

 * **Graph**

  * Didn't account for RelayBandwidthRate/Burst in effective bandwidth

 * **Connections**

  * Provide additional connection information when room's available
  * Identifying directory connections
  * Preserving old listing when netstat fails

.. _version_1.1.0:

Version 1.1.0 (September 6th, 2009)
-----------------------------------

Initial release of Nyx.
