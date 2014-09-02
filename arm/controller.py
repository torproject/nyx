"""
Main interface loop for arm, periodically redrawing the screen and issuing
user input to the proper panels.
"""

import os
import time
import curses
import threading

import arm.arguments
import arm.menu.menu
import arm.popups
import arm.header_panel
import arm.log_panel
import arm.config_panel
import arm.torrc_panel
import arm.graphing.graph_panel
import arm.graphing.bandwidth_stats
import arm.graphing.conn_stats
import arm.graphing.resource_stats
import arm.connections.conn_panel
import arm.util.tracker

import stem

from stem.control import State

from arm.util import panel, tor_config, tor_controller, ui_tools

from stem.util import conf, enum, log, system

ARM_CONTROLLER = None


def conf_handler(key, value):
  if key == "features.redrawRate":
    return max(1, value)
  elif key == "features.refreshRate":
    return max(0, value)


CONFIG = conf.config_dict("arm", {
  "startup.events": "N3",
  "startup.data_directory": "~/.arm",
  "features.acsSupport": True,
  "features.panels.show.graph": True,
  "features.panels.show.log": True,
  "features.panels.show.connection": True,
  "features.panels.show.config": True,
  "features.panels.show.torrc": True,
  "features.redrawRate": 5,
  "features.refreshRate": 5,
  "features.confirmQuit": True,
  "features.graph.type": 1,
  "features.graph.bw.prepopulate": True,
  "start_time": 0,
}, conf_handler)

GraphStat = enum.Enum("BANDWIDTH", "CONNECTIONS", "SYSTEM_RESOURCES")

# maps 'features.graph.type' config values to the initial types

GRAPH_INIT_STATS = {1: GraphStat.BANDWIDTH, 2: GraphStat.CONNECTIONS, 3: GraphStat.SYSTEM_RESOURCES}


def get_controller():
  """
  Provides the arm controller instance.
  """

  return ARM_CONTROLLER


def stop_controller():
  """
  Halts our Controller, providing back the thread doing so.
  """

  def halt_controller():
    control = get_controller()

    if control:
      for panel_impl in control.get_daemon_panels():
        panel_impl.stop()

      for panel_impl in control.get_daemon_panels():
        panel_impl.join()

  halt_thread = threading.Thread(target = halt_controller)
  halt_thread.start()
  return halt_thread


def init_controller(stdscr, start_time):
  """
  Spawns the controller, and related panels for it.

  Arguments:
    stdscr - curses window
  """

  global ARM_CONTROLLER

  # initializes the panels

  sticky_panels = [
    arm.header_panel.HeaderPanel(stdscr, start_time),
    LabelPanel(stdscr),
  ]

  page_panels, first_page_panels = [], []

  # first page: graph and log
  if CONFIG["features.panels.show.graph"]:
    first_page_panels.append(arm.graphing.graph_panel.GraphPanel(stdscr))

  if CONFIG["features.panels.show.log"]:
    expanded_events = arm.arguments.expand_events(CONFIG["startup.events"])
    first_page_panels.append(arm.log_panel.LogPanel(stdscr, expanded_events))

  if first_page_panels:
    page_panels.append(first_page_panels)

  # second page: connections
  if CONFIG["features.panels.show.connection"]:
    page_panels.append([arm.connections.conn_panel.ConnectionPanel(stdscr)])

    # The DisableDebuggerAttachment will prevent our connection panel from really
    # functioning. It'll have circuits, but little else. If this is the case then
    # notify the user and tell them what they can do to fix it.

    controller = tor_controller()

    if controller.get_conf("DisableDebuggerAttachment", None) == "1":
      log.notice("Tor is preventing system utilities like netstat and lsof from working. This means that arm can't provide you with connection information. You can change this by adding 'DisableDebuggerAttachment 0' to your torrc and restarting tor. For more information see...\nhttps://trac.torproject.org/3313")
      arm.util.tracker.get_connection_tracker().set_paused(True)
    else:
      # Configures connection resoultions. This is paused/unpaused according to
      # if Tor's connected or not.

      controller.add_status_listener(conn_reset_listener)

      tor_pid = controller.get_pid(None)

      if tor_pid:
        # use the tor pid to help narrow connection results
        tor_cmd = system.name_by_pid(tor_pid)

        if tor_cmd is None:
          tor_cmd = "tor"

        resolver = arm.util.tracker.get_connection_tracker()
        log.info("Operating System: %s, Connection Resolvers: %s" % (os.uname()[0], ", ".join(resolver._resolvers)))
        resolver.start()
      else:
        # constructs singleton resolver and, if tor isn't connected, initizes
        # it to be paused

        arm.util.tracker.get_connection_tracker().set_paused(not controller.is_alive())

  # third page: config

  if CONFIG["features.panels.show.config"]:
    page_panels.append([arm.config_panel.ConfigPanel(stdscr, arm.config_panel.State.TOR)])

  # fourth page: torrc

  if CONFIG["features.panels.show.torrc"]:
    page_panels.append([arm.torrc_panel.TorrcPanel(stdscr, arm.torrc_panel.Config.TORRC)])

  # initializes the controller

  ARM_CONTROLLER = Controller(stdscr, sticky_panels, page_panels)

  # additional configuration for the graph panel

  graph_panel = ARM_CONTROLLER.get_panel("graph")

  if graph_panel:
    # statistical monitors for graph

    bw_stats = arm.graphing.bandwidth_stats.BandwidthStats()
    graph_panel.add_stats(GraphStat.BANDWIDTH, bw_stats)
    graph_panel.add_stats(GraphStat.SYSTEM_RESOURCES, arm.graphing.resource_stats.ResourceStats())

    if CONFIG["features.panels.show.connection"]:
      graph_panel.add_stats(GraphStat.CONNECTIONS, arm.graphing.conn_stats.ConnStats())

    # sets graph based on config parameter

    try:
      initial_stats = GRAPH_INIT_STATS.get(CONFIG["features.graph.type"])
      graph_panel.set_stats(initial_stats)
    except ValueError:
      pass  # invalid stats, maybe connections when lookups are disabled

    # prepopulates bandwidth values from state file

    if CONFIG["features.graph.bw.prepopulate"] and tor_controller().is_alive():
      is_successful = bw_stats.prepopulate_from_state()

      if is_successful:
        graph_panel.update_interval = 4


