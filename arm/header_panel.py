"""
Top panel for every page, containing basic system and tor related information.
This expands the information it presents to two columns if there's room
available.
"""

import os
import time
import curses
import threading

import stem
import stem.util.proc
import stem.util.str_tools
import stem.util.system

import arm.controller
import arm.popups
import arm.starter
import arm.util.tracker

from stem.control import Listener
from stem.util import conf, log, str_tools

from util import panel, tor_controller

MIN_DUAL_COL_WIDTH = 141  # minimum width where we'll show two columns
SHOW_FD_THRESHOLD = 60  # show file descriptor usage if usage is over this percentage
UPDATE_RATE = 5  # rate in seconds at which we refresh

CONFIG = conf.config_dict('arm', {
  'attr.flag_colors': {},
  'attr.version_status_colors': {},
})


class HeaderPanel(panel.Panel, threading.Thread):
  """
  Top area containing tor settings and system information.
  """

  def __init__(self, stdscr, start_time):
    panel.Panel.__init__(self, stdscr, 'header', 0)
    threading.Thread.__init__(self)
    self.setDaemon(True)

    self._vals = Sampling()

    self._pause_condition = threading.Condition()
    self._halt = False  # terminates thread if true

    tor_controller().add_status_listener(self.reset_listener)

  def is_wide(self, width = None):
    """
    True if we should show two columns of information, False otherwise.
    """

    if width is None:
      width = self.get_parent().getmaxyx()[1]

    return width >= MIN_DUAL_COL_WIDTH

  def get_height(self):
    """
    Provides the height of the content, which is dynamically determined by the
    panel's maximum width.
    """

    if self._vals.is_relay:
      return 4 if self.is_wide() else 6
    else:
      return 3 if self.is_wide() else 4

  def send_newnym(self):
    """
    Requests a new identity and provides a visual queue.
    """

    tor_controller().signal(stem.Signal.NEWNYM)

    # If we're wide then the newnym label in this panel will give an
    # indication that the signal was sent. Otherwise use a msg.

    if not self.is_wide():
      arm.popups.show_msg('Requesting a new identity', 1)

  def handle_key(self, key):
    is_keystroke_consumed = True

    if key in (ord('n'), ord('N')) and tor_controller().is_newnym_available():
      self.send_newnym()
    elif key in (ord('r'), ord('R')) and not self._vals.is_connected:
      # oldSocket = tor_tools.get_conn().get_controller().get_socket()
      #
      # controller = None
      # allowPortConnection, allowSocketConnection, _ = starter.allowConnectionTypes()
      #
      # if os.path.exists(CONFIG["startup.interface.socket"]) and allowSocketConnection:
      #   try:
      #     # TODO: um... what about passwords?
      #     controller = Controller.from_socket_file(CONFIG["startup.interface.socket"])
      #     controller.authenticate()
      #   except (IOError, stem.SocketError), exc:
      #     controller = None
      #
      #     if not allowPortConnection:
      #       arm.popups.show_msg("Unable to reconnect (%s)" % exc, 3)
      # elif not allowPortConnection:
      #   arm.popups.show_msg("Unable to reconnect (socket '%s' doesn't exist)" % CONFIG["startup.interface.socket"], 3)
      #
      # if not controller and allowPortConnection:
      #   # TODO: This has diverged from starter.py's connection, for instance it
      #   # doesn't account for relative cookie paths or multiple authentication
      #   # methods. We can't use the starter.py's connection function directly
      #   # due to password prompts, but we could certainly make this mess more
      #   # manageable.
      #
      #   try:
      #     ctlAddr, ctl_port = CONFIG["startup.interface.ip_address"], CONFIG["startup.interface.port"]
      #     controller = Controller.from_port(ctlAddr, ctl_port)
      #
      #     try:
      #       controller.authenticate()
      #     except stem.connection.MissingPassword:
      #       controller.authenticate(authValue) # already got the password above
      #   except Exception, exc:
      #     controller = None
      #
      # if controller:
      #   tor_tools.get_conn().init(controller)
      #   log.notice("Reconnected to Tor's control port")
      #   arm.popups.show_msg("Tor reconnected", 1)

      pass
    else:
      is_keystroke_consumed = False

    return is_keystroke_consumed

  def draw(self, width, height):
    vals = self._vals  # local reference to avoid concurrency concerns
    is_wide = self.is_wide(width)

    # space available for content

    left_width = max(width / 2, 77) if is_wide else width
    right_width = width - left_width

    self._draw_platform_section(0, 0, left_width, vals)

    if vals.is_connected:
      self._draw_ports_section(0, 1, left_width, vals)
    else:
      self._draw_disconnected(0, 1, left_width, vals)

    if is_wide:
      self._draw_resource_usage(left_width, 0, right_width, vals)
    else:
      self._draw_resource_usage(0, 2, left_width, vals)

    if vals.is_relay:
      if is_wide:
        self._draw_fingerprint_and_fd_usage(left_width, 1, right_width, vals)
        self._draw_flags(0, 2, left_width, vals)
        self._draw_exit_policy(left_width, 2, right_width, vals)
      else:
        self._draw_fingerprint_and_fd_usage(0, 3, left_width, vals)
        self._draw_flags(0, 4, left_width, vals)
    elif is_wide and vals.is_connected:
      self._draw_newnym_option(left_width, 1, right_width, vals)

  def _draw_platform_section(self, x, y, width, vals):
    """
    Section providing the user's hostname, platform, and version information...

      arm - odin (Linux 3.5.0-52-generic)        Tor 0.2.5.1-alpha-dev (unrecommended)
      |------ platform (40 characters) ------|   |----------- tor version -----------|
    """

    initial_x, space_left = x, min(width, 40)

    x = self.addstr(y, x, vals.format('arm - {hostname}', space_left))
    space_left -= x - initial_x

    if space_left >= 10:
      self.addstr(y, x, ' (%s)' % vals.format('{platform}', space_left - 3))

    x, space_left = initial_x + 43, width - 43

    if vals.version != 'Unknown' and space_left >= 10:
      x = self.addstr(y, x, vals.format('Tor {version}', space_left))
      space_left -= x - 43 - initial_x

      if space_left >= 7 + len(vals.version_status):
        x = self.addstr(y, x, ' (')
        x = self.addstr(y, x, vals.version_status, vals.version_color)
        self.addstr(y, x, ')')

  def _draw_ports_section(self, x, y, width, vals):
    """
    Section providing our nickname, address, and port information...

      Unnamed - 0.0.0.0:7000, Control Port (cookie): 9051
    """

    if not vals.is_relay:
      x = self.addstr(y, x, 'Relaying Disabled', 'cyan')
    else:
      x = self.addstr(y, x, vals.format('{nickname} - {or_address}:{or_port}'))

      if vals.dir_port != '0':
        x = self.addstr(y, x, vals.format(', Dir Port: {dir_port}'))

    if vals.control_port == '0':
      self.addstr(y, x, vals.format(', Control Socket: {socket_path}'))
    else:
      if width >= x + 19 + len(vals.control_port) + len(vals.auth_type):
        x = self.addstr(y, x, ', Control Port (')
        x = self.addstr(y, x, vals.auth_type, vals.auth_color)
        self.addstr(y, x, vals.format('): {control_port}'))
      else:
        self.addstr(y, x, vals.format(', Control Port: {control_port}'))

  def _draw_disconnected(self, x, y, width, vals):
    """
    Message indicating that tor is disconnected...

      Tor Disconnected (15:21 07/13/2014, press r to reconnect)
    """

    x = self.addstr(y, x, 'Tor Disconnected', curses.A_BOLD, 'red')
    self.addstr(y, x, vals.format(' ({last_heartbeat}, press r to reconnect)'))

  def _draw_resource_usage(self, x, y, width, vals):
    """
    System resource usage of the tor process...

      cpu: 0.0% tor, 1.0% arm    mem: 0 (0.0%)       pid: 16329  uptime: 12-20:42:07
    """

    if vals.start_time:
      if not vals.is_connected:
        now = vals.connection_time
      elif self.is_paused():
        now = self.get_pause_time()
      else:
        now = time.time()

      uptime = stem.util.str_tools.short_time_label(now - vals.start_time)
    else:
      uptime = ''

    sys_fields = (
      (0, vals.format('cpu: {tor_cpu}% tor, {arm_cpu}% arm')),
      (27, vals.format('mem: {memory} ({memory_percent}%)')),
      (47, vals.format('pid: {pid}')),
      (59, 'uptime: %s' % uptime),
    )

    for (start, label) in sys_fields:
      if width >= start + len(label):
        self.addstr(y, x + start, label)
      else:
        break

  def _draw_fingerprint_and_fd_usage(self, x, y, width, vals):
    """
    Presents our fingerprint, and our file descriptor usage if we're running
    out...

      fingerprint: 1A94D1A794FCB2F8B6CBC179EF8FDD4008A98D3B, file desc: 900 / 1000 (90%)
    """

    initial_x, space_left = x, width

    x = self.addstr(y, x, vals.format('fingerprint: {fingerprint}', width))
    space_left -= x - initial_x

    if space_left >= 30 and vals.fd_used and vals.fd_limit:
      fd_percent = 100 * vals.fd_used / vals.fd_limit

      if fd_percent >= SHOW_FD_THRESHOLD:
        if fd_percent >= 95:
          percentage_format = (curses.A_BOLD, 'red')
        elif fd_percent >= 90:
          percentage_format = ('red',)
        elif fd_percent >= 60:
          percentage_format = ('yellow',)
        else:
          percentage_format = ()

        x = self.addstr(y, x, ', file descriptors' if space_left >= 37 else ', file desc')
        x = self.addstr(y, x, vals.format(': {fd_used} / {fd_limit} ('))
        x = self.addstr(y, x, '%i%%' % fd_percent, *percentage_format)
        self.addstr(y, x, ')')

  def _draw_flags(self, x, y, width, vals):
    """
    Presents flags held by our relay...

      flags: Running, Valid
    """

    x = self.addstr(y, x, 'flags: ')

    if vals.flags:
      for i, flag in enumerate(vals.flags):
        flag_color = CONFIG['attr.flag_colors'].get(flag, 'white')
        x = self.addstr(y, x, flag, curses.A_BOLD, flag_color)

        if i < len(vals.flags) - 1:
          x = self.addstr(y, x, ', ')
    else:
      self.addstr(y, x, 'none', curses.A_BOLD, 'cyan')

  def _draw_exit_policy(self, x, y, width, vals):
    """
    Presents our exit policy...

      exit policy: reject *:*
    """

    x = self.addstr(y, x, 'exit policy: ')

    if not vals.exit_policy:
      return

    # TODO: exclude private policy prefix?
    # TODO: replace the default suffix policy with a cyan '<default>'

    rules = list(vals.exit_policy)

    for i, rule in enumerate(rules):
      policy_color = 'green' if rule.is_accept else 'red'
      x = self.addstr(y, x, str(rule), curses.A_BOLD, policy_color)

      if i < len(rules) - 1:
        x = self.addstr(y, x, ', ')

  def _draw_newnym_option(self, x, y, width, vals):
    """
    Provide a notice for requiesting a new identity, and time until it's next
    available if in the process of building circuits.
    """

    if vals.newnym_wait == 0:
      self.addstr(y, x, "press 'n' for a new identity")
    else:
      plural = 's' if vals.newnym_wait > 1 else ''
      self.addstr(y, x, 'building circuits, available again in %i second%s' % (vals.newnym_wait, plural))

  def run(self):
    """
    Keeps stats updated, checking for new information at a set rate.
    """

    last_ran = -1

    while not self._halt:
      if self.is_paused() or not self._vals.is_connected or (time.time() - last_ran) < UPDATE_RATE:
        with self._pause_condition:
          if not self._halt:
            self._pause_condition.wait(0.2)

        continue  # done waiting, try again

      self._update()
      last_ran = time.time()

  def stop(self):
    """
    Halts further resolutions and terminates the thread.
    """

    with self._pause_condition:
      self._halt = True
      self._pause_condition.notifyAll()

  def reset_listener(self, controller, event_type, _):
    self._update()

  def _update(self):
    previous_height = self.get_height()
    self._vals = Sampling(self._vals)

    if previous_height != self.get_height():
      # We're toggling between being a relay and client, causing the height
      # of this panel to change. Redraw all content so we don't get
      # overlapping content.

      arm.controller.get_controller().redraw()
    else:
      self.redraw(True)  # just need to redraw ourselves


