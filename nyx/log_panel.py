"""
Panel providing a chronological log of events its been configured to listen
for. This provides prepopulation from the log file and supports filtering by
regular expressions.
"""

import os
import time
import curses
import threading

import stem
import stem.response.events

from stem.util import conf, log, str_tools

import nyx.arguments
import nyx.popups
import nyx.util.log

from nyx.util import join, panel, tor_controller, ui_tools


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
  'attr.log_color': {},
}, conf_handler)

# The height of the drawn content is estimated based on the last time we redrew
# the panel. It's chiefly used for scrolling and the bar indicating its
# position. Letting the estimate be too inaccurate results in a display bug, so
# redraws the display if it's off by this threshold.

CONTENT_HEIGHT_REDRAW_THRESHOLD = 3

# Log buffer so we start collecting stem/nyx events when imported. This is used
# to make our LogPanel when curses initializes.

stem_logger = stem.util.log.get_logger()
NYX_LOGGER = log.LogBuffer(log.Runlevel.DEBUG, yield_records = True)
stem_logger.addHandler(NYX_LOGGER)


class LogPanel(panel.Panel, threading.Thread):
  """
  Listens for and displays tor, nyx, and stem events. This can prepopulate
  from tor's log file if it exists.
  """

  def __init__(self, stdscr, logged_events):
    panel.Panel.__init__(self, stdscr, 'log', 0)
    threading.Thread.__init__(self)
    self.setDaemon(True)

    self._logged_event_types = nyx.util.log.listen_for_events(self._register_tor_event, logged_events)
    self._logged_events = nyx.util.log.LogGroup(CONFIG['cache.log_panel.size'], group_by_day = CONFIG['features.log.showDateDividers'])
    self._log_file = nyx.util.log.LogFileOutput(CONFIG['features.log_file'])
    self._filter = nyx.util.log.LogFilters(initial_filters = CONFIG['features.log.regex'])

    self.set_pause_attr('_logged_events')

    self.last_content_height = 0         # height of the rendered content when last drawn
    self.scroll = 0

    self._last_update = -1               # time the content was last revised
    self._halt = False                   # terminates thread if true
    self._pause_condition = threading.Condition()
    self._lock = threading.RLock()

    # cached parameters (invalidated if arguments for them change)
    # last set of events we've drawn with

    self._last_logged_events = []

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

    # stop logging to NYX_LOGGER, adding its event backlog and future ones

    for event in NYX_LOGGER:
      self._register_nyx_event(event)

    NYX_LOGGER.emit = self._register_nyx_event

    # leaving last_content_height as being too low causes initialization problems

    self.last_content_height = len(self._logged_events)

  def set_duplicate_visability(self, is_visible):
    """
    Sets if duplicate log entries are collaped or expanded.

    Arguments:
      is_visible - if true all log entries are shown, otherwise they're
                   deduplicated
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
          user_input = user_input.replace(' ', '')  # strips spaces
          event_types = nyx.arguments.expand_events(user_input)

          try:
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
        nyx.popups.show_msg('Unable to save snapshot: %s' % exc.strerror, 2)

  def clear(self):
    """
    Clears the contents of the event log.
    """

    with self._lock:
      self._logged_events = nyx.util.log.LogGroup(CONFIG['cache.log_panel.size'], group_by_day = CONFIG['features.log.showDateDividers'])
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

    with self._lock:
      with open(path, 'w') as snapshot_file:
        try:
          for entry in reversed(self._logged_events):
            is_visible = self._filter.match(entry.display_message)

            if is_visible:
              snapshot_file.write(entry.display_message + '\n')
        except Exception as exc:
          raise IOError("unable to write to '%s': %s" % (path, exc))

  def handle_key(self, key):
    if key.is_scroll():
      page_height = self.get_preferred_size()[0] - 1
      new_scroll = ui_tools.get_scroll_position(key, self.scroll, page_height, self.last_content_height)

      if self.scroll != new_scroll:
        with self._lock:
          self.scroll = new_scroll
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
      # Provides menu to pick regular expression filters or adding new ones:
      # for syntax see: http://docs.python.org/library/re.html#regular-expression-syntax

      options = ['None'] + self._filter.latest_selections() + ['New...']
      old_selection = 0 if not self._filter.selection() else 1

      # does all activity under a curses lock to prevent redraws when adding
      # new filters

      with panel.CURSES_LOCK:
        selection = nyx.popups.show_menu('Log Filter:', options, old_selection)

        # applies new setting

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
    event_log = self.get_attr('_logged_events')

    with self._lock:
      self._last_logged_events, self._last_update = event_log, time.time()
      event_log = list(event_log)

      # draws the top label

      if self.is_title_visible():
        title_comp = list(nyx.util.log.condense_runlevels(*self._logged_event_types))

        if self._filter.selection():
          title_comp.append('filter: %s' % self._filter.selection())

        title_comp_str = join(title_comp, ', ', width - 10)
        title = 'Events (%s):' % title_comp_str if title_comp_str else 'Events:'

        self.addstr(0, 0, title, curses.A_STANDOUT)

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

      # determines if we have the minimum width to show date dividers

      show_daybreaks = width - divider_indent >= 3
      last_day = event_log[0].days_since() if event_log else 0

      for i in range(len(event_log)):
        entry = event_log[i]
        is_last = i == len(event_log) - 1

        if CONFIG['features.log.showDuplicateEntries']:
          duplicate_count = 0
        elif entry.is_duplicate:
          continue
        else:
          duplicate_count = len(entry.duplicates) if entry.duplicates else 0

        if not self._filter.match(entry.display_message):
          continue  # filter doesn't match log message - skip

        # checks if we should be showing a divider with the date

        if last_day != entry.days_since():
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

        # entry contents to be displayed, tuples of the form:
        # (msg, formatting, includeLinebreak)

        display_queue = []

        msg_comp = entry.display_message.split('\n')

        for i in range(len(msg_comp)):
          font = curses.A_BOLD if 'ERR' in entry.type else curses.A_NORMAL  # emphasizes ERR messages
          display_queue.append((msg_comp[i].strip(), (font, CONFIG['attr.log_color'].get(entry.type, 'white')), i != len(msg_comp) - 1))

        if duplicate_count:
          plural_label = 's' if duplicate_count > 1 else ''
          duplicate_msg = ' [%i duplicate%s hidden]' % (duplicate_count, plural_label)
          display_queue.append((duplicate_msg, duplicate_attr, False))

        # TODO: a fix made line_offset unused, and probably broke max_entries_per_line... not sure if we care

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
            line_count += 1
            cursor_location = msg_indent + 2  # indent following lines

          line_count += line_offset

        # if this is the last line and there's room, then draw the bottom of the divider

        if is_last and seen_first_date_divider:
          if line_count < height and show_daybreaks:
            self.addch(line_count, divider_indent, curses.ACS_LLCORNER, *divider_attr)
            self.hline(line_count, divider_indent + 1, width - divider_indent - 2, *divider_attr)
            self.addch(line_count, width - 1, curses.ACS_LRCORNER, *divider_attr)

          line_count += 1

        last_day = entry.days_since()

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

  def run(self):
    """
    Redraws the display, coalescing updates if events are rapidly logged (for
    instance running at the DEBUG runlevel) while also being immediately
    responsive if additions are less frequent.
    """

    last_day = nyx.util.log.days_since(time.time())  # used to determine if the date has changed

    while not self._halt:
      current_day = nyx.util.log.days_since(time.time())
      time_since_reset = time.time() - self._last_update
      max_log_update_rate = CONFIG['features.log.maxRefreshRate'] / 1000.0

      sleep_time = 0

      if (self._logged_events == self._last_logged_events and last_day == current_day) or self.is_paused():
        sleep_time = 5
      elif time_since_reset < max_log_update_rate:
        sleep_time = max(0.05, max_log_update_rate - time_since_reset)

      if sleep_time:
        with self._pause_condition:
          if not self._halt:
            self._pause_condition.wait(sleep_time)
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
        with self._pause_condition:
          self._pause_condition.notifyAll()
