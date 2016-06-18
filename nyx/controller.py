# Copyright 2009-2016, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Main interface loop for nyx, periodically redrawing the screen and issuing
user input to the proper panels.
"""

import time
import threading

import nyx.curses
import nyx.menu
import nyx.popups

import nyx.panel
import nyx.panel.config
import nyx.panel.connection
import nyx.panel.graph
import nyx.panel.header
import nyx.panel.log
import nyx.panel.torrc
import nyx.panel.interpretor

import stem

from stem.util import conf, log

from nyx.curses import BOLD
from nyx import tor_controller


NYX_CONTROLLER = None


def conf_handler(key, value):
  if key == 'features.redrawRate':
    return max(1, value)
  elif key == 'features.refreshRate':
    return max(0, value)


CONFIG = conf.config_dict('nyx', {
  'features.acsSupport': True,
  'features.panels.show.graph': True,
  'features.panels.show.log': True,
  'features.panels.show.connection': True,
  'features.panels.show.config': True,
  'features.panels.show.torrc': True,
  'features.panels.show.interpretor': True,
  'features.redrawRate': 5,
  'features.refreshRate': 5,
  'features.confirmQuit': True,
  'start_time': 0,
}, conf_handler)


def get_controller():
  """
  Provides the nyx controller instance.
  """

  return NYX_CONTROLLER


def show_message(message = None, *attr, **kwargs):
  return get_controller().header_panel().show_message(message, *attr, **kwargs)


def input_prompt(msg, initial_value = ''):
  """
  Prompts the user for input.

  :param str message: prompt for user input
  :param str initial_value: initial value of the prompt

  :returns: **str** with the user input, this is **None** if the prompt is
    canceled
  """

  header_panel = get_controller().header_panel()

  header_panel.show_message(msg)
  user_input = nyx.curses.str_input(len(msg), header_panel.get_height() - 1, initial_value)
  header_panel.show_message()

  return user_input


class Controller(object):
  """
  Tracks the global state of the interface
  """

  def __init__(self):
    """
    Creates a new controller instance. Panel lists are ordered as they appear,
    top to bottom on the page.
    """

    self._header_panel = nyx.panel.header.HeaderPanel()

    self._page_panels, first_page_panels = [], []

    if CONFIG['features.panels.show.graph']:
      first_page_panels.append(nyx.panel.graph.GraphPanel())

    if CONFIG['features.panels.show.log']:
      first_page_panels.append(nyx.panel.log.LogPanel())

    if first_page_panels:
      self._page_panels.append(first_page_panels)

    if CONFIG['features.panels.show.connection']:
      self._page_panels.append([nyx.panel.connection.ConnectionPanel()])

    if CONFIG['features.panels.show.config']:
      self._page_panels.append([nyx.panel.config.ConfigPanel()])

    if CONFIG['features.panels.show.torrc']:
      self._page_panels.append([nyx.panel.torrc.TorrcPanel()])

    if CONFIG['features.panels.show.interpretor']:
      self._page_panels.append([nyx.panel.interpretor.InterpretorPanel()])

    self.quit_signal = False
    self._page = 0
    self._paused = False
    self._pause_time = -1
    self._force_redraw = False
    self._last_drawn = 0

  def get_page_count(self):
    """
    Provides the number of pages the interface has. This may be zero if all
    page panels have been disabled.
    """

    return len(self._page_panels)

  def get_page(self):
    """
    Provides the number belonging to this page. Page numbers start at zero.
    """

    return self._page

  def set_page(self, page_number):
    """
    Sets the selected page, raising a ValueError if the page number is invalid.

    Arguments:
      page_number - page number to be selected
    """

    if page_number < 0 or page_number >= self.get_page_count():
      raise ValueError('Invalid page number: %i' % page_number)

    if page_number != self._page:
      self._page = page_number
      self._force_redraw = True
      self.header_panel().redraw()

  def next_page(self):
    """
    Increments the page number.
    """

    self.set_page((self._page + 1) % len(self._page_panels))

  def prev_page(self):
    """
    Decrements the page number.
    """

    self.set_page((self._page - 1) % len(self._page_panels))

  def is_paused(self):
    """
    Provides if the interface is configured to be paused or not.

    :returns: **True** if the interface is paused and **False** otherwise
    """

    return self._paused

  def set_paused(self, is_pause):
    """
    Pauses or unpauses the interface.

    :param bool is_pause: suspends the interface if **True**, resumes it
      otherwise
    """

    if is_pause != self._paused:
      if is_pause:
        self._pause_time = time.time()

      # Couple panels have their own pausing behavior. I'll later change this to
      # a listener approach or someting else that's less hacky.

      for panel_impl in self.get_all_panels():
        if isinstance(panel_impl, nyx.panel.graph.GraphPanel) or isinstance(panel_impl, nyx.panel.log.LogPanel):
          panel_impl.set_paused(is_pause)

      self._paused = is_pause

      for panel_impl in self.get_display_panels():
        panel_impl.redraw()

  def get_pause_time(self):
    """
    Provides the time that we were last paused, returning -1 if we've never
    been paused.

    :returns: **float** with the unix timestamp for when we were last paused
    """

    return self._pause_time

  def header_panel(self):
    return self._header_panel

  def get_display_panels(self, page_number = None):
    """
    Provides all panels belonging to a page and sticky content above it. This
    is ordered they way they are presented (top to bottom) on the page.

    Arguments:
      page_number    - page number of the panels to be returned, the current
                      page if None
    """

    return_page = self._page if page_number is None else page_number
    return list(self._page_panels[return_page]) if self._page_panels else []

  def get_daemon_panels(self):
    """
    Provides thread panels.
    """

    thread_panels = []

    for panel_impl in self.get_all_panels():
      if isinstance(panel_impl, threading.Thread):
        thread_panels.append(panel_impl)

    return thread_panels

  def get_all_panels(self):
    """
    Provides all panels in the interface.
    """

    all_panels = [self._header_panel]

    for page in self._page_panels:
      all_panels += list(page)

    return all_panels

  def redraw(self, force = True):
    """
    Redraws the displayed panel content.

    Arguments:
      force - redraws regardless of if it's needed if true, otherwise ignores
              the request when there aren't changes to be displayed
    """

    force |= self._force_redraw
    self._force_redraw = False

    current_time = time.time()

    if CONFIG['features.refreshRate'] != 0:
      if self._last_drawn + CONFIG['features.refreshRate'] <= current_time:
        force = True

    display_panels = [self.header_panel()] + self.get_display_panels()

    occupied_content = 0

    for panel_impl in display_panels:
      panel_impl.set_top(occupied_content)
      height = panel_impl.get_height()

      if height:
        occupied_content += height

    # apparently curses may cache display contents unless we explicitely
    # request a redraw here...
    # https://trac.torproject.org/projects/tor/ticket/2830#comment:9

    if force:
      with nyx.curses.raw_screen() as stdscr:
        stdscr.clear()

    for panel_impl in display_panels:
      panel_impl.redraw(force = force)

    if force:
      self._last_drawn = current_time

  def quit(self):
    self.quit_signal = True

  def halt(self):
    """
    Halts curses panels, providing back the thread doing so.
    """

    def halt_panels():
      for panel_impl in self.get_daemon_panels():
        panel_impl.stop()

      for panel_impl in self.get_daemon_panels():
        panel_impl.join()

    halt_thread = threading.Thread(target = halt_panels)
    halt_thread.start()
    return halt_thread


def start_nyx():
  """
  Main draw loop context.
  """

  global NYX_CONTROLLER

  NYX_CONTROLLER = Controller()
  control = get_controller()

  if not CONFIG['features.acsSupport']:
    nyx.curses.disable_acs()

  # provides notice about any unused config keys

  for key in sorted(conf.get_config('nyx').unused_keys()):
    if not key.startswith('msg.') and not key.startswith('dedup.'):
      log.notice('Unused configuration entry: %s' % key)

  # tells daemon panels to start

  for panel_impl in control.get_daemon_panels():
    panel_impl.start()

  # logs the initialization time

  log.info('nyx started (initialization took %0.3f seconds)' % (time.time() - CONFIG['start_time']))

  # main draw loop

  override_key = None      # uses this rather than waiting on user input

  while not control.quit_signal:
    display_panels = [control.header_panel()] + control.get_display_panels()

    # sets panel visability

    for panel_impl in control.get_all_panels():
      panel_impl.set_visible(panel_impl in display_panels)

    # redraws the interface if it's needed

    control.redraw(False)

    with nyx.curses.raw_screen() as stdscr:
      stdscr.refresh()

    # wait for user keyboard input until timeout, unless an override was set

    if override_key:
      key, override_key = override_key, None
    else:
      key = nyx.curses.key_input(CONFIG['features.redrawRate'])

    if key.match('right'):
      control.next_page()
    elif key.match('left'):
      control.prev_page()
    elif key.match('p'):
      control.set_paused(not control.is_paused())
    elif key.match('m'):
      nyx.menu.show_menu()
    elif key.match('q'):
      # provides prompt to confirm that nyx should exit

      if CONFIG['features.confirmQuit']:
        msg = 'Are you sure (q again to confirm)?'
        confirmation_key = show_message(msg, BOLD, max_wait = 30)
        quit_confirmed = confirmation_key.match('q')
      else:
        quit_confirmed = True

      if quit_confirmed:
        break
    elif key.match('x'):
      # provides prompt to confirm that nyx should issue a sighup

      msg = "This will reset Tor's internal state. Are you sure (x again to confirm)?"
      confirmation_key = show_message(msg, BOLD, max_wait = 30)

      if confirmation_key in (ord('x'), ord('X')):
        try:
          tor_controller().signal(stem.Signal.RELOAD)
        except IOError as exc:
          log.error('Error detected when reloading tor: %s' % exc.strerror)
    elif key.match('h'):
      override_key = nyx.popups.show_help()
    elif key == ord('l') - 96:
      # force redraw when ctrl+l is pressed
      control.redraw(True)
    else:
      for panel_impl in display_panels:
        for keybinding in panel_impl.key_handlers():
          keybinding.handle(key)
