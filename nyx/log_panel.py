"""
Panel providing a chronological log of events its been configured to listen
for. This provides prepopulation from the log file and supports filtering by
regular expressions.
"""

import os
import time
import curses
import threading

import stem.response.events

import nyx.arguments
import nyx.popups
import nyx.util.log

from stem.util import conf, log, str_tools
from nyx.util import join, panel, tor_controller, ui_tools


def conf_handler(key, value):
  if key == 'features.log.maxLinesPerEntry':
    return max(1, value)
  elif key == 'features.log.prepopulateReadLimit':
    return max(0, value)
  elif key == 'features.log.maxRefreshRate':
    return max(10, value)
  elif key == 'cache.log_panel.size':
    return max(1000, value)


CONFIG = conf.config_dict('nyx', {
  'features.logFile': '',
  'features.log.showDuplicateEntries': False,
  'features.log.maxLinesPerEntry': 6,
  'features.log.prepopulate': True,
  'features.log.prepopulateReadLimit': 5000,
  'features.log.maxRefreshRate': 300,
  'features.log.regex': [],
  'cache.log_panel.size': 1000,
  'msg.misc.event_types': '',
  'attr.log_color': {},
}, conf_handler)

TIMEZONE_OFFSET = time.altzone if time.localtime()[8] else time.timezone

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


