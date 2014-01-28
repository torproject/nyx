"""
Panel providing a chronological log of events its been configured to listen
for. This provides prepopulation from the log file and supports filtering by
regular expressions.
"""

import re
import os
import time
import curses
import logging
import threading

import stem
from stem.control import State
from stem.response import events
from stem.util import conf, log, system

import arm.arguments
import arm.popups
from arm import __version__
from arm.util import panel, tor_controller, ui_tools

RUNLEVEL_EVENT_COLOR = {
  log.DEBUG: "magenta",
  log.INFO: "blue",
  log.NOTICE: "green",
  log.WARN: "yellow",
  log.ERR: "red",
}

DAYBREAK_EVENT = "DAYBREAK"  # special event for marking when the date changes
TIMEZONE_OFFSET = time.altzone if time.localtime()[8] else time.timezone

ENTRY_INDENT = 2  # spaces an entry's message is indented after the first line


def conf_handler(key, value):
  if key == "features.log.max_lines_per_entry":
    return max(1, value)
  elif key == "features.log.prepopulateReadLimit":
    return max(0, value)
  elif key == "features.log.maxRefreshRate":
    return max(10, value)
  elif key == "cache.log_panel.size":
    return max(1000, value)


CONFIG = conf.config_dict("arm", {
  "features.log_file": "",
  "features.log.showDateDividers": True,
  "features.log.showDuplicateEntries": False,
  "features.log.entryDuration": 7,
  "features.log.max_lines_per_entry": 6,
  "features.log.prepopulate": True,
  "features.log.prepopulateReadLimit": 5000,
  "features.log.maxRefreshRate": 300,
  "features.log.regex": [],
  "cache.log_panel.size": 1000,
  "msg.misc.event_types": '',
  "tor.chroot": '',
}, conf_handler)

DUPLICATE_MSG = " [%i duplicate%s hidden]"

# The height of the drawn content is estimated based on the last time we redrew
# the panel. It's chiefly used for scrolling and the bar indicating its
# position. Letting the estimate be too inaccurate results in a display bug, so
# redraws the display if it's off by this threshold.

CONTENT_HEIGHT_REDRAW_THRESHOLD = 3

# static starting portion of common log entries, fetched from the config when
# needed if None

COMMON_LOG_MESSAGES = None

# cached values and the arguments that generated it for the get_daybreaks and
# get_duplicates functions

CACHED_DAYBREAKS_ARGUMENTS = (None, None)  # events, current day
CACHED_DAYBREAKS_RESULT = None
CACHED_DUPLICATES_ARGUMENTS = None  # events
CACHED_DUPLICATES_RESULT = None

# duration we'll wait for the deduplication function before giving up (in ms)

DEDUPLICATION_TIMEOUT = 100

# maximum number of regex filters we'll remember

MAX_REGEX_FILTERS = 5


def days_since(timestamp = None):
  """
  Provides the number of days since the epoch converted to local time (rounded
  down).

  Arguments:
    timestamp - unix timestamp to convert, current time if undefined
  """

  if timestamp is None:
    timestamp = time.time()

  return int((timestamp - TIMEZONE_OFFSET) / 86400)


def load_log_messages():
  """
  Fetches a mapping of common log messages to their runlevels from the config.
  """

  global COMMON_LOG_MESSAGES
  arm_config = conf.get_config("arm")

  COMMON_LOG_MESSAGES = {}

  for conf_key in arm_config.keys():
    if conf_key.startswith("dedup."):
      event_type = conf_key[4:].upper()
      messages = arm_config.get(conf_key, [])
      COMMON_LOG_MESSAGES[event_type] = messages


