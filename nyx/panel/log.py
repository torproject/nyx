# Copyright 2009-2017, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Panel providing a chronological log of events its been configured to listen
for. This provides prepopulation from the log file and supports filtering by
regular expressions.
"""

import functools
import os
import time

import stem.response.events

import nyx.arguments
import nyx.curses
import nyx.panel
import nyx.popups
import nyx.log

from nyx import nyx_interface, tor_controller, join, input_prompt, show_message
from nyx.curses import GREEN, YELLOW, WHITE, NORMAL, BOLD, HIGHLIGHT
from nyx.menu import MenuItem, Submenu, RadioMenuItem, RadioGroup
from stem.util import conf, log


def conf_handler(key, value):
  if key == 'prepopulate_read_limit':
    return max(0, value)
  elif key == 'max_log_size':
    return max(1000, value)


CONFIG = conf.config_dict('nyx', {
  'attr.log_color': {},
  'deduplicate_log': True,
  'logged_events': 'NOTICE,WARN,ERR,NYX_NOTICE,NYX_WARNING,NYX_ERROR',
  'logging_filter': [],
  'max_log_size': 1000,
  'prepopulate_log': True,
  'prepopulate_read_limit': 5000,
  'write_logs_to': '',
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
    nyx.panel.DaemonPanel.__init__(self, UPDATE_RATE)

    logged_events = CONFIG['logged_events'].split(',')
    tor_events = tor_controller().get_info('events/names', '').split()
    invalid_events = list(filter(lambda event: not event.startswith('NYX_') and event not in tor_events, logged_events))

    if invalid_events:
      logged_events = ['NOTICE', 'WARN', 'ERR', 'NYX_NOTICE', 'NYX_WARNING', 'NYX_ERROR']
      log.warn("Your --log argument had the following events tor doesn't recognize: %s" % ', '.join(invalid_events))

    self._event_log = nyx.log.LogGroup(CONFIG['max_log_size'])
    self._event_log_paused = None
    self._event_types = nyx.log.listen_for_events(self._register_tor_event, logged_events)
    self._log_file = nyx.log.LogFileOutput(CONFIG['write_logs_to'])
    self._filter = nyx.log.LogFilters(initial_filters = CONFIG['logging_filter'])
    self._show_duplicates = not CONFIG['deduplicate_log']

    self._scroller = nyx.curses.Scroller()
    self._has_new_event = False
    self._last_day = nyx.log.day_count(time.time())

    # fetches past tor events from log file, if available

    if CONFIG['prepopulate_log']:
      log_location = nyx.log.log_file_path(tor_controller())

      if log_location:
        try:
          for entry in reversed(list(nyx.log.read_tor_log(log_location, CONFIG['prepopulate_read_limit']))):
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

  def _show_filter_prompt(self):
    """
    Prompts the user to add a new regex filter.
    """

    regex_input = input_prompt('Regular expression: ')

    if regex_input:
      self._filter.select(regex_input)

  def _show_event_selection_prompt(self):
    """
    Prompts the user to select the events being listened for.
    """

    event_types = nyx.popups.select_event_types(self._event_types)

    if event_types and event_types != self._event_types:
      self._event_types = nyx.log.listen_for_events(self._register_tor_event, event_types)
      self.redraw()

  def _show_snapshot_prompt(self):
    """
    Lets user enter a path to take a snapshot, canceling if left blank.
    """

    path_input = input_prompt('Path to save log snapshot: ')

    if path_input:
      try:
        self.save_snapshot(path_input)
        show_message('Saved: %s' % path_input, HIGHLIGHT, max_wait = 2)
      except IOError as exc:
        show_message('Unable to save snapshot: %s' % exc, HIGHLIGHT, max_wait = 2)

  def _clear(self):
    """
    Clears the contents of the event log.
    """

    self._event_log = nyx.log.LogGroup(CONFIG['max_log_size'])
    self.redraw()

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

  def set_paused(self, is_pause):
    if is_pause:
      self._event_log_paused = self._event_log.clone()

  def key_handlers(self):
    def _scroll(key):
      page_height = self.get_height() - 1
      is_changed = self._scroller.handle_key(key, self._last_content_height, page_height)

      if is_changed:
        self.redraw()

    def _pick_filter():
      with nyx.curses.CURSES_LOCK:
        options = ['None'] + self._filter.latest_selections() + ['New...']
        initial_selection = self._filter.selection() if self._filter.selection() else 'None'
        selection = nyx.popups.select_from_list('Log Filter:', options, initial_selection)

        if selection == 'None':
          self._filter.select(None)
        elif selection == 'New...':
          self._show_filter_prompt()  # prompt user to input regular expression
        else:
          self._filter.select(selection)

    def _toggle_deduplication():
      self._show_duplicates = not self._show_duplicates
      self.redraw()

    def _clear_log():
      msg = 'This will clear the log. Are you sure (c again to confirm)?'
      key_press = show_message(msg, BOLD, max_wait = 30)

      if key_press.match('c'):
        self._clear()

    return (
      nyx.panel.KeyHandler('arrows', 'scroll up and down', _scroll, key_func = lambda key: key.is_scroll()),
      nyx.panel.KeyHandler('a', 'save snapshot of the log', self._show_snapshot_prompt),
      nyx.panel.KeyHandler('e', 'change logged events', self._show_event_selection_prompt),
      nyx.panel.KeyHandler('f', 'log regex filter', _pick_filter, 'enabled' if self._filter.selection() else 'disabled'),
      nyx.panel.KeyHandler('u', 'duplicate log entries', _toggle_deduplication, 'visible' if self._show_duplicates else 'hidden'),
      nyx.panel.KeyHandler('c', 'clear event log', _clear_log),
    )

  def submenu(self):
    """
    Submenu consisting of...

      Events...
      Snapshot...
      Clear
      Show / Hide Duplicates
      Filter (Submenu)
    """

    filter_group = RadioGroup(self._filter.select, self._filter.selection())
    duplicates_label, duplicates_arg = ('Hide Duplicates', False) if self._show_duplicates else ('Show Duplicates', True)

    return Submenu('Log', [
      MenuItem('Events...', self._show_event_selection_prompt),
      MenuItem('Snapshot...', self._show_snapshot_prompt),
      MenuItem('Clear', self._clear),
      MenuItem(duplicates_label, functools.partial(setattr, self, '_show_duplicates'), duplicates_arg),
      Submenu('Filter', [
        RadioMenuItem('None', filter_group, None),
        [RadioMenuItem(opt, filter_group, opt) for opt in self._filter.latest_selections()],
        MenuItem('New...', self._show_filter_prompt),
      ]),
    ])

  def _draw(self, subwindow):
    scroll = self._scroller.location(self._last_content_height, subwindow.height - 1)

    event_filter = self._filter.clone()
    event_types = list(self._event_types)
    last_content_height = self._last_content_height
    show_duplicates = self._show_duplicates

    event_log = self._event_log_paused if nyx_interface().is_paused() else self._event_log
    event_log = list(filter(lambda entry: event_filter.match(entry.display_message), event_log))
    event_log = list(filter(lambda entry: not entry.is_duplicate or show_duplicates, event_log))

    is_scrollbar_visible = last_content_height > subwindow.height - 1

    if is_scrollbar_visible:
      subwindow.scrollbar(1, scroll, last_content_height)

    x, y = 2 if is_scrollbar_visible else 0, 1 - scroll
    y = _draw_entries(subwindow, x, y, event_log, show_duplicates)

    # drawing the title after the content, so we'll clear content from the top line

    _draw_title(subwindow, event_types, event_filter)

    # redraw the display if...
    # - last_content_height was off by too much
    # - we're off the bottom of the page

    new_content_height = y + scroll - 1
    content_height_delta = abs(last_content_height - new_content_height)
    force_redraw, force_redraw_reason = True, ''

    if content_height_delta >= CONTENT_HEIGHT_REDRAW_THRESHOLD:
      force_redraw_reason = 'estimate was off by %i' % content_height_delta
    elif new_content_height > subwindow.height and scroll + subwindow.height - 1 > new_content_height:
      force_redraw_reason = 'scrolled off the bottom of the page'
    elif not is_scrollbar_visible and new_content_height > subwindow.height - 1:
      force_redraw_reason = "scroll bar wasn't previously visible"
    elif is_scrollbar_visible and new_content_height <= subwindow.height - 1:
      force_redraw_reason = "scroll bar shouldn't be visible"
    else:
      force_redraw = False

    self._last_content_height = new_content_height
    self._has_new_event = False

    if force_redraw:
      log.debug('redrawing the log panel with the corrected content height (%s)' % force_redraw_reason)
      self.redraw()

  def _update(self):
    """
    Redraws the display, coalescing updates if events are rapidly logged (for
    instance running at the DEBUG runlevel) while also being immediately
    responsive if additions are less frequent.
    """

    current_day = nyx.log.day_count(time.time())

    if self._has_new_event or self._last_day != current_day:
      self._last_day = current_day
      self.redraw()

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


def _draw_title(subwindow, event_types, event_filter):
  """
  Panel title with the event types we're logging and our regex filter if set.
  """

  subwindow.addstr(0, 0, ' ' * subwindow.width)  # clear line
  title_comp = list(nyx.log.condense_runlevels(*event_types))

  if event_filter.selection():
    title_comp.append('filter: %s' % event_filter.selection())

  title_comp_str = join(title_comp, ', ', subwindow.width - 10)
  title = 'Events (%s):' % title_comp_str if title_comp_str else 'Events:'

  subwindow.addstr(0, 0, title, HIGHLIGHT)


def _draw_entries(subwindow, x, y, event_log, show_duplicates):
  """
  Presents a list of log entries, grouped by the day they appeared.
  """

  day_to_entries, today = {}, nyx.log.day_count(time.time())

  for entry in event_log:
    day_to_entries.setdefault(entry.day_count(), []).append(entry)

  for day in sorted(day_to_entries.keys(), reverse = True):
    if day == today:
      for entry in day_to_entries[day]:
        y = _draw_entry(subwindow, x + 1, y, subwindow.width, entry, show_duplicates)
    else:
      original_y, y = y, y + 1

      for entry in day_to_entries[day]:
        y = _draw_entry(subwindow, x + 1, y, subwindow.width - 1, entry, show_duplicates)

      subwindow.box(x, original_y, subwindow.width - x, y - original_y + 1, YELLOW, BOLD)
      time_label = time.strftime(' %B %d, %Y ', time.localtime(day_to_entries[day][0].timestamp))
      subwindow.addstr(x + 2, original_y, time_label, YELLOW, BOLD)

      y += 1

  return y


def _draw_entry(subwindow, x, y, width, entry, show_duplicates):
  """
  Presents an individual log entry with line wrapping.
  """

  color = CONFIG['attr.log_color'].get(entry.type, WHITE)
  boldness = BOLD if entry.type in ('ERR', 'ERROR') else NORMAL  # emphasize ERROR messages
  min_x = x + 2

  for line in entry.display_message.splitlines():
    x, y = subwindow.addstr_wrap(x, y, line, width, min_x, boldness, color)

  if entry.duplicates and not show_duplicates:
    duplicate_count = len(entry.duplicates) - 1
    plural = 's' if duplicate_count > 1 else ''
    duplicate_msg = ' [%i duplicate%s hidden]' % (duplicate_count, plural)
    x, y = subwindow.addstr_wrap(x, y, duplicate_msg, width, min_x, GREEN, BOLD)

  return y + 1
