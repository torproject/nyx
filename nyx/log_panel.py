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
import stem.response.events

from stem.control import State
from stem.util import conf, log, str_tools

import nyx.arguments
import nyx.popups

from nyx import __version__
from nyx.util import panel, tor_controller, ui_tools
from nyx.util.log import LogGroup, LogEntry, read_tor_log

DAYBREAK_EVENT = 'DAYBREAK'  # special event for marking when the date changes
TIMEZONE_OFFSET = time.altzone if time.localtime()[8] else time.timezone

ENTRY_INDENT = 2  # spaces an entry's message is indented after the first line


def conf_handler(key, value):
  if key == 'features.log.max_lines_per_entry':
    return max(1, value)
  elif key == 'features.log.prepopulateReadLimit':
    return max(0, value)
  elif key == 'features.log.maxRefreshRate':
    return max(10, value)
  elif key == 'cache.log_panel.size':
    return max(1000, value)


CONFIG = conf.config_dict('nyx', {
  'features.log_file': '',
  'features.log.showDateDividers': True,
  'features.log.showDuplicateEntries': False,
  'features.log.max_lines_per_entry': 6,
  'features.log.prepopulate': True,
  'features.log.prepopulateReadLimit': 5000,
  'features.log.maxRefreshRate': 300,
  'features.log.regex': [],
  'cache.log_panel.size': 1000,
  'msg.misc.event_types': '',
  'tor.chroot': '',
  'attr.log_color': {},
}, conf_handler)

DUPLICATE_MSG = ' [%i duplicate%s hidden]'

# The height of the drawn content is estimated based on the last time we redrew
# the panel. It's chiefly used for scrolling and the bar indicating its
# position. Letting the estimate be too inaccurate results in a display bug, so
# redraws the display if it's off by this threshold.

CONTENT_HEIGHT_REDRAW_THRESHOLD = 3

# cached values and the arguments that generated it for the get_daybreaks and
# get_duplicates functions

CACHED_DAYBREAKS_ARGUMENTS = (None, None)  # events, current day
CACHED_DAYBREAKS_RESULT = None

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


def log_file_path():
  for log_entry in tor_controller().get_conf('Log', [], True):
    entry_comp = log_entry.split()  # looking for an entry like: notice file /var/log/tor/notices.log

    if entry_comp[1] == 'file':
      return CONFIG['tor.chroot'] + entry_comp[2]


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
      new_listing.append(LogEntry(marker_timestamp, DAYBREAK_EVENT, ''))

    new_listing.append(entry)
    last_day = event_day

  CACHED_DAYBREAKS_ARGUMENTS = (list(events), current_day)
  CACHED_DAYBREAKS_RESULT = list(new_listing)

  return new_listing