class Sampling(object):
  """
  Statistical information rendered by the header panel.
  """

  def __init__(self, last_sampling = None):
    controller = tor_controller()

    pid = controller.get_pid(None)
    fingerprint = controller.get_info('fingerprint', None)
    or_listeners = controller.get_listeners(Listener.OR, [])
    fd_limit = controller.get_info('process/descriptor-limit', '-1')

    uname_vals = os.uname()
    tor_resources = arm.util.tracker.get_resource_tracker().get_value()

    self.is_connected = controller.is_alive()
    self.connection_time = controller.connection_time()
    self.last_heartbeat = time.strftime('%H:%M %m/%d/%Y', time.localtime(controller.get_latest_heartbeat()))
    self.retrieved = time.time()
    self.arm_total_cpu_time = sum(os.times()[:3])

    self.fingerprint = fingerprint if fingerprint else 'Unknown'
    self.nickname = controller.get_conf('Nickname', '')
    self.or_address = or_listeners[0][0] if or_listeners else controller.get_info('address', 'Unknown')
    self.or_port = or_listeners[0][1] if or_listeners else ''
    self.dir_port = controller.get_conf('DirPort', '0')
    self.control_port = controller.get_conf('ControlPort', '0')
    self.socket_path = controller.get_conf('ControlSocket', '')
    self.is_relay = bool(self.or_port)
    self.newnym_wait = controller.get_newnym_wait()

    if controller.get_conf('HashedControlPassword', None):
      self.auth_type = 'password'
    elif controller.get_conf('CookieAuthentication', None) == '1':
      self.auth_type = 'cookie'
    else:
      self.auth_type = 'open'

    self.auth_color = 'red' if self.auth_type == 'open' else 'green'
    self.exit_policy = controller.get_exit_policy(None)
    self.flags = getattr(controller.get_network_status(fingerprint, None), 'flags', [])
    self.version = str(controller.get_version('Unknown')).split()[0]
    self.version_status = controller.get_info('status/version/current', 'Unknown')
    self.version_color = CONFIG['attr.version_status_colors'].get(self.version_status, 'white')

    self.pid = pid if pid else ''
    self.start_time = stem.util.system.start_time(pid)
    self.fd_limit = int(fd_limit) if fd_limit.isdigit() else None

    try:
      self.fd_used = stem.util.proc.file_descriptors_used(pid)
    except IOError:
      self.fd_used = None

    self.tor_cpu = '%0.1f' % (100 * tor_resources.cpu_sample)
    self.arm_cpu = '%0.1f' % (100 * self._get_cpu_percentage(last_sampling))
    self.memory = stem.util.str_tools.size_label(tor_resources.memory_bytes) if tor_resources.memory_bytes > 0 else 0
    self.memory_percent = '%0.1f' % (100 * tor_resources.memory_percent)
    self.hostname = uname_vals[1]
    self.platform = '%s %s' % (uname_vals[0], uname_vals[2])  # [platform name] [version]

    if self.fd_used and self.fd_limit:
      fd_percent = 100 * self.fd_used / self.fd_limit
      msg = "Tor's file descriptor usage is at %i%%." % fd_percent

      if fd_percent >= 90:
        msg += ' If you run out Tor will be unable to continue functioning.'
        log.log_once('fd_used_at_ninety_percent', log.WARN, msg)
      elif fd_percent >= 60:
        log.log_once('fd_used_at_sixty_percent', log.NOTICE, msg)

  def format(self, msg, crop_width = None):
    """
    Applies our attributes to the given string.
    """

    formatted_msg = msg.format(**self.__dict__)

    if crop_width:
      formatted_msg = str_tools.crop(formatted_msg, crop_width)

    return formatted_msg

  def _get_cpu_percentage(self, last_sampling):
    """
    Determine the cpu usage of our own process since the last sampling.

    :param arm.header_panel.Sampling last_sampling: sampling for which to
      provide a CPU usage delta with

    :returns: **float** representation for our cpu usage over the given period
      of time
    """

    if last_sampling:
      arm_cpu_delta = self.arm_total_cpu_time - last_sampling.arm_total_cpu_time
      arm_time_delta = self.retrieved - last_sampling.retrieved

      python_cpu_time = arm_cpu_delta / arm_time_delta
      sys_call_cpu_time = 0.0  # TODO: add a wrapper around call() to get this

      return python_cpu_time + sys_call_cpu_time
    else:
      return 0.0
