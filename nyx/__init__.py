# Copyright 2009-2016, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Tor curses monitoring application.

::

  nyx_interface - nyx interface singleton
  tor_controller - tor connection singleton

  show_message - shows a message to the user
  input_prompt - prompts the user for text input
  init_controller - initializes our connection to tor
  expand_path - expands path with respect to our chroot
  join - joins a series of strings up to a set length
  msg - string from our configuration

  Interface - overall nyx interface
    |- get_page - page we're showing
    |- set_page - sets the page we're showing
    |- page_count - pages within our interface
    |
    |- header_panel - provides the header panel
    |- page_panels - provides panels on a page
    |
    |- is_paused - checks if the interface is paused
    |- set_paused - sets paused state
    |
    |- redraw - renders our content
    |- quit - quits our application
    +- halt - stops daemon panels
"""

import distutils.spawn
import os
import sys
import threading
import time

import stem
import stem.connection
import stem.control
import stem.util.conf
import stem.util.log
import stem.util.system

__version__ = '1.4.6-dev'
__release_date__ = 'April 28, 2011'
__author__ = 'Damian Johnson'
__contact__ = 'atagar@torproject.org'
__url__ = 'http://www.atagar.com/arm/'
__license__ = 'GPLv3'

__all__ = [
  'arguments',
  'controller',
  'curses',
  'log',
  'menu',
  'panel',
  'popups',
  'starter',
  'tracker',
]


def conf_handler(key, value):
  if key == 'features.redrawRate':
    return max(1, value)


CONFIG = stem.util.conf.config_dict('nyx', {
  'features.confirmQuit': True,
  'features.panels.show.graph': True,
  'features.panels.show.log': True,
  'features.panels.show.connection': True,
  'features.panels.show.config': True,
  'features.panels.show.torrc': True,
  'features.panels.show.interpreter': True,
  'features.redrawRate': 5,
  'start_time': 0,
}, conf_handler)

NYX_INTERFACE = None
TOR_CONTROLLER = None
BASE_DIR = os.path.sep.join(__file__.split(os.path.sep)[:-1])
DEFAULT_DATA_DIR = os.path.expanduser('~/.nyx')
TESTING = False

# technically can change but we use this query a *lot* so needs to be cached

stem.control.CACHEABLE_GETINFO_PARAMS = list(stem.control.CACHEABLE_GETINFO_PARAMS) + ['address']

# disable trace level messages about cache hits

stem.control.LOG_CACHE_FETCHES = False

try:
  uses_settings = stem.util.conf.uses_settings('nyx', os.path.join(BASE_DIR, 'settings'), lazy_load = False)
except IOError as exc:
  print("Unable to load nyx's internal configurations: %s" % exc)
  sys.exit(1)


def main():
  try:
    nyx.starter.main()
  except ImportError as exc:
    if exc.message == 'No module named stem':
      if distutils.spawn.find_executable('pip') is not None:
        advice = ", try running 'sudo pip install stem'"
      elif distutils.spawn.find_executable('apt-get') is not None:
        advice = ", try running 'sudo apt-get install python-stem'"
      else:
        advice = ', you can find it at https://stem.torproject.org/download.html'

      print('nyx requires stem' + advice)
    else:
      print('Unable to start nyx: %s' % exc)

    sys.exit(1)


def draw_loop():
  interface = nyx_interface()
  next_key = None  # use this as the next user input

  stem.util.log.info(msg('startup_time', start_time = time.time() - CONFIG['start_time']))

  while not interface._quit:
    interface.redraw()

    with nyx.curses.raw_screen() as stdscr:
      stdscr.refresh()

    if next_key:
      key, next_key = next_key, None
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
      if CONFIG['features.confirmQuit']:
        confirmation_key = show_message(msg('confirm_quit'), nyx.curses.BOLD, max_wait = 30)

        if not confirmation_key.match('q'):
          continue

      break
    elif key.match('x'):
      confirmation_key = show_message(msg('confirm_reload'), nyx.curses.BOLD, max_wait = 30)

      if confirmation_key.match('x'):
        try:
          tor_controller().signal(stem.Signal.RELOAD)
        except stem.ControllerError as exc:
          stem.util.log.error('Error detected when reloading tor: %s' % exc.strerror)
    elif key.match('h'):
      next_key = nyx.popups.show_help()
    else:
      for panel in interface.page_panels():
        for keybinding in panel.key_handlers():
          keybinding.handle(key)


def nyx_interface():
  """
  Singleton controller for our interface.

  :returns: :class:`~nyx.Interface` controller
  """

  if NYX_INTERFACE is None:
    Interface()  # constructor sets NYX_INTERFACE

  return NYX_INTERFACE


def tor_controller():
  """
  Singleton for getting our tor controller connection.

  :returns: :class:`~stem.control.Controller` nyx is using
  """

  return TOR_CONTROLLER


def show_message(message = None, *attr, **kwargs):
  """
  Shows a message in our header.

  :param str message: message to be shown
  """

  return nyx_interface().header_panel().show_message(message, *attr, **kwargs)


def input_prompt(msg, initial_value = ''):
  """
  Prompts the user for input.

  :param str message: prompt for user input
  :param str initial_value: initial value of the prompt

  :returns: **str** with the user input, this is **None** if the prompt is
    canceled
  """

  header_panel = nyx_interface().header_panel()

  header_panel.show_message(msg)
  user_input = nyx.curses.str_input(len(msg), header_panel.get_height() - 1, initial_value)
  header_panel.show_message()

  return user_input


def init_controller(*args, **kwargs):
  """
  Sets the Controller used by nyx. This is a passthrough for Stem's
  :func:`~stem.connection.connect` function.

  :returns: :class:`~stem.control.Controller` nyx is using
  """

  global TOR_CONTROLLER
  TOR_CONTROLLER = stem.connection.connect(*args, **kwargs)
  return TOR_CONTROLLER


@uses_settings
def expand_path(path, config):
  """
  Expands relative paths and include our chroot if one was set.

  :param str path: path to be expanded

  :returns: **str** with the expanded path
  """

  if path is None:
    return None

  try:
    chroot = config.get('tor.chroot', '')
    tor_cwd = stem.util.system.cwd(tor_controller().get_pid(None))
    return chroot + stem.util.system.expand_path(path, tor_cwd)
  except IOError as exc:
    stem.util.log.info('Unable to expand a relative path (%s): %s' % (path, exc))
    return path


def join(entries, joiner = ' ', size = None):
  """
  Joins a series of strings similar to str.join(), but only up to a given size.
  This returns an empty string if none of the entries will fit. For example...

    >>> join(['This', 'is', 'a', 'looooong', 'message'], size = 18)
    'This is a looooong'

    >>> join(['This', 'is', 'a', 'looooong', 'message'], size = 17)
    'This is a'

    >>> join(['This', 'is', 'a', 'looooong', 'message'], size = 2)
    ''

  :param list entries: strings to be joined
  :param str joiner: strings to join the entries with
  :param int size: maximum length the result can be, there's no length
    limitation if **None**

  :returns: **str** of the joined entries up to the given length
  """

  if size is None:
    return joiner.join(entries)

  result = ''

  for entry in entries:
    new_result = joiner.join((result, entry)) if result else entry

    if len(new_result) > size:
      break
    else:
      result = new_result

  return result


@uses_settings
def msg(message, config, **attr):
  """
  Provides the given message.

  :param str message: message handle to log
  :param dict attr: attributes to format the message with

  :returns: **str** that was requested
  """

  try:
    return config.get('msg.%s' % message).format(**attr)
  except:
    msg = 'BUG: We attempted to use an undefined string resource (%s)' % message

    if TESTING:
      raise ValueError(msg)

    stem.util.log.notice(msg)
    return ''


class Interface(object):
  """
  Overall state of the nyx interface.
  """

  def __init__(self):
    global NYX_INTERFACE

    self._page = 0
    self._page_panels = []
    self._header_panel = None
    self._paused = False
    self._quit = False

    NYX_INTERFACE = self

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

    visible_panels = self.page_panels()

    for panel in self:
      panel.set_visible(panel in visible_panels)

      if isinstance(panel, nyx.panel.DaemonPanel):
        panel.start()

  def get_page(self):
    """
    Provides the page we're showing.

    :return: **int** of the page we're showing
    """

    return self._page

  def set_page(self, page_number):
    """
    Sets the selected page.

    :param int page_number: page to be shown

    :raises: **ValueError** if the page_number is invalid
    """

    if page_number < 0 or page_number >= self.page_count():
      raise ValueError('Invalid page number: %i' % page_number)

    if page_number != self._page:
      self._page = page_number
      self.header_panel().redraw()

      visible_panels = self.page_panels()

      for panel in self:
        panel.set_visible(panel in visible_panels)

  def page_count(self):
    """
    Provides the number of pages the interface has.

    :returns: **int** number of pages in the interface
    """

    return len(self._page_panels)

  def header_panel(self):
    """
    Provides our interface's header.

    :returns: :class:`~nyx.panel.header.HeaderPanel` of our interface
    """

    return self._header_panel

  def page_panels(self, page_number = None):
    """
    Provides panels belonging to a page, ordered top to bottom.

    :param int page_number: page to provide the panels of, current page if
      **None**

    :returns: **list** of panels on that page
    """

    page_number = self._page if page_number is None else page_number
    return [self._header_panel] + self._page_panels[page_number]

  def is_paused(self):
    """
    Checks if the interface is configured to be paused.

    :returns: **True** if the interface is paused, **False** otherwise
    """

    return self._paused

  def set_paused(self, is_pause):
    """
    Pauses or unpauses the interface.

    :param bool is_pause: suspends the interface if **True**, resumes it
      otherwise
    """

    if is_pause != self._paused:
      self._paused = is_pause

      for panel in self:
        panel.set_paused(is_pause)

      for panel in self.page_panels():
        panel.redraw()

  def redraw(self, force = True):
    """
    Renders our displayed content.

    :param bool force: if **False** only redraws content if resized
    """

    # Curses may overly cache content without clearing here...
    # https://trac.torproject.org/projects/tor/ticket/2830#comment:9

    if force:
      with nyx.curses.raw_screen() as stdscr:
        stdscr.clear()

    occupied = 0

    for panel in self.page_panels():
      panel.redraw(force = force, top = occupied)
      occupied += panel.get_height()

  def quit(self):
    """
    Quits our application.
    """

    self._quit = True

  def halt(self):
    """
    Stops curses panels in our interface.

    :returns: **threading.Thread** terminating daemons
    """

    def halt_panels():
      daemons = [panel for panel in self if isinstance(panel, nyx.panel.DaemonPanel)]

      for panel in daemons:
        panel.stop()

      for panel in daemons:
        panel.join()

    halt_thread = threading.Thread(target = halt_panels)
    halt_thread.start()
    return halt_thread

  def __iter__(self):
    yield self._header_panel

    for page in self._page_panels:
      for panel in page:
        yield panel


import nyx.curses
import nyx.menu
import nyx.panel
import nyx.panel.config
import nyx.panel.connection
import nyx.panel.graph
import nyx.panel.header
import nyx.panel.interpreter
import nyx.panel.log
import nyx.panel.torrc
import nyx.popups
import nyx.starter
