# Copyright 2009-2016, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Panel providing a chronological log of events its been configured to listen
for. This provides prepopulation from the log file and supports filtering by
regular expressions.
"""

import os
import time

import stem.response.events

import nyx.arguments
import nyx.controller
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
  elif key == 'cache.log_panel.size':
    return max(1000, value)


CONFIG = conf.config_dict('nyx', {
  'attr.log_color': {},
  'cache.log_panel.size': 1000,
  'features.logFile': '',
  'features.log.showDuplicateEntries': False,
  'features.log.prepopulate': True,
  'features.log.prepopulateReadLimit': 5000,
  'features.log.regex': [],
  'startup.events': 'N3',
}, conf_handler)

UPDATE_RATE = 0.3

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


class LogPanel(nyx.panel.DaemonPanel):
  """
  Listens for and displays tor, nyx, and stem events. This prepopulates
  from tor's log file if it exists.
  """

  def __init__(self):
    nyx.panel.DaemonPanel.__init__(self, 'log', UPDATE_RATE)

    logged_events = nyx.arguments.expand_events(CONFIG['startup.events'])
    self._event_log = nyx.log.LogGroup(CONFIG['cache.log_panel.size'], group_by_day = True)
    self._event_log_paused = None
    self._event_types = nyx.log.listen_for_events(self._register_tor_event, logged_events)
    self._log_file = nyx.log.LogFileOutput(CONFIG['features.logFile'])
    self._filter = nyx.log.LogFilters(initial_filters = CONFIG['features.log.regex'])
    self._show_duplicates = CONFIG['features.log.showDuplicateEntries']

    self._scroller = nyx.curses.Scroller()
    self._has_new_event = False
    self._last_day = nyx.log.day_count(time.time())

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

    regex_input = nyx.controller.input_prompt('Regular expression: ')

    if regex_input:
      self._filter.select(regex_input)

  def show_event_selection_prompt(self):
    """
    Prompts the user to select the events being listened for.
    """

    event_types = nyx.popups.select_event_types()

    if event_types and event_types != self._event_types:
      self._event_types = nyx.log.listen_for_events(self._register_tor_event, event_types)
      self.redraw(True)

  def new_show_event_selection_prompt(self):
    """
    Prompts the user to select the events being listened for.
    TODO: Replace show_event_selection_prompt() with this method.
    """
    event_types = nyx.popups.new_select_event_types()

    if event_types and event_types != self._event_types:
      self._event_types = nyx.log.listen_for_events(self._register_tor_event, event_types)
      self.redraw(True)

  def show_snapshot_prompt(self):
    """
    Lets user enter a path to take a snapshot, canceling if left blank.
    """

    path_input = nyx.controller.input_prompt('Path to save log snapshot: ')

    if path_input:
      try:
        self.save_snapshot(path_input)
        nyx.controller.show_message('Saved: %s' % path_input, HIGHLIGHT, max_wait = 2)
      except IOError as exc:
        nyx.controller.show_message('Unable to save snapshot: %s' % exc, HIGHLIGHT, max_wait = 2)

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

  def key_handlers(self):
    def _scroll(key):
      page_height = self.get_preferred_size()[0] - 1
      is_changed = self._scroller.handle_key(key, self._last_content_height, page_height)

      if is_changed:
        self.redraw(True)

    def _pick_filter():
      with nyx.curses.CURSES_LOCK:
        options = ['None'] + self._filter.latest_selections() + ['New...']
        initial_selection = self._filter.selection() if self._filter.selection() else 'None'
        selection = nyx.popups.select_from_list('Log Filter:', options, initial_selection)

        if selection == 'None':
          self._filter.select(None)
        elif selection == 'New...':
          self.show_filter_prompt()  # prompt user to input regular expression
        else:
          self._filter.select(selection)

    def _toggle_deduplication():
      self.set_duplicate_visability(not self._show_duplicates)
      self.redraw(True)

    def _clear_log():
      msg = 'This will clear the log. Are you sure (c again to confirm)?'
      key_press = nyx.controller.show_message(msg, BOLD, max_wait = 30)

      if key_press.match('c'):
        self.clear()

    return (
      nyx.panel.KeyHandler('arrows', 'scroll up and down', _scroll, key_func = lambda key: key.is_scroll()),
      nyx.panel.KeyHandler('a', 'save snapshot of the log', self.show_snapshot_prompt),
      nyx.panel.KeyHandler('e', 'change logged events', self.show_event_selection_prompt),
      nyx.panel.KeyHandler('w', 'new change logged events', self.new_show_event_selection_prompt),
      nyx.panel.KeyHandler('f', 'log regex filter', _pick_filter, 'enabled' if self._filter.selection() else 'disabled'),
      nyx.panel.KeyHandler('u', 'duplicate log entries', _toggle_deduplication, 'visible' if self._show_duplicates else 'hidden'),
      nyx.panel.KeyHandler('c', 'clear event log', _clear_log),
    )

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

  def _update(self):
    """
    Redraws the display, coalescing updates if events are rapidly logged (for
    instance running at the DEBUG runlevel) while also being immediately
    responsive if additions are less frequent.
    """

    current_day = nyx.log.day_count(time.time())

    if self._has_new_event or self._last_day != current_day:
      self._last_day = current_day
      self.redraw(True)

  def _register_tor_event(self, event):
    msg = ' '.join(str(event).split(' ')[1:])

    if isinstance(event, stem.response.events.BandwidthEvent):
      msg = 'READ: %i, WRITTEN: %i' % (event.read, event.written)
    elif isinstance(event, stem.response.events.LogEvent):
      msg = event.message

    self._register_event(nyx.log.LogEntry(event.arrived_at, event.type, msg))

  def _register_nyx_event(self, record):
    self._register_event(nyx.log.LogEntry(int(record.created), 'NYX_%s' % record.levelname, record.msg))

  def _register_event(self, event):
    if event.type not in self._event_types:
      return

    self._event_log.add(event)
    self._log_file.write(event.display_message)

    # notifies the display that it has new content

    if self._filter.match(event.display_message):
      self._has_new_event = True