class LogPanel(panel.Panel, threading.Thread, logging.Handler):
  """
  Listens for and displays tor, nyx, and stem events. This can prepopulate
  from tor's log file if it exists.
  """

  def __init__(self, stdscr, logged_events):
    panel.Panel.__init__(self, stdscr, 'log', 0)
    logging.Handler.__init__(self, level = log.logging_level(log.DEBUG))

    self.setFormatter(logging.Formatter(
      fmt = '%(asctime)s [%(levelname)s] %(message)s',
      datefmt = '%m/%d/%Y %H:%M:%S'),
    )

    threading.Thread.__init__(self)
    self.setDaemon(True)

    # regex filters the user has defined

    self.filter_options = []

    for filter in CONFIG['features.log.regex']:
      # checks if we can't have more filters

      if len(self.filter_options) >= MAX_REGEX_FILTERS:
        break

      try:
        re.compile(filter)
        self.filter_options.append(filter)
      except re.error as exc:
        log.notice('Invalid regular expression pattern (%s): %s' % (exc, filter))

    self.logged_events = []  # needs to be set before we receive any events

    # restricts the input to the set of events we can listen to, and
    # configures the controller to liten to them

    self.logged_events = self.set_event_listening(logged_events)

    self.regex_filter = None             # filter for presented log events (no filtering if None)
    self.last_content_height = 0         # height of the rendered content when last drawn
    self.log_file = None                 # file log messages are saved to (skipped if None)
    self.scroll = 0

    self.set_pause_attr('_msg_log')
    self._msg_log = LogGroup(CONFIG['cache.log_panel.size'])

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

    self.last_content_height = len(self._msg_log)

    # adds listeners for tor and stem events

    controller = tor_controller()
    controller.add_status_listener(self._reset_listener)

    # opens log file if we'll be saving entries

    if CONFIG['features.log_file']:
      log_path = CONFIG['features.log_file']

      try:
        # make dir if the path doesn't already exist

        base_dir = os.path.dirname(log_path)

        if not os.path.exists(base_dir):
          os.makedirs(base_dir)

        self.log_file = open(log_path, 'a')
        log.notice('nyx %s opening log file (%s)' % (__version__, log_path))
      except IOError as exc:
        log.error('Unable to write to log file: %s' % exc.strerror)
        self.log_file = None
      except OSError as exc:
        log.error('Unable to write to log file: %s' % exc)
        self.log_file = None

    stem_logger = log.get_logger()
    stem_logger.addHandler(self)

  def emit(self, record):
    if record.levelname == 'WARNING':
      record.levelname = 'WARN'

    self.register_event(LogEntry(int(record.created), 'NYX_%s' % record.levelname, record.msg))

  def reprepopulate_events(self):
    """
    Clears the event log and repopulates it from the nyx and tor backlogs.
    """

    with self.vals_lock:
      # clears the event log

      self._msg_log = LogGroup(CONFIG['cache.log_panel.size'])

      # fetches past tor events from log file, if available

      if CONFIG['features.log.prepopulate']:
        set_runlevels = list(set.intersection(set(self.logged_events), set(list(log.Runlevel))))
        read_limit = CONFIG['features.log.prepopulateReadLimit']

        logging_location = log_file_path()

        if logging_location:
          try:
            for entry in reversed(list(read_tor_log(logging_location, read_limit))):
              if entry.type in set_runlevels:
                self._msg_log.add(entry.timestamp, entry.type, entry.message)
          except IOError as exc:
            log.info('Unable to read log located at %s: %s' % (logging_location, exc))
          except ValueError as exc:
            log.info(str(exc))

  def set_duplicate_visability(self, is_visible):
    """
    Sets if duplicate log entries are collaped or expanded.

    Arguments:
      is_visible - if true all log entries are shown, otherwise they're
                   deduplicated
    """

    nyx_config = conf.get_config('nyx')
    nyx_config.set('features.log.showDuplicateEntries', str(is_visible))

  def register_tor_event(self, event):
    """
    Translates a stem.response.event.Event instance into a LogEvent, and calls
    register_event().
    """

    msg = ' '.join(str(event).split(' ')[1:])

    if isinstance(event, stem.response.events.BandwidthEvent):
      msg = 'READ: %i, WRITTEN: %i' % (event.read, event.written)
    elif isinstance(event, stem.response.events.LogEvent):
      msg = event.message

    self.register_event(LogEntry(event.arrived_at, event.type, msg))

  def register_event(self, event):
    """
    Notes event and redraws log. If paused it's held in a temporary buffer.

    Arguments:
      event - LogEntry for the event that occurred
    """

    if event.type not in self.logged_events:
      return

    # note event in the log file if we're saving them

    if self.log_file:
      try:
        self.log_file.write(event.display_message + '\n')
        self.log_file.flush()
      except IOError as exc:
        log.error('Unable to write to log file: %s' % exc.strerror)
        self.log_file = None

    with self.vals_lock:
      self._msg_log.add(event.timestamp, event.type, event.message)

      # notifies the display that it has new content

      if not self.regex_filter or self.regex_filter.search(event.display_message):
        with self._cond:
          self._cond.notifyAll()

  def set_logged_events(self, event_types):
    """
    Sets the event types recognized by the panel.

    Arguments:
      event_types - event types to be logged
    """

    if event_types == self.logged_events:
      return

    with self.vals_lock:
      # configures the controller to listen for these tor events, and provides
      # back a subset without anything we're failing to listen to

      set_types = self.set_event_listening(event_types)
      self.logged_events = set_types
      self.redraw(True)

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

    with self.vals_lock:
      self.regex_filter = log_filter
      self.redraw(True)

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

    regex_input = nyx.popups.input_prompt('Regular expression: ')

    if regex_input:
      try:
        self.set_filter(re.compile(regex_input))

        if regex_input in self.filter_options:
          self.filter_options.remove(regex_input)

        self.filter_options.insert(0, regex_input)
      except re.error as exc:
        nyx.popups.show_msg('Unable to compile expression: %s' % exc, 2)

  def show_event_selection_prompt(self):
    """
    Prompts the user to select the events being listened for.
    """

    # allow user to enter new types of events to log - unchanged if left blank

    popup, width, height = nyx.popups.init(11, 80)

    if popup:
      try:
        # displays the available flags

        popup.win.box()
        popup.addstr(0, 0, 'Event Types:', curses.A_STANDOUT)
        event_lines = CONFIG['msg.misc.event_types'].split('\n')

        for i in range(len(event_lines)):
          popup.addstr(i + 1, 1, event_lines[i][6:])

        popup.win.refresh()

        user_input = nyx.popups.input_prompt('Events to log: ')

        if user_input:
          user_input = user_input.replace(' ', '')  # strips spaces

          try:
            self.set_logged_events(nyx.arguments.expand_events(user_input))
          except ValueError as exc:
            nyx.popups.show_msg('Invalid flags: %s' % str(exc), 2)
      finally:
        nyx.popups.finalize()

  def show_snapshot_prompt(self):
    """
    Lets user enter a path to take a snapshot, canceling if left blank.
    """

    path_input = nyx.popups.input_prompt('Path to save log snapshot: ')

    if path_input:
      try:
        self.save_snapshot(path_input)
        nyx.popups.show_msg('Saved: %s' % path_input, 2)
      except IOError as exc:
        nyx.popups.show_msg('Unable to save snapshot: %s' % exc.strerror, 2)

  def clear(self):
    """
    Clears the contents of the event log.
    """

    with self.vals_lock:
      self._msg_log = LogGroup(CONFIG['cache.log_panel.size'])
      self.redraw(True)

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

    snapshot_file = open(path, 'w')

    with self.vals_lock:
      try:
        for entry in reversed(self._msg_log):
          is_visible = not self.regex_filter or self.regex_filter.search(entry.display_message)

          if is_visible:
            snapshot_file.write(entry.display_message + '\n')
      except Exception as exc:
        raise exc

  def handle_key(self, key):
    if key.is_scroll():
      page_height = self.get_preferred_size()[0] - 1
      new_scroll = ui_tools.get_scroll_position(key, self.scroll, page_height, self.last_content_height)

      if self.scroll != new_scroll:
        with self.vals_lock:
          self.scroll = new_scroll
          self.redraw(True)
    elif key.match('u'):
      with self.vals_lock:
        self.set_duplicate_visability(not CONFIG['features.log.showDuplicateEntries'])
        self.redraw(True)
    elif key.match('c'):
      msg = 'This will clear the log. Are you sure (c again to confirm)?'
      key_press = nyx.popups.show_msg(msg, attr = curses.A_BOLD)

      if key_press.match('c'):
        self.clear()
    elif key.match('f'):
      # Provides menu to pick regular expression filters or adding new ones:
      # for syntax see: http://docs.python.org/library/re.html#regular-expression-syntax

      options = ['None'] + self.filter_options + ['New...']
      old_selection = 0 if not self.regex_filter else 1

      # does all activity under a curses lock to prevent redraws when adding
      # new filters

      panel.CURSES_LOCK.acquire()

      try:
        selection = nyx.popups.show_menu('Log Filter:', options, old_selection)

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
    elif key.match('e'):
      self.show_event_selection_prompt()
    elif key.match('a'):
      self.show_snapshot_prompt()
    else:
      return False

    return True

  def get_help(self):
    return [
      ('up arrow', 'scroll log up a line', None),
      ('down arrow', 'scroll log down a line', None),
      ('a', 'save snapshot of the log', None),
      ('e', 'change logged events', None),
      ('f', 'log regex filter', 'enabled' if self.regex_filter else 'disabled'),
      ('u', 'duplicate log entries', 'visible' if CONFIG['features.log.showDuplicateEntries'] else 'hidden'),
      ('c', 'clear event log', None),
    ]

  def draw(self, width, height):
    """
    Redraws message log. Entries stretch to use available space and may
    contain up to two lines. Starts with newest entries.
    """

    event_log = self.get_attr('_msg_log')

    with self.vals_lock:
      self._last_logged_events, self._last_update = event_log, time.time()
      event_log = list(event_log)

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
      divider_attr, duplicate_attr = (curses.A_BOLD, 'yellow'), (curses.A_BOLD, 'green')

      # TODO: fix daybreak handling
      # is_dates_shown = self.regex_filter is None and CONFIG['features.log.showDateDividers']
      # event_log = get_daybreaks(current_log, self.is_paused()) if is_dates_shown else current_log

      if not CONFIG['features.log.showDuplicateEntries']:
        deduplicated_log = []

        for entry in event_log:
          if not entry.is_duplicate:
            duplicate_count = len(entry.duplicates) if entry.duplicates else 0
            deduplicated_log.append((entry, duplicate_count))
      else:
        deduplicated_log = [(entry, 0) for entry in event_log]

      # determines if we have the minimum width to show date dividers

      show_daybreaks = width - divider_indent >= 3

      while deduplicated_log:
        entry, duplicate_count = deduplicated_log.pop(0)

        if self.regex_filter and not self.regex_filter.search(entry.display_message):
          continue  # filter doesn't match log message - skip

        # checks if we should be showing a divider with the date

        if entry.type == DAYBREAK_EVENT:
          # bottom of the divider

          if seen_first_date_divider:
            if line_count >= 1 and line_count < height and show_daybreaks:
              self.addch(line_count, divider_indent, curses.ACS_LLCORNER, *divider_attr)
              self.hline(line_count, divider_indent + 1, width - divider_indent - 2, *divider_attr)
              self.addch(line_count, width - 1, curses.ACS_LRCORNER, *divider_attr)

            line_count += 1

          # top of the divider

          if line_count >= 1 and line_count < height and show_daybreaks:
            time_label = time.strftime(' %B %d, %Y ', time.localtime(entry.timestamp))
            self.addch(line_count, divider_indent, curses.ACS_ULCORNER, *divider_attr)
            self.addch(line_count, divider_indent + 1, curses.ACS_HLINE, *divider_attr)
            self.addstr(line_count, divider_indent + 2, time_label, curses.A_BOLD, *divider_attr)

            line_length = width - divider_indent - len(time_label) - 3
            self.hline(line_count, divider_indent + len(time_label) + 2, line_length, *divider_attr)
            self.addch(line_count, divider_indent + len(time_label) + 2 + line_length, curses.ACS_URCORNER, *divider_attr)

          seen_first_date_divider = True
          line_count += 1
        else:
          # entry contents to be displayed, tuples of the form:
          # (msg, formatting, includeLinebreak)

          display_queue = []

          msg_comp = entry.display_message.split('\n')

          for i in range(len(msg_comp)):
            font = curses.A_BOLD if 'ERR' in entry.type else curses.A_NORMAL  # emphasizes ERR messages
            display_queue.append((msg_comp[i].strip(), (font, CONFIG['attr.log_color'].get(entry.type, 'white')), i != len(msg_comp) - 1))

          if duplicate_count:
            plural_label = 's' if duplicate_count > 1 else ''
            duplicate_msg = DUPLICATE_MSG % (duplicate_count, plural_label)
            display_queue.append((duplicate_msg, duplicate_attr, False))

          cursor_location, line_offset = msg_indent, 0
          max_entries_per_line = CONFIG['features.log.max_lines_per_entry']

          while display_queue:
            msg, format, include_break = display_queue.pop(0)
            draw_line = line_count + line_offset

            if line_offset == max_entries_per_line:
              break

            max_msg_size = width - cursor_location - 1

            if len(msg) > max_msg_size:
              # message is too long - break it up
              if line_offset == max_entries_per_line - 1:
                msg = str_tools.crop(msg, max_msg_size)
              else:
                msg, remainder = str_tools.crop(msg, max_msg_size, 4, 4, str_tools.Ending.HYPHEN, True)
                display_queue.insert(0, (remainder.strip(), format, include_break))

              include_break = True

            if draw_line < height and draw_line >= 1:
              if seen_first_date_divider and width - divider_indent >= 3 and show_daybreaks:
                self.addch(draw_line, divider_indent, curses.ACS_VLINE, *divider_attr)
                self.addch(draw_line, width - 1, curses.ACS_VLINE, *divider_attr)

              self.addstr(draw_line, cursor_location, msg, *format)

            cursor_location += len(msg)

            if include_break or not display_queue:
              line_offset += 1
              cursor_location = msg_indent + ENTRY_INDENT

          line_count += line_offset

        # if this is the last line and there's room, then draw the bottom of the divider

        if not deduplicated_log and seen_first_date_divider:
          if line_count < height and show_daybreaks:
            self.addch(line_count, divider_indent, curses.ACS_LLCORNER, *divider_attr)
            self.hline(line_count, divider_indent + 1, width - divider_indent - 2, *divider_attr)
            self.addch(line_count, width - 1, curses.ACS_LRCORNER, *divider_attr)

          line_count += 1

      # redraw the display if...
      # - last_content_height was off by too much
      # - we're off the bottom of the page

      new_content_height = line_count + self.scroll - 1
      content_height_delta = abs(self.last_content_height - new_content_height)
      force_redraw, force_redraw_reason = True, ''

      if content_height_delta >= CONTENT_HEIGHT_REDRAW_THRESHOLD:
        force_redraw_reason = 'estimate was off by %i' % content_height_delta
      elif new_content_height > height and self.scroll + height - 1 > new_content_height:
        force_redraw_reason = 'scrolled off the bottom of the page'
      elif not is_scroll_bar_visible and new_content_height > height - 1:
        force_redraw_reason = "scroll bar wasn't previously visible"
      elif is_scroll_bar_visible and new_content_height <= height - 1:
        force_redraw_reason = "scroll bar shouldn't be visible"
      else:
        force_redraw = False

      self.last_content_height = new_content_height

      if force_redraw:
        log.debug('redrawing the log panel with the corrected content height (%s)' % force_redraw_reason)
        self.redraw(True)

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
      max_log_update_rate = CONFIG['features.log.maxRefreshRate'] / 1000.0

      sleep_time = 0

      if (self._msg_log == self._last_logged_events and last_day == current_day) or self.is_paused():
        sleep_time = 5
      elif time_since_reset < max_log_update_rate:
        sleep_time = max(0.05, max_log_update_rate - time_since_reset)

      if sleep_time:
        with self._cond:
          if not self._halt:
            self._cond.wait(sleep_time)
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

    with self._cond:
      self._halt = True
      self._cond.notifyAll()

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

    if 'ERROR' in events:
      events.add('ERR')
      events.remove('ERROR')

    if 'WARNING' in events:
      events.add('WARN')
      events.remove('WARNING')

    tor_events = events.intersection(set(nyx.arguments.TOR_EVENT_TYPES.values()))
    nyx_events = events.intersection(set(['NYX_%s' % runlevel for runlevel in log.Runlevel.keys()]))

    # adds events unrecognized by nyx if we're listening to the 'UNKNOWN' type

    if 'UNKNOWN' in events:
      tor_events.update(set(nyx.arguments.missing_event_types()))

    controller = tor_controller()
    controller.remove_event_listener(self.register_tor_event)

    for event_type in list(tor_events):
      try:
        controller.add_event_listener(self.register_tor_event, event_type)
      except stem.ProtocolError:
        tor_events.remove(event_type)

    # provides back the input set minus events we failed to set

    return sorted(tor_events.union(nyx_events))

  def _reset_listener(self, controller, event_type, _):
    # if we're attaching to a new tor instance then clears the log and
    # prepopulates it with the content belonging to this instance

    if event_type == State.INIT:
      self.reprepopulate_events()
      self.redraw(True)
    elif event_type == State.CLOSED:
      log.notice('Tor control port closed')

  def _get_title(self, width):
    """
    Provides the label used for the panel, looking like:
      Events (NYX NOTICE - ERR, BW - filter: prepopulate):

    This truncates the attributes (with an ellipse) if too long, and condenses
    runlevel ranges if there's three or more in a row (for instance NYX_INFO,
    NYX_NOTICE, and NYX_WARN becomes 'NYX_INFO - WARN').

    Arguments:
      width - width constraint the label needs to fix in
    """

    # usually the attributes used to make the label are decently static, so
    # provide cached results if they're unchanged

    with self.vals_lock:
      current_pattern = self.regex_filter.pattern if self.regex_filter else None
      is_unchanged = self._title_args[0] == self.logged_events
      is_unchanged &= self._title_args[1] == current_pattern
      is_unchanged &= self._title_args[2] == width

      if is_unchanged:
        return self._title_cache

      events_list = list(self.logged_events)

      if not events_list:
        if not current_pattern:
          panel_label = 'Events:'
        else:
          label_pattern = str_tools.crop(current_pattern, width - 18)
          panel_label = 'Events (filter: %s):' % label_pattern
      else:
        # does the following with all runlevel types (tor, nyx, and stem):
        # - pulls to the start of the list
        # - condenses range if there's three or more in a row (ex. "NYX_INFO - WARN")
        # - condense further if there's identical runlevel ranges for multiple
        #   types (ex. "NOTICE - ERR, NYX_NOTICE - ERR" becomes "TOR/NYX NOTICE - ERR")

        tmp_runlevels = []  # runlevels pulled from the list (just the runlevel part)
        runlevel_ranges = []  # tuple of type, start_level, end_level for ranges to be consensed

        # reverses runlevels and types so they're appended in the right order

        reversed_runlevels = list(log.Runlevel)
        reversed_runlevels.reverse()

        for prefix in ('NYX_', ''):
          # blank ending runlevel forces the break condition to be reached at the end
          for runlevel in reversed_runlevels + ['']:
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
                if prefixes[k] == '':
                  prefixes[k] = 'TOR'
                else:
                  prefixes[k] = prefixes[k].replace('_', '')

              events_list.insert(0, '%s %s - %s' % ('/'.join(prefixes), start_level, end_level))
            else:
              events_list.insert(0, '%s%s - %s' % (prefix, start_level, end_level))

        # truncates to use an ellipsis if too long, for instance:

        attr_label = ', '.join(events_list)

        if current_pattern:
          attr_label += ' - filter: %s' % current_pattern

        attr_label = str_tools.crop(attr_label, width - 10, 1)

        if attr_label:
          attr_label = ' (%s)' % attr_label

        panel_label = 'Events%s:' % attr_label

      # cache results and return

      self._title_cache = panel_label
      self._title_args = (list(self.logged_events), current_pattern, width)

      return panel_label
