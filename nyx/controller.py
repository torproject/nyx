# Copyright 2009-2016, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Main interface loop for nyx, periodically redrawing the screen and issuing
user input to the proper panels.
"""

import time

import nyx.curses
import nyx.menu
import nyx.popups

import nyx.panel
import nyx.panel.config
import nyx.panel.connection
import nyx.panel.graph
import nyx.panel.header
import nyx.panel.interpreter
import nyx.panel.log
import nyx.panel.torrc

import stem

from stem.util import conf, log

from nyx.curses import BOLD
from nyx import Interface, tor_controller


NYX_CONTROLLER = None


def conf_handler(key, value):
  if key == 'features.redrawRate':
    return max(1, value)


CONFIG = conf.config_dict('nyx', {
  'features.acsSupport': True,
  'features.panels.show.graph': True,
  'features.panels.show.log': True,
  'features.panels.show.connection': True,
  'features.panels.show.config': True,
  'features.panels.show.torrc': True,
  'features.panels.show.interpreter': True,
  'features.redrawRate': 5,
  'features.confirmQuit': True,
  'start_time': 0,
}, conf_handler)


def get_controller():
  """
  Provides the nyx controller instance.
  """

  if NYX_CONTROLLER is None:
    Controller()  # constructor sets NYX_CONTROLLER

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


class Controller(Interface):
  """
  Tracks the global state of the interface
  """

  def __init__(self):
    """
    Creates a new controller instance. Panel lists are ordered as they appear,
    top to bottom on the page.
    """

    global NYX_CONTROLLER
    super(Controller, self).__init__()

    self._page_panels = []
    self._header_panel = None

    NYX_CONTROLLER = self

    self._header_panel = nyx.panel.header.HeaderPanel()
    first_page_panels = []

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

    if CONFIG['features.panels.show.interpreter']:
      self._page_panels.append([nyx.panel.interpreter.InterpreterPanel()])

  def header_panel(self):
    return self._header_panel


def start_nyx():
  """
  Main draw loop context.
  """

  interface = get_controller()

  if not CONFIG['features.acsSupport']:
    nyx.curses.disable_acs()

  # provides notice about any unused config keys

  for key in sorted(conf.get_config('nyx').unused_keys()):
    if not key.startswith('msg.') and not key.startswith('dedup.'):
      log.notice('Unused configuration entry: %s' % key)

  # tells daemon panels to start

  for panel in interface.get_daemon_panels():
    panel.start()

  # logs the initialization time

  log.info('nyx started (initialization took %0.3f seconds)' % (time.time() - CONFIG['start_time']))

  # main draw loop

  override_key = None      # uses this rather than waiting on user input

  while not interface._quit:
    display_panels = [interface.header_panel()] + interface.get_page_panels()

    # sets panel visability

    for panel in interface:
      panel.set_visible(panel in display_panels)

    interface.redraw()

    with nyx.curses.raw_screen() as stdscr:
      stdscr.refresh()

    # wait for user keyboard input until timeout, unless an override was set

    if override_key:
      key, override_key = override_key, None
    else:
      key = nyx.curses.key_input(CONFIG['features.redrawRate'])

    if key.match('right'):
      interface.set_page((interface.get_page() + 1) % interface.page_count())
    elif key.match('left'):
      interface.set_page((interface.get_page() - 1) % interface.page_count())
    elif key.match('p'):
      interface.set_paused(not interface.is_paused())
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

      if confirmation_key.match('x'):
        try:
          tor_controller().signal(stem.Signal.RELOAD)
        except stem.ControllerError as exc:
          log.error('Error detected when reloading tor: %s' % exc.strerror)
    elif key.match('h'):
      override_key = nyx.popups.show_help()
    else:
      for panel in display_panels:
        for keybinding in panel.key_handlers():
          keybinding.handle(key)