def get_log_file_entries(runlevels, read_limit = None, add_limit = None):
  """
  Parses tor's log file for past events matching the given runlevels, providing
  a list of log entries (ordered newest to oldest). Limiting the number of read
  entries is suggested to avoid parsing everything from logs in the GB and TB
  range.

  Arguments:
    runlevels - event types (DEBUG - ERR) to be returned
    read_limit - max lines of the log file that'll be read (unlimited if None)
    add_limit  - maximum entries to provide back (unlimited if None)
  """

  start_time = time.time()

  if not runlevels:
    return []

  # checks tor's configuration for the log file's location (if any exists)

  logging_types, logging_location = None, None

  for logging_entry in tor_controller().get_conf("Log", [], True):
    # looks for an entry like: notice file /var/log/tor/notices.log

    entry_comp = logging_entry.split()

    if entry_comp[1] == "file":
      logging_types, logging_location = entry_comp[0], entry_comp[2]
      break

  if not logging_location:
    return []

  # includes the prefix for tor paths

  logging_location = CONFIG['tor.chroot'] + logging_location

  # if the runlevels argument is a superset of the log file then we can
  # limit the read contents to the add_limit

  runlevels = list(log.Runlevel)
  logging_types = logging_types.upper()

  if add_limit and (not read_limit or read_limit > add_limit):
    if "-" in logging_types:
      div_index = logging_types.find("-")
      start_index = runlevels.index(logging_types[:div_index])
      end_index = runlevels.index(logging_types[div_index + 1:])
      log_file_run_levels = runlevels[start_index:end_index + 1]
    else:
      start_index = runlevels.index(logging_types)
      log_file_run_levels = runlevels[start_index:]

    # checks if runlevels we're reporting are a superset of the file's contents

    is_file_subset = True

    for runlevel_type in log_file_run_levels:
      if runlevel_type not in runlevels:
        is_file_subset = False
        break

    if is_file_subset:
      read_limit = add_limit

  # tries opening the log file, cropping results to avoid choking on huge logs

  lines = []

  try:
    if read_limit:
      lines = system.call("tail -n %i %s" % (read_limit, logging_location))

      if not lines:
        raise IOError()
    else:
      log_file = open(logging_location, "r")
      lines = log_file.readlines()
      log_file.close()
  except IOError:
    log.warn("Unable to read tor's log file: %s" % logging_location)

  if not lines:
    return []

  logged_events = []
  current_unix_time, current_local_time = time.time(), time.localtime()

  for i in range(len(lines) - 1, -1, -1):
    line = lines[i]

    # entries look like:
    # Jul 15 18:29:48.806 [notice] Parsing GEOIP file.

    line_comp = line.split()

    # Checks that we have all the components we expect. This could happen if
    # we're either not parsing a tor log or in weird edge cases (like being
    # out of disk space)

    if len(line_comp) < 4:
      continue

    event_type = line_comp[3][1:-1].upper()

    if event_type in runlevels:
      # converts timestamp to unix time

      timestamp = " ".join(line_comp[:3])

      # strips the decimal seconds

      if "." in timestamp:
        timestamp = timestamp[:timestamp.find(".")]

      # Ignoring wday and yday since they aren't used.
      #
      # Pretend the year is 2012, because 2012 is a leap year, and parsing a
      # date with strptime fails if Feb 29th is passed without a year that's
      # actually a leap year. We can't just use the current year, because we
      # might be parsing old logs which didn't get rotated.
      #
      # https://trac.torproject.org/projects/tor/ticket/5265

      timestamp = "2012 " + timestamp
      event_time_comp = list(time.strptime(timestamp, "%Y %b %d %H:%M:%S"))
      event_time_comp[8] = current_local_time.tm_isdst
      event_time = time.mktime(event_time_comp)  # converts local to unix time

      # The above is gonna be wrong if the logs are for the previous year. If
      # the event's in the future then correct for this.

      if event_time > current_unix_time + 60:
        event_time_comp[0] -= 1
        event_time = time.mktime(event_time_comp)

      event_msg = " ".join(line_comp[4:])
      logged_events.append(LogEntry(event_time, event_type, event_msg, RUNLEVEL_EVENT_COLOR[event_type]))

    if "opening log file" in line:
      break  # this entry marks the start of this tor instance

  if add_limit:
    logged_events = logged_events[:add_limit]

  log.info("Read %i entries from tor's log file: %s (read limit: %i, runtime: %0.3f)" % (len(logged_events), logging_location, read_limit, time.time() - start_time))

  return logged_events


def get_daybreaks(events, ignore_time_for_cache = False):
  """
  Provides the input events back with special 'DAYBREAK_EVENT' markers inserted
  whenever the date changed between log entries (or since the most recent
  event). The timestamp matches the beginning of the day for the following
  entry.

  Arguments:
    events             - chronologically ordered listing of events
    ignore_time_for_cache - skips taking the day into consideration for providing
                         cached results if true
  """

  global CACHED_DAYBREAKS_ARGUMENTS, CACHED_DAYBREAKS_RESULT

  if not events:
    return []

  new_listing = []
  current_day = days_since()
  last_day = current_day

  if CACHED_DAYBREAKS_ARGUMENTS[0] == events and \
    (ignore_time_for_cache or CACHED_DAYBREAKS_ARGUMENTS[1] == current_day):
    return list(CACHED_DAYBREAKS_RESULT)

  for entry in events:
    event_day = days_since(entry.timestamp)

    if event_day != last_day:
      marker_timestamp = (event_day * 86400) + TIMEZONE_OFFSET
      new_listing.append(LogEntry(marker_timestamp, DAYBREAK_EVENT, "", "white"))

    new_listing.append(entry)
    last_day = event_day

  CACHED_DAYBREAKS_ARGUMENTS = (list(events), current_day)
  CACHED_DAYBREAKS_RESULT = list(new_listing)

  return new_listing


def get_duplicates(events):
  """
  Deduplicates a list of log entries, providing back a tuple listing with the
  log entry and count of duplicates following it. Entries in different days are
  not considered to be duplicates. This times out, returning None if it takes
  longer than DEDUPLICATION_TIMEOUT.

  Arguments:
    events - chronologically ordered listing of events
  """

  global CACHED_DUPLICATES_ARGUMENTS, CACHED_DUPLICATES_RESULT

  if CACHED_DUPLICATES_ARGUMENTS == events:
    return list(CACHED_DUPLICATES_RESULT)

  # loads common log entries from the config if they haven't been

  if COMMON_LOG_MESSAGES is None:
    load_log_messages()

  start_time = time.time()
  events_remaining = list(events)
  return_events = []

  while events_remaining:
    entry = events_remaining.pop(0)
    duplicate_indices = is_duplicate(entry, events_remaining, True)

    # checks if the call timeout has been reached

    if (time.time() - start_time) > DEDUPLICATION_TIMEOUT / 1000.0:
      return None

    # drops duplicate entries

    duplicate_indices.reverse()

    for i in duplicate_indices:
      del events_remaining[i]

    return_events.append((entry, len(duplicate_indices)))

  CACHED_DUPLICATES_ARGUMENTS = list(events)
  CACHED_DUPLICATES_RESULT = list(return_events)

  return return_events


