"""
Main interface loop for nyx, periodically redrawing the screen and issuing
user input to the proper panels.
"""

import time
import curses
import threading

import nyx.menu.menu
import nyx.popups
import nyx.header_panel
import nyx.log_panel
import nyx.config_panel
import nyx.torrc_panel
import nyx.graph_panel
import nyx.connection_panel
import nyx.util.tracker

import stem

from stem.util import conf, log

from nyx.util import panel, tor_controller, ui_tools


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


class LabelPanel(panel.Panel):
  """
  Panel that just displays a single line of text.
  """

  def __init__(self, stdscr):
    panel.Panel.__init__(self, stdscr, 'msg', 0, height=1)
    self.msg_text = ''
    self.msg_attr = curses.A_NORMAL

  def set_message(self, msg, attr = None):
    """
    Sets the message being displayed by the panel.

    Arguments:
      msg  - string to be displayed
      attr - attribute for the label, normal text if undefined
    """

    if attr is None:
      attr = curses.A_NORMAL

    self.msg_text = msg
    self.msg_attr = attr

  def draw(self, width, height):
    self.addstr(0, 0, self.msg_text, self.msg_attr)


class Controller:
  """
  Tracks the global state of the interface
  """

  def __init__(self, stdscr):
    """
    Creates a new controller instance. Panel lists are ordered as they appear,
    top to bottom on the page.

    Arguments:
      stdscr       - curses window
    """

    self._screen = stdscr

    self._sticky_panels = [
      nyx.header_panel.HeaderPanel(stdscr),
      LabelPanel(stdscr),
    ]

    self._page_panels, first_page_panels = [], []

    if CONFIG['features.panels.show.graph']:
      first_page_panels.append(nyx.graph_panel.GraphPanel(stdscr))

    if CONFIG['features.panels.show.log']:
      first_page_panels.append(nyx.log_panel.LogPanel(stdscr))

    if first_page_panels:
      self._page_panels.append(first_page_panels)

    if CONFIG['features.panels.show.connection']:
      self._page_panels.append([nyx.connection_panel.ConnectionPanel(stdscr)])

    if CONFIG['features.panels.show.config']:
      self._page_panels.append([nyx.config_panel.ConfigPanel(stdscr)])

    if CONFIG['features.panels.show.torrc']:
      self._page_panels.append([nyx.torrc_panel.TorrcPanel(stdscr)])

    self.quit_signal = False
    self._page = 0
    self._is_paused = False
    self._force_redraw = False
    self._last_drawn = 0
    self.set_msg()  # initializes our control message

  def get_screen(self):
    """
    Provides our curses window.
    """

    return self._screen

  def key_input(self):
    """
    Gets keystroke from the user.
    """

    return panel.KeyInput(self.get_screen().getch())

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
      self.set_msg()

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
    True if the interface is paused, false otherwise.
    """

    return self._is_paused

  def set_paused(self, is_pause):
    """
    Sets the interface to be paused or unpaused.
    """

    if is_pause != self._is_paused:
      self._is_paused = is_pause
      self._force_redraw = True
      self.set_msg()

      for panel_impl in self.get_all_panels():
        panel_impl.set_paused(is_pause)

  def get_panel(self, name):
    """
    Provides the panel with the given identifier. This returns None if no such
    panel exists.

    Arguments:
      name - name of the panel to be fetched
    """

    for panel_impl in self.get_all_panels():
      if panel_impl.get_name() == name:
        return panel_impl

    return None

  def get_sticky_panels(self):
    """
    Provides the panels visibile at the top of every page.
    """

    return list(self._sticky_panels)

  def get_display_panels(self, page_number = None, include_sticky = True):
    """
    Provides all panels belonging to a page and sticky content above it. This
    is ordered they way they are presented (top to bottom) on the page.

    Arguments:
      page_number    - page number of the panels to be returned, the current
                      page if None
      include_sticky - includes sticky panels in the results if true
    """

    return_page = self._page if page_number is None else page_number

    if self._page_panels:
      if include_sticky:
        return self._sticky_panels + self._page_panels[return_page]
      else:
        return list(self._page_panels[return_page])
    else:
      return self._sticky_panels if include_sticky else []

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

    all_panels = list(self._sticky_panels)

    for page in self._page_panels:
      all_panels += list(page)

    return all_panels

  def redraw(self, force = True):
    """
    Redraws the displayed panel content.

    Arguments:
      force - redraws reguardless of if it's needed if true, otherwise ignores
              the request when there arne't changes to be displayed
    """

    force |= self._force_redraw
    self._force_redraw = False

    current_time = time.time()

    if CONFIG['features.refreshRate'] != 0:
      if self._last_drawn + CONFIG['features.refreshRate'] <= current_time:
        force = True

    display_panels = self.get_display_panels()

    occupied_content = 0

    for panel_impl in display_panels:
      panel_impl.set_top(occupied_content)
      occupied_content += panel_impl.get_height()

    # apparently curses may cache display contents unless we explicitely
    # request a redraw here...
    # https://trac.torproject.org/projects/tor/ticket/2830#comment:9

    if force:
      self._screen.clear()

    for panel_impl in display_panels:
      panel_impl.redraw(force)

    if force:
      self._last_drawn = current_time

  def set_msg(self, msg = None, attr = None, redraw = False):
    """
    Sets the message displayed in the interfaces control panel. This uses our
    default prompt if no arguments are provided.

    Arguments:
      msg    - string to be displayed
      attr   - attribute for the label, normal text if undefined
      redraw - redraws right away if true, otherwise redraws when display
               content is next normally drawn
    """

    if msg is None:
      msg = ''

      if attr is None:
        if not self._is_paused:
          msg = 'page %i / %i - m: menu, p: pause, h: page help, q: quit' % (self._page + 1, len(self._page_panels))
          attr = curses.A_NORMAL
        else:
          msg = 'Paused'
          attr = curses.A_STANDOUT

    control_panel = self.get_panel('msg')
    control_panel.set_message(msg, attr)

    if redraw:
      control_panel.redraw(True)
    else:
      self._force_redraw = True

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


def start_nyx(stdscr):
  """
  Main draw loop context.

  Arguments:
    stdscr    - curses window
  """

  global NYX_CONTROLLER

  NYX_CONTROLLER = Controller(stdscr)
  control = get_controller()

  if not CONFIG['features.acsSupport']:
    ui_tools.disable_acs()

  # provides notice about any unused config keys

  for key in sorted(conf.get_config('nyx').unused_keys()):
    if not key.startswith('msg.') and not key.startswith('dedup.'):
      log.notice('Unused configuration entry: %s' % key)

  # tells daemon panels to start

  for panel_impl in control.get_daemon_panels():
    panel_impl.start()

  # allows for background transparency

  try:
    curses.use_default_colors()
  except curses.error:
    pass

  # makes the cursor invisible

  try:
    curses.curs_set(0)
  except curses.error:
    pass

  # logs the initialization time

  log.info('nyx started (initialization took %0.3f seconds)' % (time.time() - CONFIG['start_time']))

  # main draw loop

  override_key = None      # uses this rather than waiting on user input

  while not control.quit_signal:
    display_panels = control.get_display_panels()

    # sets panel visability

    for panel_impl in control.get_all_panels():
      panel_impl.set_visible(panel_impl in display_panels)

    # redraws the interface if it's needed

    control.redraw(False)
    stdscr.refresh()

    # wait for user keyboard input until timeout, unless an override was set

    if override_key:
      key, override_key = override_key, None
    else:
      curses.halfdelay(CONFIG['features.redrawRate'] * 10)
      key = panel.KeyInput(stdscr.getch())

    if key.match('right'):
      control.next_page()
    elif key.match('left'):
      control.prev_page()
    elif key.match('p'):
      control.set_paused(not control.is_paused())
    elif key.match('m'):
      nyx.menu.menu.show_menu()
    elif key.match('q'):
      # provides prompt to confirm that nyx should exit

      if CONFIG['features.confirmQuit']:
        msg = 'Are you sure (q again to confirm)?'
        confirmation_key = nyx.popups.show_msg(msg, attr = curses.A_BOLD)
        quit_confirmed = confirmation_key.match('q')
      else:
        quit_confirmed = True

      if quit_confirmed:
        break
    elif key.match('x'):
      # provides prompt to confirm that nyx should issue a sighup

      msg = "This will reset Tor's internal state. Are you sure (x again to confirm)?"
      confirmation_key = nyx.popups.show_msg(msg, attr = curses.A_BOLD)

      if confirmation_key in (ord('x'), ord('X')):
        try:
          tor_controller().signal(stem.Signal.RELOAD)
        except IOError as exc:
          log.error('Error detected when reloading tor: %s' % exc.strerror)
    elif key.match('h'):
      override_key = nyx.popups.show_help_popup()
    elif key == ord('l') - 96:
      # force redraw when ctrl+l is pressed
      control.redraw(True)
    else:
      for panel_impl in display_panels:
        is_keystroke_consumed = panel_impl.handle_key(key)

        if is_keystroke_consumed:
          break