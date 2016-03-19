"""
Panel providing a chronological log of events its been configured to listen
for. This provides prepopulation from the log file and supports filtering by
regular expressions.
"""

import os
import time
import threading

import stem.response.events

import nyx.arguments
import nyx.curses
import nyx.panel
import nyx.popups
import nyx.log

from nyx import join, tor_controller
from nyx.curses import GREEN, YELLOW, WHITE, NORMAL, BOLD, HIGHLIGHT
from stem.util import conf, log


def conf_handler(key, value):
  if key == 'features.log.prepopulateReadLimit':
    return max(0, value)
  elif key == 'features.log.maxRefreshRate':
    return max(10, value)
  elif key == 'cache.log_panel.size':
    return max(1000, value)


CONFIG = conf.config_dict('nyx', {
  'attr.log_color': {},
  'cache.log_panel.size': 1000,
  'features.logFile': '',
  'features.log.showDuplicateEntries': False,
  'features.log.prepopulate': True,
  'features.log.prepopulateReadLimit': 5000,
  'features.log.maxRefreshRate': 300,
  'features.log.regex': [],
  'msg.misc.event_types': '',
  'startup.events': 'N3',
}, conf_handler)

# The height of the drawn content is estimated based on the last time we redrew
# the panel. It's chiefly used for scrolling and the bar indicating its
# position. Letting the estimate be too inaccurate results in a display bug, so
# redraws the display if it's off by this threshold.

CONTENT_HEIGHT_REDRAW_THRESHOLD = 3

# Log buffer so we start collecting stem/nyx events when imported. This is used
# to make our LogPanel when curses initializes.

stem_logger = log.get_logger()
NYX_LOGGER = log.LogBuffer(log.Runlevel.DEBUG, yield_records = True)
stem_logger.addHandler(NYX_LOGGER)