def is_duplicate(event, event_set, get_duplicates = False):
  """
  True if the event is a duplicate for something in the event_set, false
  otherwise. If the get_duplicates flag is set this provides the indices of
  the duplicates instead.

  Arguments:
    event         - event to search for duplicates of
    event_set      - set to look for the event in
    get_duplicates - instead of providing back a boolean this gives a list of
                    the duplicate indices in the event_set
  """

  duplicate_indices = []

  for i in range(len(event_set)):
    forward_entry = event_set[i]

    # if showing dates then do duplicate detection for each day, rather
    # than globally

    if forward_entry.type == DAYBREAK_EVENT:
      break

    if event.type == forward_entry.type:
      is_duplicate = False

      if event.msg == forward_entry.msg:
        is_duplicate = True
      elif event.type in COMMON_LOG_MESSAGES:
        for common_msg in COMMON_LOG_MESSAGES[event.type]:
          # if it starts with an asterisk then check the whole message rather
          # than just the start

          if common_msg[0] == "*":
            is_duplicate = common_msg[1:] in event.msg and common_msg[1:] in forward_entry.msg
          else:
            is_duplicate = event.msg.startswith(common_msg) and forward_entry.msg.startswith(common_msg)

          if is_duplicate:
            break

      if is_duplicate:
        if get_duplicates:
          duplicate_indices.append(i)
        else:
          return True

  if get_duplicates:
    return duplicate_indices
  else:
    return False


class LogEntry():
  """
  Individual log file entry, having the following attributes:
    timestamp - unix timestamp for when the event occurred
    event_type - event type that occurred ("INFO", "BW", "ARM_WARN", etc)
    msg       - message that was logged
    color     - color of the log entry
  """

  def __init__(self, timestamp, event_type, msg, color):
    self.timestamp = timestamp
    self.type = event_type
    self.msg = msg
    self.color = color
    self._display_message = None

  def get_display_message(self, include_date = False):
    """
    Provides the entry's message for the log.

    Arguments:
      include_date - appends the event's date to the start of the message
    """

    if include_date:
      # not the common case so skip caching
      entry_time = time.localtime(self.timestamp)
      time_label = "%i/%i/%i %02i:%02i:%02i" % (entry_time[1], entry_time[2], entry_time[0], entry_time[3], entry_time[4], entry_time[5])
      return "%s [%s] %s" % (time_label, self.type, self.msg)

    if not self._display_message:
      entry_time = time.localtime(self.timestamp)
      self._display_message = "%02i:%02i:%02i [%s] %s" % (entry_time[3], entry_time[4], entry_time[5], self.type, self.msg)

    return self._display_message