class LabelPanel(panel.Panel):
  """
  Panel that just displays a single line of text.
  """

  def __init__(self, stdscr):
    panel.Panel.__init__(self, stdscr, "msg", 0, height=1)
    self.msg_text = ""
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

  def __init__(self, stdscr, sticky_panels, page_panels):
    """
    Creates a new controller instance. Panel lists are ordered as they appear,
    top to bottom on the page.

    Arguments:
      stdscr       - curses window
      sticky_panels - panels shown at the top of each page
      page_panels   - list of pages, each being a list of the panels on it
    """

    self._screen = stdscr
    self._sticky_panels = sticky_panels
    self._page_panels = page_panels
    self._page = 0
    self._is_paused = False
    self._force_redraw = False
    self._is_done = False
    self._last_drawn = 0
    self.set_msg()  # initializes our control message

  def get_screen(self):
    """
    Provides our curses window.
    """

    return self._screen

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
      raise ValueError("Invalid page number: %i" % page_number)

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

    if CONFIG["features.refreshRate"] != 0:
      if self._last_drawn + CONFIG["features.refreshRate"] <= current_time:
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

  def request_redraw(self):
    """
    Requests that all content is redrawn when the interface is next rendered.
    """

    self._force_redraw = True

  def get_last_redraw_time(self):
    """
    Provides the time when the content was last redrawn, zero if the content
    has never been drawn.
    """

    return self._last_drawn

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
      msg = ""

      if attr is None:
        if not self._is_paused:
          msg = "page %i / %i - m: menu, p: pause, h: page help, q: quit" % (self._page + 1, len(self._page_panels))
          attr = curses.A_NORMAL
        else:
          msg = "Paused"
          attr = curses.A_STANDOUT

    control_panel = self.get_panel("msg")
    control_panel.set_message(msg, attr)

    if redraw:
      control_panel.redraw(True)
    else:
      self._force_redraw = True

  def get_data_directory(self):
    """
    Provides the path where arm's resources are being placed. The path ends
    with a slash and is created if it doesn't already exist.
    """

    data_dir = os.path.expanduser(CONFIG["startup.data_directory"])

    if not data_dir.endswith("/"):
      data_dir += "/"

    if not os.path.exists(data_dir):
      os.makedirs(data_dir)

    return data_dir

  def is_done(self):
    """
    True if arm should be terminated, false otherwise.
    """

    return self._is_done

  def quit(self):
    """
    Terminates arm after the input is processed. Optionally if we're connected
    to a arm generated tor instance then this may check if that should be shut
    down too.
    """

    self._is_done = True

    # check if the torrc has a "ARM_SHUTDOWN" comment flag, if so then shut
    # down the instance

    is_shutdown_flag_present = False
    torrc_contents = tor_config.get_torrc().get_contents()

    if torrc_contents:
      for line in torrc_contents:
        if "# ARM_SHUTDOWN" in line:
          is_shutdown_flag_present = True
          break

    if is_shutdown_flag_present:
      try:
        tor_controller().close()
      except IOError as exc:
        arm.popups.show_msg(str(exc), 3, curses.A_BOLD)