class LogPanel(panel.Panel, threading.Thread):
  """
  Listens for and displays tor, nyx, and stem events. This prepopulates
  from tor's log file if it exists.
  """

  def __init__(self, stdscr, logged_events):
    panel.Panel.__init__(self, stdscr, 'log', 0)
    threading.Thread.__init__(self)
    self.setDaemon(True)

    self._logged_events = nyx.util.log.LogGroup(CONFIG['cache.log_panel.size'], group_by_day = True)
    self._logged_event_types = nyx.util.log.listen_for_events(self._register_tor_event, logged_events)
    self._log_file = nyx.util.log.LogFileOutput(CONFIG['features.logFile'])
    self._filter = nyx.util.log.LogFilters(initial_filters = CONFIG['features.log.regex'])

    self.set_pause_attr('_logged_events')

    self._halt = False  # terminates thread if true
    self._lock = threading.RLock()
    self._pause_condition = threading.Condition()
    self._has_new_event = False

    # fetches past tor events from log file, if available

    if CONFIG['features.log.prepopulate']:
      log_location = nyx.util.log.log_file_path(tor_controller())

      if log_location:
        try:
          for entry in reversed(list(nyx.util.log.read_tor_log(log_location, CONFIG['features.log.prepopulateReadLimit']))):
            if entry.type in self._logged_event_types:
              self._logged_events.add(entry)
        except IOError as exc:
          log.info('Unable to read log located at %s: %s' % (log_location, exc))
        except ValueError as exc:
          log.info(str(exc))

    self._last_content_height = len(self._logged_events)  # height of the rendered content when last drawn
    self._scroll = 0

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

    nyx_config = conf.get_config('nyx')
    nyx_config.set('features.log.showDuplicateEntries', str(is_visible))

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
          try:
            user_input = user_input.replace(' ', '')  # strip spaces
            event_types = nyx.arguments.expand_events(user_input)

            if event_types != self._logged_event_types:
              with self._lock:
                self._logged_event_types = nyx.util.log.listen_for_events(self._register_tor_event, event_types)
                self.redraw(True)
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
        nyx.popups.show_msg('Unable to save snapshot: %s' % exc, 2)

  def clear(self):
    """
    Clears the contents of the event log.
    """

    with self._lock:
      self._logged_events = nyx.util.log.LogGroup(CONFIG['cache.log_panel.size'], group_by_day = True)
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

    with self._lock:
      with open(path, 'w') as snapshot_file:
        try:
          for entry in reversed(list(self._logged_events)):
            if self._filter.match(entry.display_message):
              snapshot_file.write(entry.display_message + '\n')
        except Exception as exc:
          raise IOError("unable to write to '%s': %s" % (path, exc))

  def handle_key(self, key):
    if key.is_scroll():
      page_height = self.get_preferred_size()[0] - 1
      new_scroll = ui_tools.get_scroll_position(key, self._scroll, page_height, self._last_content_height)

      if self._scroll != new_scroll:
        with self._lock:
          self._scroll = new_scroll
          self.redraw(True)
    elif key.match('u'):
      with self._lock:
        self.set_duplicate_visability(not CONFIG['features.log.showDuplicateEntries'])
        self.redraw(True)
    elif key.match('c'):
      msg = 'This will clear the log. Are you sure (c again to confirm)?'
      key_press = nyx.popups.show_msg(msg, attr = curses.A_BOLD)

      if key_press.match('c'):
        self.clear()
    elif key.match('f'):
      with panel.CURSES_LOCK:
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
      ('u', 'duplicate log entries', 'visible' if CONFIG['features.log.showDuplicateEntries'] else 'hidden'),
      ('c', 'clear event log', None),
    ]

  def draw(self, width, height):
    with self._lock:
      event_log = list(self.get_attr('_logged_events'))
      self._scroll = max(0, min(self._scroll, self._last_content_height - height + 1))

      is_scroll_bar_visible = self._last_content_height > height - 1

      if is_scroll_bar_visible:
        self.add_scroll_bar(self._scroll, self._scroll + height - 1, self._last_content_height, 1)

      x, y = 3 if is_scroll_bar_visible else 1, 1 - self._scroll

      # group entries by date, filtering out those that aren't visible

      days_ago_to_entries = {}

      for entry in event_log:
        if entry.is_duplicate and not CONFIG['features.log.showDuplicateEntries']:
          continue  # deduplicated message
        elif not self._filter.match(entry.display_message):
          continue  # filter doesn't match log message

        days_ago_to_entries.setdefault(entry.days_since(), []).append(entry)

      for days_ago in sorted(days_ago_to_entries.keys()):
        if days_ago == 0:
          for entry in days_ago_to_entries[days_ago]:
            y = self._draw_entry(x, y, width, entry)
        else:
          original_y, y = y, y + 1

          for entry in days_ago_to_entries[days_ago]:
            y = self._draw_entry(x, y, width, entry)

          ui_tools.draw_box(self, original_y, x - 1, width - x + 1, y - original_y + 1, curses.A_BOLD, 'yellow')
          time_label = time.strftime(' %B %d, %Y ', time.localtime(days_ago_to_entries[days_ago][0].timestamp))
          self.addstr(original_y, x + 1, time_label, curses.A_BOLD, curses.A_BOLD, 'yellow')

          y += 1

      # drawing the title after the content, so we'll clear content from the top line

      if self.is_title_visible():
        self._draw_title(width)

      # redraw the display if...
      # - last_content_height was off by too much
      # - we're off the bottom of the page

      new_content_height = y + self._scroll - 1
      content_height_delta = abs(self._last_content_height - new_content_height)
      force_redraw, force_redraw_reason = True, ''

      if content_height_delta >= CONTENT_HEIGHT_REDRAW_THRESHOLD:
        force_redraw_reason = 'estimate was off by %i' % content_height_delta
      elif new_content_height > height and self._scroll + height - 1 > new_content_height:
        force_redraw_reason = 'scrolled off the bottom of the page'
      elif not is_scroll_bar_visible and new_content_height > height - 1:
        force_redraw_reason = "scroll bar wasn't previously visible"
      elif is_scroll_bar_visible and new_content_height <= height - 1:
        force_redraw_reason = "scroll bar shouldn't be visible"
      else:
        force_redraw = False

      self._last_content_height = new_content_height
      self._has_new_event = False

      if force_redraw:
        log.debug('redrawing the log panel with the corrected content height (%s)' % force_redraw_reason)
        self.redraw(True)

  def _draw_title(self, width):
    """
    Panel title with the event types we're logging and our regex filter if set.
    """

    self.addstr(0, 0, ' ' * width)  # clear line
    title_comp = list(nyx.util.log.condense_runlevels(*self._logged_event_types))

    if self._filter.selection():
      title_comp.append('filter: %s' % self._filter.selection())

    title_comp_str = join(title_comp, ', ', width - 10)
    title = 'Events (%s):' % title_comp_str if title_comp_str else 'Events:'

    self.addstr(0, 0, title, curses.A_STANDOUT)

  def _draw_entry(self, x, y, width, entry):
    """
    Presents a log entry with line wrapping.
    """

    def draw_line(x, y, width, msg, *attr):
      msg, remaining_lines = msg.split('\n', 1) if ('\n' in msg) else (msg, '')
      msg, cropped = str_tools.crop(msg, width - x - 1, min_crop = 4, ending = str_tools.Ending.HYPHEN, get_remainder = True)
      x = self.addstr(y, x, msg, *attr)
      return x, (cropped + '\n' + remaining_lines).strip()

    def draw_msg(min_x, x, y, width, msg, *attr):
      orig_y = y

      while msg:
        x, msg = draw_line(x, y, width, msg, *attr)

        if (y - orig_y + 1) >= CONFIG['features.log.maxLinesPerEntry']:
          break  # filled up the maximum number of lines we're allowing for

        if msg:
          msg = '  ' + msg  # indent the next line
          x, y = min_x, y + 1

      return x, y

    min_x, msg = x, entry.display_message
    boldness = curses.A_BOLD if 'ERR' in entry.type else curses.A_NORMAL  # emphasize ERR messages
    color = CONFIG['attr.log_color'].get(entry.type, 'white')

    x, y = draw_msg(min_x, x, y, width, msg, boldness, color)

    if entry.duplicates and not CONFIG['features.log.showDuplicateEntries']:
      duplicate_count = len(entry.duplicates) - 1
      plural = 's' if duplicate_count > 1 else ''
      duplicate_msg = ' [%i duplicate%s hidden]' % (duplicate_count, plural)
      x, y = draw_msg(min_x, x, y, width, duplicate_msg, curses.A_BOLD, 'green')

    return y + 1

  def run(self):
    """
    Redraws the display, coalescing updates if events are rapidly logged (for
    instance running at the DEBUG runlevel) while also being immediately
    responsive if additions are less frequent.
    """

    last_ran, last_day = -1, int((time.time() - TIMEZONE_OFFSET) / 86400)

    while not self._halt:
      current_day = int((time.time() - TIMEZONE_OFFSET) / 86400)
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

    self._register_event(nyx.util.log.LogEntry(event.arrived_at, event.type, msg))

  def _register_nyx_event(self, record):
    if record.levelname == 'WARNING':
      record.levelname = 'WARN'

    self._register_event(nyx.util.log.LogEntry(int(record.created), 'NYX_%s' % record.levelname, record.msg))

  def _register_event(self, event):
    if event.type not in self._logged_event_types:
      return

    with self._lock:
      self._logged_events.add(event)
      self._log_file.write(event.display_message)

      # notifies the display that it has new content

      if self._filter.match(event.display_message):
        self._has_new_event = True

        with self._pause_condition:
          self._pause_condition.notifyAll()