class LogPanel(panel.Panel, threading.Thread, logging.Handler):
  """
  Listens for and displays tor, arm, and stem events. This can prepopulate
  from tor's log file if it exists.
  """

  def __init__(self, stdscr, logged_events):
    panel.Panel.__init__(self, stdscr, "log", 0)
    logging.Handler.__init__(self, level = log.logging_level(log.DEBUG))

    self.setFormatter(logging.Formatter(
      fmt = '%(asctime)s [%(levelname)s] %(message)s',
      datefmt = '%m/%d/%Y %H:%M:%S'),
    )

    threading.Thread.__init__(self)
    self.setDaemon(True)

    # Make sure that the msg.* messages are loaded. Lazy loading it later is
    # fine, but this way we're sure it happens before warning about unused
    # config options.

    load_log_messages()

    # regex filters the user has defined

    self.filter_options = []

    for filter in CONFIG["features.log.regex"]:
      # checks if we can't have more filters

      if len(self.filter_options) >= MAX_REGEX_FILTERS:
        break

      try:
        re.compile(filter)
        self.filter_options.append(filter)
      except re.error as exc:
        log.notice("Invalid regular expression pattern (%s): %s" % (exc, filter))

    self.logged_events = []  # needs to be set before we receive any events

    # restricts the input to the set of events we can listen to, and
    # configures the controller to liten to them

    self.logged_events = self.set_event_listening(logged_events)

    self.set_pause_attr("msg_log")       # tracks the message log when we're paused
    self.msg_log = []                    # log entries, sorted by the timestamp
    self.regex_filter = None             # filter for presented log events (no filtering if None)
    self.last_content_height = 0         # height of the rendered content when last drawn
    self.log_file = None                 # file log messages are saved to (skipped if None)
    self.scroll = 0

    self._last_update = -1               # time the content was last revised
    self._halt = False                   # terminates thread if true
    self._cond = threading.Condition()   # used for pausing/resuming the thread

    # restricts concurrent write access to attributes used to draw the display
    # and pausing:
    # msg_log, logged_events, regex_filter, scroll

    self.vals_lock = threading.RLock()

    # cached parameters (invalidated if arguments for them change)
    # last set of events we've drawn with

    self._last_logged_events = []

    # _get_title (args: logged_events, regex_filter pattern, width)

    self._title_cache = None
    self._title_args = (None, None, None)

    self.reprepopulate_events()

    # leaving last_content_height as being too low causes initialization problems

    self.last_content_height = len(self.msg_log)

    # adds listeners for tor and stem events

    controller = tor_controller()
    controller.add_status_listener(self._reset_listener)

    # opens log file if we'll be saving entries

    if CONFIG["features.log_file"]:
      log_path = CONFIG["features.log_file"]

      try:
        # make dir if the path doesn't already exist

        base_dir = os.path.dirname(log_path)

        if not os.path.exists(base_dir):
          os.makedirs(base_dir)

        self.log_file = open(log_path, "a")
        log.notice("arm %s opening log file (%s)" % (__version__, log_path))
      except IOError as exc:
        log.error("Unable to write to log file: %s" % exc.strerror)
        self.log_file = None
      except OSError as exc:
        log.error("Unable to write to log file: %s" % exc)
        self.log_file = None

    stem_logger = log.get_logger()
    stem_logger.addHandler(self)

  def emit(self, record):
    if record.levelname == "WARNING":
      record.levelname = "WARN"

    event_color = RUNLEVEL_EVENT_COLOR[record.levelname]
    self.register_event(LogEntry(int(record.created), "ARM_%s" % record.levelname, record.msg, event_color))

  def reprepopulate_events(self):
    """
    Clears the event log and repopulates it from the arm and tor backlogs.
    """

    self.vals_lock.acquire()

    # clears the event log

    self.msg_log = []

    # fetches past tor events from log file, if available

    if CONFIG["features.log.prepopulate"]:
      set_runlevels = list(set.intersection(set(self.logged_events), set(list(log.Runlevel))))
      read_limit = CONFIG["features.log.prepopulateReadLimit"]
      add_limit = CONFIG["cache.log_panel.size"]

      for entry in get_log_file_entries(set_runlevels, read_limit, add_limit):
        self.msg_log.append(entry)

    # crops events that are either too old, or more numerous than the caching size

    self._trim_events(self.msg_log)

    self.vals_lock.release()

  def set_duplicate_visability(self, is_visible):
    """
    Sets if duplicate log entries are collaped or expanded.

    Arguments:
      is_visible - if true all log entries are shown, otherwise they're
                   deduplicated
    """

    arm_config = conf.get_config("arm")
    arm_config.set("features.log.showDuplicateEntries", str(is_visible))

  def register_tor_event(self, event):
    """
    Translates a stem.response.event.Event instance into a LogEvent, and calls
    register_event().
    """

    msg, color = ' '.join(str(event).split(' ')[1:]), "white"

    if isinstance(event, events.CircuitEvent):
      color = "yellow"
    elif isinstance(event, events.BandwidthEvent):
      color = "cyan"
      msg = "READ: %i, WRITTEN: %i" % (event.read, event.written)
    elif isinstance(event, events.LogEvent):
      color = RUNLEVEL_EVENT_COLOR[event.runlevel]
      msg = event.message
    elif isinstance(event, events.NetworkStatusEvent):
      color = "blue"
    elif isinstance(event, events.NewConsensusEvent):
      color = "magenta"
    elif isinstance(event, events.GuardEvent):
      color = "yellow"
    elif not event.type in arm.arguments.TOR_EVENT_TYPES.values():
      color = "red"  # unknown event type

    self.register_event(LogEntry(event.arrived_at, event.type, msg, color))

  def register_event(self, event):
    """
    Notes event and redraws log. If paused it's held in a temporary buffer.

    Arguments:
      event - LogEntry for the event that occurred
    """

    if not event.type in self.logged_events:
      return

    # strips control characters to avoid screwing up the terminal

    event.msg = ui_tools.get_printable(event.msg)

    # note event in the log file if we're saving them

    if self.log_file:
      try:
        self.log_file.write(event.get_display_message(True) + "\n")
        self.log_file.flush()
      except IOError as exc:
        log.error("Unable to write to log file: %s" % exc.strerror)
        self.log_file = None

    self.vals_lock.acquire()
    self.msg_log.insert(0, event)
    self._trim_events(self.msg_log)

    # notifies the display that it has new content

    if not self.regex_filter or self.regex_filter.search(event.get_display_message()):
      self._cond.acquire()
      self._cond.notifyAll()
      self._cond.release()

    self.vals_lock.release()

  def set_logged_events(self, event_types):
    """
    Sets the event types recognized by the panel.

    Arguments:
      event_types - event types to be logged
    """

    if event_types == self.logged_events:
      return

    self.vals_lock.acquire()

    # configures the controller to listen for these tor events, and provides
    # back a subset without anything we're failing to listen to

    set_types = self.set_event_listening(event_types)
    self.logged_events = set_types
    self.redraw(True)
    self.vals_lock.release()

  def get_filter(self):
    """
    Provides our currently selected regex filter.
    """

    return self.filter_options[0] if self.regex_filter else None

  def set_filter(self, log_filter):
    """
    Filters log entries according to the given regular expression.

    Arguments:
      log_filter - regular expression used to determine which messages are
                  shown, None if no filter should be applied
    """

    if log_filter == self.regex_filter:
      return

    self.vals_lock.acquire()
    self.regex_filter = log_filter
    self.redraw(True)
    self.vals_lock.release()

  def make_filter_selection(self, selected_option):
    """
    Makes the given filter selection, applying it to the log and reorganizing
    our filter selection.

    Arguments:
      selected_option - regex filter we've already added, None if no filter
                       should be applied
    """

    if selected_option:
      try:
        self.set_filter(re.compile(selected_option))

        # move selection to top

        self.filter_options.remove(selected_option)
        self.filter_options.insert(0, selected_option)
      except re.error as exc:
        # shouldn't happen since we've already checked validity

        log.warn("Invalid regular expression ('%s': %s) - removing from listing" % (selected_option, exc))
        self.filter_options.remove(selected_option)
    else:
      self.set_filter(None)

  def show_filter_prompt(self):
    """
    Prompts the user to add a new regex filter.
    """

    regex_input = arm.popups.input_prompt("Regular expression: ")

    if regex_input:
      try:
        self.set_filter(re.compile(regex_input))

        if regex_input in self.filter_options:
          self.filter_options.remove(regex_input)

        self.filter_options.insert(0, regex_input)
      except re.error as exc:
        arm.popups.show_msg("Unable to compile expression: %s" % exc, 2)

  def show_event_selection_prompt(self):
    """
    Prompts the user to select the events being listened for.
    """

    # allow user to enter new types of events to log - unchanged if left blank

    popup, width, height = arm.popups.init(11, 80)

    if popup:
      try:
        # displays the available flags

        popup.win.box()
        popup.addstr(0, 0, "Event Types:", curses.A_STANDOUT)
        event_lines = CONFIG['msg.misc.event_types'].split("\n")

        for i in range(len(event_lines)):
          popup.addstr(i + 1, 1, event_lines[i][6:])

        popup.win.refresh()

        user_input = arm.popups.input_prompt("Events to log: ")

        if user_input:
          user_input = user_input.replace(' ', '')  # strips spaces

          try:
            self.set_logged_events(arm.arguments.expand_events(user_input))
          except ValueError as exc:
            arm.popups.show_msg("Invalid flags: %s" % str(exc), 2)
      finally:
        arm.popups.finalize()

  def show_snapshot_prompt(self):
    """
    Lets user enter a path to take a snapshot, canceling if left blank.
    """

    path_input = arm.popups.input_prompt("Path to save log snapshot: ")

    if path_input:
      try:
        self.save_snapshot(path_input)
        arm.popups.show_msg("Saved: %s" % path_input, 2)
      except IOError as exc:
        arm.popups.show_msg("Unable to save snapshot: %s" % exc.strerror, 2)

  def clear(self):
    """
    Clears the contents of the event log.
    """

    self.vals_lock.acquire()
    self.msg_log = []
    self.redraw(True)
    self.vals_lock.release()

  def save_snapshot(self, path):
    """
    Saves the log events currently being displayed to the given path. This
    takes filers into account. This overwrites the file if it already exists,
    and raises an IOError if there's a problem.

    Arguments:
      path - path where to save the log snapshot
    """

    path = os.path.abspath(os.path.expanduser(path))

    # make dir if the path doesn't already exist

    base_dir = os.path.dirname(path)

    try:
      if not os.path.exists(base_dir):
        os.makedirs(base_dir)
    except OSError as exc:
      raise IOError("unable to make directory '%s'" % base_dir)

    snapshot_file = open(path, "w")
    self.vals_lock.acquire()

    try:
      for entry in self.msg_log:
        is_visible = not self.regex_filter or self.regex_filter.search(entry.get_display_message())

        if is_visible:
          snapshot_file.write(entry.get_display_message(True) + "\n")

      self.vals_lock.release()
    except Exception as exc:
      self.vals_lock.release()
      raise exc

  def handle_key(self, key):
    is_keystroke_consumed = True

    if ui_tools.is_scroll_key(key):
      page_height = self.get_preferred_size()[0] - 1
      new_scroll = ui_tools.get_scroll_position(key, self.scroll, page_height, self.last_content_height)

      if self.scroll != new_scroll:
        self.vals_lock.acquire()
        self.scroll = new_scroll
        self.redraw(True)
        self.vals_lock.release()
    elif key in (ord('u'), ord('U')):
      self.vals_lock.acquire()
      self.set_duplicate_visability(not CONFIG["features.log.showDuplicateEntries"])
      self.redraw(True)
      self.vals_lock.release()
    elif key == ord('c') or key == ord('C'):
      msg = "This will clear the log. Are you sure (c again to confirm)?"
      key_press = arm.popups.show_msg(msg, attr = curses.A_BOLD)

      if key_press in (ord('c'), ord('C')):
        self.clear()
    elif key == ord('f') or key == ord('F'):
      # Provides menu to pick regular expression filters or adding new ones:
      # for syntax see: http://docs.python.org/library/re.html#regular-expression-syntax

      options = ["None"] + self.filter_options + ["New..."]
      old_selection = 0 if not self.regex_filter else 1

      # does all activity under a curses lock to prevent redraws when adding
      # new filters

      panel.CURSES_LOCK.acquire()

      try:
        selection = arm.popups.show_menu("Log Filter:", options, old_selection)

        # applies new setting

        if selection == 0:
          self.set_filter(None)
        elif selection == len(options) - 1:
          # selected 'New...' option - prompt user to input regular expression
          self.show_filter_prompt()
        elif selection != -1:
          self.make_filter_selection(self.filter_options[selection - 1])
      finally:
        panel.CURSES_LOCK.release()

      if len(self.filter_options) > MAX_REGEX_FILTERS:
        del self.filter_options[MAX_REGEX_FILTERS:]
    elif key == ord('e') or key == ord('E'):
      self.show_event_selection_prompt()
    elif key == ord('a') or key == ord('A'):
      self.show_snapshot_prompt()
    else:
      is_keystroke_consumed = False

    return is_keystroke_consumed

  def get_help(self):
    options = []
    options.append(("up arrow", "scroll log up a line", None))
    options.append(("down arrow", "scroll log down a line", None))
    options.append(("a", "save snapshot of the log", None))
    options.append(("e", "change logged events", None))
    options.append(("f", "log regex filter", "enabled" if self.regex_filter else "disabled"))
    options.append(("u", "duplicate log entries", "visible" if CONFIG["features.log.showDuplicateEntries"] else "hidden"))
    options.append(("c", "clear event log", None))
    return options

  def draw(self, width, height):
    """
    Redraws message log. Entries stretch to use available space and may
    contain up to two lines. Starts with newest entries.
    """

    current_log = self.get_attr("msg_log")

    self.vals_lock.acquire()
    self._last_logged_events, self._last_update = list(current_log), time.time()

    # draws the top label

    if self.is_title_visible():
      self.addstr(0, 0, self._get_title(width), curses.A_STANDOUT)

    # restricts scroll location to valid bounds

    self.scroll = max(0, min(self.scroll, self.last_content_height - height + 1))

    # draws left-hand scroll bar if content's longer than the height

    msg_indent, divider_indent = 1, 0  # offsets for scroll bar
    is_scroll_bar_visible = self.last_content_height > height - 1

    if is_scroll_bar_visible:
      msg_indent, divider_indent = 3, 2
      self.add_scroll_bar(self.scroll, self.scroll + height - 1, self.last_content_height, 1)

    # draws log entries

    line_count = 1 - self.scroll
    seen_first_date_divider = False
    divider_attr, duplicate_attr = curses.A_BOLD | ui_tools.get_color("yellow"), curses.A_BOLD | ui_tools.get_color("green")

    is_dates_shown = self.regex_filter is None and CONFIG["features.log.showDateDividers"]
    event_log = get_daybreaks(current_log, self.is_paused()) if is_dates_shown else list(current_log)

    if not CONFIG["features.log.showDuplicateEntries"]:
      deduplicated_log = get_duplicates(event_log)

      if deduplicated_log is None:
        log.warn("Deduplication took too long. Its current implementation has difficulty handling large logs so disabling it to keep the interface responsive.")
        self.set_duplicate_visability(True)
        deduplicated_log = [(entry, 0) for entry in event_log]
    else:
      deduplicated_log = [(entry, 0) for entry in event_log]

    # determines if we have the minimum width to show date dividers

    show_daybreaks = width - divider_indent >= 3

    while deduplicated_log:
      entry, duplicate_count = deduplicated_log.pop(0)

      if self.regex_filter and not self.regex_filter.search(entry.get_display_message()):
        continue  # filter doesn't match log message - skip

      # checks if we should be showing a divider with the date

      if entry.type == DAYBREAK_EVENT:
        # bottom of the divider

        if seen_first_date_divider:
          if line_count >= 1 and line_count < height and show_daybreaks:
            self.addch(line_count, divider_indent, curses.ACS_LLCORNER, divider_attr)
            self.hline(line_count, divider_indent + 1, width - divider_indent - 2, divider_attr)
            self.addch(line_count, width - 1, curses.ACS_LRCORNER, divider_attr)

          line_count += 1

        # top of the divider

        if line_count >= 1 and line_count < height and show_daybreaks:
          time_label = time.strftime(" %B %d, %Y ", time.localtime(entry.timestamp))
          self.addch(line_count, divider_indent, curses.ACS_ULCORNER, divider_attr)
          self.addch(line_count, divider_indent + 1, curses.ACS_HLINE, divider_attr)
          self.addstr(line_count, divider_indent + 2, time_label, curses.A_BOLD | divider_attr)

          line_length = width - divider_indent - len(time_label) - 3
          self.hline(line_count, divider_indent + len(time_label) + 2, line_length, divider_attr)
          self.addch(line_count, divider_indent + len(time_label) + 2 + line_length, curses.ACS_URCORNER, divider_attr)

        seen_first_date_divider = True
        line_count += 1
      else:
        # entry contents to be displayed, tuples of the form:
        # (msg, formatting, includeLinebreak)

        display_queue = []

        msg_comp = entry.get_display_message().split("\n")

        for i in range(len(msg_comp)):
          font = curses.A_BOLD if "ERR" in entry.type else curses.A_NORMAL  # emphasizes ERR messages
          display_queue.append((msg_comp[i].strip(), font | ui_tools.get_color(entry.color), i != len(msg_comp) - 1))

        if duplicate_count:
          plural_label = "s" if duplicate_count > 1 else ""
          duplicate_msg = DUPLICATE_MSG % (duplicate_count, plural_label)
          display_queue.append((duplicate_msg, duplicate_attr, False))

        cursor_location, line_offset = msg_indent, 0
        max_entries_per_line = CONFIG["features.log.max_lines_per_entry"]

        while display_queue:
          msg, format, include_break = display_queue.pop(0)
          draw_line = line_count + line_offset

          if line_offset == max_entries_per_line:
            break

          max_msg_size = width - cursor_location - 1

          if len(msg) > max_msg_size:
            # message is too long - break it up
            if line_offset == max_entries_per_line - 1:
              msg = ui_tools.crop_str(msg, max_msg_size)
            else:
              msg, remainder = ui_tools.crop_str(msg, max_msg_size, 4, 4, ui_tools.Ending.HYPHEN, True)
              display_queue.insert(0, (remainder.strip(), format, include_break))

            include_break = True

          if draw_line < height and draw_line >= 1:
            if seen_first_date_divider and width - divider_indent >= 3 and show_daybreaks:
              self.addch(draw_line, divider_indent, curses.ACS_VLINE, divider_attr)
              self.addch(draw_line, width - 1, curses.ACS_VLINE, divider_attr)

            self.addstr(draw_line, cursor_location, msg, format)

          cursor_location += len(msg)

          if include_break or not display_queue:
            line_offset += 1
            cursor_location = msg_indent + ENTRY_INDENT

        line_count += line_offset

      # if this is the last line and there's room, then draw the bottom of the divider

      if not deduplicated_log and seen_first_date_divider:
        if line_count < height and show_daybreaks:
          self.addch(line_count, divider_indent, curses.ACS_LLCORNER, divider_attr)
          self.hline(line_count, divider_indent + 1, width - divider_indent - 2, divider_attr)
          self.addch(line_count, width - 1, curses.ACS_LRCORNER, divider_attr)

        line_count += 1

    # redraw the display if...
    # - last_content_height was off by too much
    # - we're off the bottom of the page

    new_content_height = line_count + self.scroll - 1
    content_height_delta = abs(self.last_content_height - new_content_height)
    force_redraw, force_redraw_reason = True, ""

    if content_height_delta >= CONTENT_HEIGHT_REDRAW_THRESHOLD:
      force_redraw_reason = "estimate was off by %i" % content_height_delta
    elif new_content_height > height and self.scroll + height - 1 > new_content_height:
      force_redraw_reason = "scrolled off the bottom of the page"
    elif not is_scroll_bar_visible and new_content_height > height - 1:
      force_redraw_reason = "scroll bar wasn't previously visible"
    elif is_scroll_bar_visible and new_content_height <= height - 1:
      force_redraw_reason = "scroll bar shouldn't be visible"
    else:
      force_redraw = False

    self.last_content_height = new_content_height

    if force_redraw:
      log.debug("redrawing the log panel with the corrected content height (%s)" % force_redraw_reason)
      self.redraw(True)

    self.vals_lock.release()

  def redraw(self, force_redraw=False, block=False):
    # determines if the content needs to be redrawn or not
    panel.Panel.redraw(self, force_redraw, block)

  def run(self):
    """
    Redraws the display, coalescing updates if events are rapidly logged (for
    instance running at the DEBUG runlevel) while also being immediately
    responsive if additions are less frequent.
    """

    last_day = days_since()  # used to determine if the date has changed

    while not self._halt:
      current_day = days_since()
      time_since_reset = time.time() - self._last_update
      max_log_update_rate = CONFIG["features.log.maxRefreshRate"] / 1000.0

      sleep_time = 0

      if (self.msg_log == self._last_logged_events and last_day == current_day) or self.is_paused():
        sleep_time = 5
      elif time_since_reset < max_log_update_rate:
        sleep_time = max(0.05, max_log_update_rate - time_since_reset)

      if sleep_time:
        self._cond.acquire()

        if not self._halt:
          self._cond.wait(sleep_time)

        self._cond.release()
      else:
        last_day = current_day
        self.redraw(True)

        # makes sure that we register this as an update, otherwise lacking the
        # curses lock can cause a busy wait here

        self._last_update = time.time()

  def stop(self):
    """
    Halts further resolutions and terminates the thread.
    """

    self._cond.acquire()
    self._halt = True
    self._cond.notifyAll()
    self._cond.release()

  def set_event_listening(self, events):
    """
    Configures the events Tor listens for, filtering non-tor events from what we
    request from the controller. This returns a sorted list of the events we
    successfully set.

    Arguments:
      events - event types to attempt to set
    """

    events = set(events)  # drops duplicates

    # accounts for runlevel naming difference

    if "ERROR" in events:
      events.add("ERR")
      events.remove("ERROR")

    if "WARNING" in events:
      events.add("WARN")
      events.remove("WARNING")

    tor_events = events.intersection(set(arm.arguments.TOR_EVENT_TYPES.values()))
    arm_events = events.intersection(set(["ARM_%s" % runlevel for runlevel in log.Runlevel.keys()]))

    # adds events unrecognized by arm if we're listening to the 'UNKNOWN' type

    if "UNKNOWN" in events:
      tor_events.update(set(arm.arguments.missing_event_types()))

    controller = tor_controller()
    controller.remove_event_listener(self.register_tor_event)

    for event_type in list(tor_events):
      try:
        controller.add_event_listener(self.register_tor_event, event_type)
      except stem.ProtocolError:
        tor_events.remove(event_type)

    # provides back the input set minus events we failed to set

    return sorted(tor_events.union(arm_events))

  def _reset_listener(self, controller, event_type, _):
    # if we're attaching to a new tor instance then clears the log and
    # prepopulates it with the content belonging to this instance

    if event_type == State.INIT:
      self.reprepopulate_events()
      self.redraw(True)
    elif event_type == State.CLOSED:
      log.notice("Tor control port closed")

  def _get_title(self, width):
    """
    Provides the label used for the panel, looking like:
      Events (ARM NOTICE - ERR, BW - filter: prepopulate):

    This truncates the attributes (with an ellipse) if too long, and condenses
    runlevel ranges if there's three or more in a row (for instance ARM_INFO,
    ARM_NOTICE, and ARM_WARN becomes "ARM_INFO - WARN").

    Arguments:
      width - width constraint the label needs to fix in
    """

    # usually the attributes used to make the label are decently static, so
    # provide cached results if they're unchanged

    self.vals_lock.acquire()
    current_pattern = self.regex_filter.pattern if self.regex_filter else None
    is_unchanged = self._title_args[0] == self.logged_events
    is_unchanged &= self._title_args[1] == current_pattern
    is_unchanged &= self._title_args[2] == width

    if is_unchanged:
      self.vals_lock.release()
      return self._title_cache

    events_list = list(self.logged_events)

    if not events_list:
      if not current_pattern:
        panel_label = "Events:"
      else:
        label_pattern = ui_tools.crop_str(current_pattern, width - 18)
        panel_label = "Events (filter: %s):" % label_pattern
    else:
      # does the following with all runlevel types (tor, arm, and stem):
      # - pulls to the start of the list
      # - condenses range if there's three or more in a row (ex. "ARM_INFO - WARN")
      # - condense further if there's identical runlevel ranges for multiple
      #   types (ex. "NOTICE - ERR, ARM_NOTICE - ERR" becomes "TOR/ARM NOTICE - ERR")

      tmp_runlevels = []  # runlevels pulled from the list (just the runlevel part)
      runlevel_ranges = []  # tuple of type, start_level, end_level for ranges to be consensed

      # reverses runlevels and types so they're appended in the right order

      reversed_runlevels = list(log.Runlevel)
      reversed_runlevels.reverse()

      for prefix in ("ARM_", ""):
        # blank ending runlevel forces the break condition to be reached at the end
        for runlevel in reversed_runlevels + [""]:
          event_type = prefix + runlevel
          if runlevel and event_type in events_list:
            # runlevel event found, move to the tmp list
            events_list.remove(event_type)
            tmp_runlevels.append(runlevel)
          elif tmp_runlevels:
            # adds all tmp list entries to the start of events_list
            if len(tmp_runlevels) >= 3:
              # save condense sequential runlevels to be added later
              runlevel_ranges.append((prefix, tmp_runlevels[-1], tmp_runlevels[0]))
            else:
              # adds runlevels individaully
              for tmp_runlevel in tmp_runlevels:
                events_list.insert(0, prefix + tmp_runlevel)

            tmp_runlevels = []

      # adds runlevel ranges, condensing if there's identical ranges

      for i in range(len(runlevel_ranges)):
        if runlevel_ranges[i]:
          prefix, start_level, end_level = runlevel_ranges[i]

          # check for matching ranges

          matches = []

          for j in range(i + 1, len(runlevel_ranges)):
            if runlevel_ranges[j] and runlevel_ranges[j][1] == start_level and runlevel_ranges[j][2] == end_level:
              matches.append(runlevel_ranges[j])
              runlevel_ranges[j] = None

          if matches:
            # strips underscores and replaces empty entries with "TOR"

            prefixes = [entry[0] for entry in matches] + [prefix]

            for k in range(len(prefixes)):
              if prefixes[k] == "":
                prefixes[k] = "TOR"
              else:
                prefixes[k] = prefixes[k].replace("_", "")

            events_list.insert(0, "%s %s - %s" % ("/".join(prefixes), start_level, end_level))
          else:
            events_list.insert(0, "%s%s - %s" % (prefix, start_level, end_level))

      # truncates to use an ellipsis if too long, for instance:

      attr_label = ", ".join(events_list)

      if current_pattern:
        attr_label += " - filter: %s" % current_pattern

      attr_label = ui_tools.crop_str(attr_label, width - 10, 1)

      if attr_label:
        attr_label = " (%s)" % attr_label

      panel_label = "Events%s:" % attr_label

    # cache results and return

    self._title_cache = panel_label
    self._title_args = (list(self.logged_events), current_pattern, width)
    self.vals_lock.release()

    return panel_label

  def _trim_events(self, event_listing):
    """
    Crops events that have either:
    - grown beyond the cache limit
    - outlived the configured log duration

    Argument:
      event_listing - listing of log entries
    """

    cache_size = CONFIG["cache.log_panel.size"]

    if len(event_listing) > cache_size:
      del event_listing[cache_size:]

    log_ttl = CONFIG["features.log.entryDuration"]

    if log_ttl > 0:
      current_day = days_since()

      breakpoint = None  # index at which to crop from

      for i in range(len(event_listing) - 1, -1, -1):
        days_since_event = current_day - days_since(event_listing[i].timestamp)

        if days_since_event > log_ttl:
          breakpoint = i  # older than the ttl
        else:
          break

      # removes entries older than the ttl

      if breakpoint is not None:
        del event_listing[breakpoint:]
