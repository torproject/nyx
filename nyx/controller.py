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

import stem

from stem.util import conf, log

from nyx.curses import BOLD
from nyx import nyx_interface, tor_controller, show_message


def conf_handler(key, value):
  if key == 'features.redrawRate':
    return max(1, value)


CONFIG = conf.config_dict('nyx', {
  'features.redrawRate': 5,
  'features.confirmQuit': True,
  'start_time': 0,
}, conf_handler)


def start_nyx():
  """
  Main draw loop context.
  """

  # provides notice about any unused config keys

  for key in sorted(conf.get_config('nyx').unused_keys()):
    if not key.startswith('msg.') and not key.startswith('dedup.'):
      log.notice('Unused configuration entry: %s' % key)

  interface = nyx_interface()

  # logs the initialization time

  log.info('nyx started (initialization took %0.3f seconds)' % (time.time() - CONFIG['start_time']))

  # main draw loop

  override_key = None      # uses this rather than waiting on user input

  while not interface._quit:
    display_panels = [interface.header_panel()] + interface.page_panels()

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