def heartbeat_check(is_unresponsive):
  """
  Logs if its been ten seconds since the last BW event.

  Arguments:
    is_unresponsive - flag for if we've indicated to be responsive or not
  """

  controller = tor_controller()
  last_heartbeat = controller.get_latest_heartbeat()

  if controller.is_alive():
    if not is_unresponsive and (time.time() - last_heartbeat) >= 10:
      is_unresponsive = True
      log.notice("Relay unresponsive (last heartbeat: %s)" % time.ctime(last_heartbeat))
    elif is_unresponsive and (time.time() - last_heartbeat) < 10:
      # really shouldn't happen (meant Tor froze for a bit)
      is_unresponsive = False
      log.notice("Relay resumed")

  return is_unresponsive


def conn_reset_listener(controller, event_type, _):
  """
  Pauses connection resolution when tor's shut down, and resumes with the new
  pid if started again.
  """

  resolver = arm.util.tracker.get_connection_tracker()

  if resolver.is_alive():
    resolver.set_paused(event_type == State.CLOSED)

    if event_type in (State.INIT, State.RESET):
      # Reload the torrc contents. If the torrc panel is present then it will
      # do this instead since it wants to do validation and redraw _after_ the
      # new contents are loaded.

      if get_controller().get_panel("torrc") is None:
        tor_config.get_torrc().load(True)


def start_arm(stdscr):
  """
  Main draw loop context.

  Arguments:
    stdscr    - curses window
  """

  init_controller(stdscr, CONFIG['start_time'])
  control = get_controller()

  if not CONFIG["features.acsSupport"]:
    ui_tools.disable_acs()

  # provides notice about any unused config keys

  for key in conf.get_config("arm").unused_keys():
    log.notice("Unused configuration entry: %s" % key)

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

  log.info("arm started (initialization took %0.3f seconds)" % (time.time() - CONFIG['start_time']))

  # main draw loop

  override_key = None      # uses this rather than waiting on user input
  is_unresponsive = False  # flag for heartbeat responsiveness check

  while not control.is_done():
    display_panels = control.get_display_panels()
    is_unresponsive = heartbeat_check(is_unresponsive)

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
      curses.halfdelay(CONFIG["features.redrawRate"] * 10)
      key = stdscr.getch()

    if key == curses.KEY_RIGHT:
      control.next_page()
    elif key == curses.KEY_LEFT:
      control.prev_page()
    elif key == ord('p') or key == ord('P'):
      control.set_paused(not control.is_paused())
    elif key == ord('m') or key == ord('M'):
      arm.menu.menu.show_menu()
    elif key == ord('q') or key == ord('Q'):
      # provides prompt to confirm that arm should exit

      if CONFIG["features.confirmQuit"]:
        msg = "Are you sure (q again to confirm)?"
        confirmation_key = arm.popups.show_msg(msg, attr = curses.A_BOLD)
        quit_confirmed = confirmation_key in (ord('q'), ord('Q'))
      else:
        quit_confirmed = True

      if quit_confirmed:
        control.quit()
    elif key == ord('x') or key == ord('X'):
      # provides prompt to confirm that arm should issue a sighup

      msg = "This will reset Tor's internal state. Are you sure (x again to confirm)?"
      confirmation_key = arm.popups.show_msg(msg, attr = curses.A_BOLD)

      if confirmation_key in (ord('x'), ord('X')):
        try:
          tor_controller().signal(stem.Signal.RELOAD)
        except IOError as exc:
          log.error("Error detected when reloading tor: %s" % exc.strerror)
    elif key == ord('h') or key == ord('H'):
      override_key = arm.popups.show_help_popup()
    elif key == ord('l') - 96:
      # force redraw when ctrl+l is pressed
      control.redraw(True)
    else:
      for panel_impl in display_panels:
        is_keystroke_consumed = panel_impl.handle_key(key)

        if is_keystroke_consumed:
          break