class LogPanel(nyx.panel.Panel, threading.Thread):
  """
  Listens for and displays tor, nyx, and stem events. This prepopulates
  from tor's log file if it exists.
  """

  def __init__(self):
    nyx.panel.Panel.__init__(self, 'log')
    threading.Thread.__init__(self)
    self.setDaemon(True)

    logged_events = nyx.arguments.expand_events(CONFIG['startup.events'])
    self._event_log = nyx.log.LogGroup(CONFIG['cache.log_panel.size'], group_by_day = True)
    self._event_log_paused = None
    self._event_types = nyx.log.listen_for_events(self._register_tor_event, logged_events)
    self._log_file = nyx.log.LogFileOutput(CONFIG['features.logFile'])
    self._filter = nyx.log.LogFilters(initial_filters = CONFIG['features.log.regex'])
    self._show_duplicates = CONFIG['features.log.showDuplicateEntries']

    self._scroller = nyx.curses.Scroller()
    self._halt = False  # terminates thread if true
    self._pause_condition = threading.Condition()
    self._has_new_event = False

    # fetches past tor events from log file, if available

    if CONFIG['features.log.prepopulate']:
      log_location = nyx.log.log_file_path(tor_controller())

      if log_location:
        try:
          for entry in reversed(list(nyx.log.read_tor_log(log_location, CONFIG['features.log.prepopulateReadLimit']))):
            if entry.type in self._event_types:
              self._event_log.add(entry)
        except IOError as exc:
          log.info('Unable to read log located at %s: %s' % (log_location, exc))
        except ValueError as exc:
          log.info(str(exc))

    self._last_content_height = len(self._event_log)  # height of the rendered content when last drawn

    # merge NYX_LOGGER into us, and listen for its future events

    for event in NYX_LOGGER:
      self._register_nyx_event(event)

    NYX_LOGGER.emit = self._register_nyx_event

  def set_duplicate_visability(self, is_visible):
    """
    Sets if duplicate log entries are collaped or expanded.

    :param bool is_visible: if **True** all log entries are shown, otherwise
      they're deduplicated
    """

    self._show_duplicates = is_visible

  def get_filter(self):
    """
    Provides our currently selected regex filter.
    """

    return self._filter

  def show_filter_prompt(self):
    """
    Prompts the user to add a new regex filter.
    """

    regex_input = nyx.popups.input_prompt('Regular expression: ')

    if regex_input:
      self._filter.select(regex_input)

  def show_event_selection_prompt(self):
    """
    Prompts the user to select the events being listened for.
    """

    # allow user to enter new types of events to log - unchanged if left blank

    with nyx.popups.popup_window(16, 80) as (popup, width, height):
      if popup:
        # displays the available flags

        popup.win.box()
        popup.addstr(0, 0, 'Event Types:', HIGHLIGHT)
        event_lines = CONFIG['msg.misc.event_types'].split('\n')

        for i in range(len(event_lines)):
          popup.addstr(i + 1, 1, event_lines[i][6:])

        popup.win.refresh()

        user_input = nyx.popups.input_prompt('Events to log: ')

        if user_input:
          try:
            user_input = user_input.replace(' ', '')  # strip spaces
            event_types = nyx.arguments.expand_events(user_input)

            if event_types != self._event_types:
              self._event_types = nyx.log.listen_for_events(self._register_tor_event, event_types)
              self.redraw(True)
          except ValueError as exc:
            nyx.popups.show_msg('Invalid flags: %s' % str(exc), 2)

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
        nyx.popups.show_msg('Unable to save snapshot: %s' % exc, 2)

  def clear(self):
    """
    Clears the contents of the event log.
    """

    self._event_log = nyx.log.LogGroup(CONFIG['cache.log_panel.size'], group_by_day = True)
    self.redraw(True)

  def save_snapshot(self, path):
    """
    Saves the log events currently being displayed to the given path. This
    takes filers into account. This overwrites the file if it already exists.

    :param str path: path where to save the log snapshot

    :raises: **IOError** if unsuccessful
    """

    path = os.path.abspath(os.path.expanduser(path))

    # make dir if the path doesn't already exist

    base_dir = os.path.dirname(path)

    try:
      if not os.path.exists(base_dir):
        os.makedirs(base_dir)
    except OSError as exc:
      raise IOError("unable to make directory '%s'" % base_dir)

    event_log = list(self._event_log)
    event_filter = self._filter.clone()

    with open(path, 'w') as snapshot_file:
      try:
        for entry in reversed(event_log):
          if event_filter.match(entry.display_message):
            snapshot_file.write(entry.display_message + '\n')
      except Exception as exc:
        raise IOError("unable to write to '%s': %s" % (path, exc))

  def handle_key(self, key):
    if key.is_scroll():
      page_height = self.get_preferred_size()[0] - 1
      is_changed = self._scroller.handle_key(key, self._last_content_height, page_height)

      if is_changed:
        self.redraw(True)
    elif key.match('u'):
      self.set_duplicate_visability(not self._show_duplicates)
      self.redraw(True)
    elif key.match('c'):
      msg = 'This will clear the log. Are you sure (c again to confirm)?'
      key_press = nyx.popups.show_msg(msg, attr = BOLD)

      if key_press.match('c'):
        self.clear()
    elif key.match('f'):
      with nyx.curses.CURSES_LOCK:
        initial_selection = 1 if self._filter.selection() else 0
        options = ['None'] + self._filter.latest_selections() + ['New...']
        selection = nyx.popups.show_menu('Log Filter:', options, initial_selection)

        if selection == 0:
          self._filter.select(None)
        elif selection == len(options) - 1:
          # selected 'New...' option - prompt user to input regular expression
          self.show_filter_prompt()
        elif selection != -1:
          self._filter.select(self._filter.latest_selections()[selection - 1])
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
      ('f', 'log regex filter', 'enabled' if self._filter.selection() else 'disabled'),
      ('u', 'duplicate log entries', 'visible' if self._show_duplicates else 'hidden'),
      ('c', 'clear event log', None),
    ]

  def set_paused(self, is_pause):
    if is_pause:
      self._event_log_paused = self._event_log.clone()

    nyx.panel.Panel.set_paused(self, is_pause)

  def draw(self, width, height):
    scroll = self._scroller.location(self._last_content_height, height)

    event_log = list(self._event_log_paused if self.is_paused() else self._event_log)
    event_filter = self._filter.clone()
    event_types = list(self._event_types)
    last_content_height = self._last_content_height
    show_duplicates = self._show_duplicates

    is_scrollbar_visible = last_content_height > height - 1

    if is_scrollbar_visible:
      self.add_scroll_bar(scroll, scroll + height - 1, last_content_height, 1)

    x, y = 3 if is_scrollbar_visible else 1, 1 - scroll

    # group entries by date, filtering out those that aren't visible

    day_to_entries, today = {}, nyx.log.day_count(time.time())

    for entry in event_log:
      if entry.is_duplicate and not show_duplicates:
        continue  # deduplicated message
      elif not event_filter.match(entry.display_message):
        continue  # filter doesn't match log message

      day_to_entries.setdefault(entry.day_count(), []).append(entry)

    for day in sorted(day_to_entries.keys(), reverse = True):
      if day == today:
        for entry in day_to_entries[day]:
          y = self._draw_entry(x, y, width, entry, show_duplicates)
      else:
        original_y, y = y, y + 1

        for entry in day_to_entries[day]:
          y = self._draw_entry(x, y, width, entry, show_duplicates)

        self.draw_box(original_y, x - 1, width - x + 1, y - original_y + 1, YELLOW, BOLD)
        time_label = time.strftime(' %B %d, %Y ', time.localtime(day_to_entries[day][0].timestamp))
        self.addstr(original_y, x + 1, time_label, YELLOW, BOLD)

        y += 1

    # drawing the title after the content, so we'll clear content from the top line

    if self.is_title_visible():
      self._draw_title(width, event_types, event_filter)

    # redraw the display if...
    # - last_content_height was off by too much
    # - we're off the bottom of the page

    new_content_height = y + scroll - 1
    content_height_delta = abs(last_content_height - new_content_height)
    force_redraw, force_redraw_reason = True, ''

    if content_height_delta >= CONTENT_HEIGHT_REDRAW_THRESHOLD:
      force_redraw_reason = 'estimate was off by %i' % content_height_delta
    elif new_content_height > height and scroll + height - 1 > new_content_height:
      force_redraw_reason = 'scrolled off the bottom of the page'
    elif not is_scrollbar_visible and new_content_height > height - 1:
      force_redraw_reason = "scroll bar wasn't previously visible"
    elif is_scrollbar_visible and new_content_height <= height - 1:
      force_redraw_reason = "scroll bar shouldn't be visible"
    else:
      force_redraw = False

    self._last_content_height = new_content_height
    self._has_new_event = False

    if force_redraw:
      log.debug('redrawing the log panel with the corrected content height (%s)' % force_redraw_reason)
      self.redraw(True)

  def _draw_title(self, width, event_types, event_filter):
    """
    Panel title with the event types we're logging and our regex filter if set.
    """

    self.addstr(0, 0, ' ' * width)  # clear line
    title_comp = list(nyx.log.condense_runlevels(*event_types))

    if event_filter.selection():
      title_comp.append('filter: %s' % event_filter.selection())

    title_comp_str = join(title_comp, ', ', width - 10)
    title = 'Events (%s):' % title_comp_str if title_comp_str else 'Events:'

    self.addstr(0, 0, title, HIGHLIGHT)

  def _draw_entry(self, x, y, width, entry, show_duplicates):
    """
    Presents a log entry with line wrapping.
    """

    min_x, msg = x + 2, entry.display_message
    boldness = BOLD if 'ERR' in entry.type else NORMAL  # emphasize ERR messages
    color = CONFIG['attr.log_color'].get(entry.type, WHITE)

    for line in msg.splitlines():
      x, y = self.addstr_wrap(y, x, line, width, min_x, boldness, color)

    if entry.duplicates and not show_duplicates:
      duplicate_count = len(entry.duplicates) - 1
      plural = 's' if duplicate_count > 1 else ''
      duplicate_msg = ' [%i duplicate%s hidden]' % (duplicate_count, plural)
      x, y = self.addstr_wrap(y, x, duplicate_msg, width, min_x, GREEN, BOLD)

    return y + 1

  def run(self):
    """
    Redraws the display, coalescing updates if events are rapidly logged (for
    instance running at the DEBUG runlevel) while also being immediately
    responsive if additions are less frequent.
    """

    last_ran, last_day = -1, nyx.log.day_count(time.time())

    while not self._halt:
      current_day = nyx.log.day_count(time.time())
      time_since_reset = time.time() - last_ran
      max_log_update_rate = CONFIG['features.log.maxRefreshRate'] / 1000.0

      sleep_time = 0

      if (not self._has_new_event and last_day == current_day) or self.is_paused():
        sleep_time = 5
      elif time_since_reset < max_log_update_rate:
        sleep_time = max(0.05, max_log_update_rate - time_since_reset)

      if sleep_time:
        with self._pause_condition:
          if not self._halt:
            self._pause_condition.wait(sleep_time)

        continue

      last_ran, last_day = time.time(), current_day
      self.redraw(True)

  def stop(self):
    """
    Halts further updates and terminates the thread.
    """

    with self._pause_condition:
      self._halt = True
      self._pause_condition.notifyAll()

  def _register_tor_event(self, event):
    msg = ' '.join(str(event).split(' ')[1:])

    if isinstance(event, stem.response.events.BandwidthEvent):
      msg = 'READ: %i, WRITTEN: %i' % (event.read, event.written)
    elif isinstance(event, stem.response.events.LogEvent):
      msg = event.message

    self._register_event(nyx.log.LogEntry(event.arrived_at, event.type, msg))

  def _register_nyx_event(self, record):
    if record.levelname == 'WARNING':
      record.levelname = 'WARN'

    self._register_event(nyx.log.LogEntry(int(record.created), 'NYX_%s' % record.levelname, record.msg))

  def _register_event(self, event):
    if event.type not in self._event_types:
      return

    self._event_log.add(event)
    self._log_file.write(event.display_message)

    # notifies the display that it has new content

    if self._filter.match(event.display_message):
      self._has_new_event = True

      with self._pause_condition:
        self._pause_condition.notifyAll()
