"""
Top panel for every page, containing basic system and tor related information.
This expands the information it presents to two columns if there's room
available.
"""

import os
import time
import curses
import threading

import arm.util.tracker

import stem
import stem.util.proc
import stem.util.str_tools
import stem.util.system

from stem.control import Listener, State
from stem.util import conf, log

import arm.starter
import arm.popups
import arm.controller

from util import panel, ui_tools, tor_controller

MIN_DUAL_COL_WIDTH = 141  # minimum width where we'll show two columns

CONFIG = conf.config_dict('arm', {
  'attr.flag_colors': {},
  'attr.version_status_colors': {},
  'features.showFdUsage': False,
})


class HeaderPanel(panel.Panel, threading.Thread):
  """
  Top area containing tor settings and system information.
  """

  def __init__(self, stdscr, start_time):
    panel.Panel.__init__(self, stdscr, 'header', 0)
    threading.Thread.__init__(self)
    self.setDaemon(True)

    self._halt = False           # terminates thread if true
    self._cond = threading.Condition()  # used for pausing the thread

    # Time when the panel was paused or tor was stopped. This is used to
    # freeze the uptime statistic (uptime increments normally when None).

    self._halt_time = None

    # flag to indicate if we've already given file descriptor warnings

    self._is_fd_sixty_percent_warned = False
    self._is_fd_ninety_percent_warned = False

    self.vals = Sampling()

    # listens for tor reload (sighup) events

    tor_controller().add_status_listener(self.reset_listener)

  def get_height(self):
    """
    Provides the height of the content, which is dynamically determined by the
    panel's maximum width.
    """

    is_wide = self.get_parent().getmaxyx()[1] >= MIN_DUAL_COL_WIDTH

    if self.vals.or_port:
      return 4 if is_wide else 6
    else:
      return 3 if is_wide else 4

  def send_newnym(self):
    """
    Requests a new identity and provides a visual queue.
    """

    tor_controller().signal(stem.Signal.NEWNYM)

    # If we're wide then the newnym label in this panel will give an
    # indication that the signal was sent. Otherwise use a msg.

    is_wide = self.get_parent().getmaxyx()[1] >= MIN_DUAL_COL_WIDTH

    if not is_wide:
      arm.popups.show_msg('Requesting a new identity', 1)

  def handle_key(self, key):
    is_keystroke_consumed = True

    if key in (ord('n'), ord('N')) and tor_controller().is_newnym_available():
      self.send_newnym()
    elif key in (ord('r'), ord('R')) and not self.vals.is_connected:
      #oldSocket = tor_tools.get_conn().get_controller().get_socket()
      #
      #controller = None
      #allowPortConnection, allowSocketConnection, _ = starter.allowConnectionTypes()
      #
      #if os.path.exists(CONFIG["startup.interface.socket"]) and allowSocketConnection:
      #  try:
      #    # TODO: um... what about passwords?
      #    controller = Controller.from_socket_file(CONFIG["startup.interface.socket"])
      #    controller.authenticate()
      #  except (IOError, stem.SocketError), exc:
      #    controller = None
      #
      #    if not allowPortConnection:
      #      arm.popups.show_msg("Unable to reconnect (%s)" % exc, 3)
      #elif not allowPortConnection:
      #  arm.popups.show_msg("Unable to reconnect (socket '%s' doesn't exist)" % CONFIG["startup.interface.socket"], 3)
      #
      #if not controller and allowPortConnection:
      #  # TODO: This has diverged from starter.py's connection, for instance it
      #  # doesn't account for relative cookie paths or multiple authentication
      #  # methods. We can't use the starter.py's connection function directly
      #  # due to password prompts, but we could certainly make this mess more
      #  # manageable.
      #
      #  try:
      #    ctlAddr, ctl_port = CONFIG["startup.interface.ip_address"], CONFIG["startup.interface.port"]
      #    controller = Controller.from_port(ctlAddr, ctl_port)
      #
      #    try:
      #      controller.authenticate()
      #    except stem.connection.MissingPassword:
      #      controller.authenticate(authValue) # already got the password above
      #  except Exception, exc:
      #    controller = None
      #
      #if controller:
      #  tor_tools.get_conn().init(controller)
      #  log.notice("Reconnected to Tor's control port")
      #  arm.popups.show_msg("Tor reconnected", 1)

      pass
    else:
      is_keystroke_consumed = False

    return is_keystroke_consumed

  def addtstr(self, y, x, msg, space_left, attr=curses.A_NORMAL):
    cursor_position = self.addstr(y, x, ui_tools.crop_str(msg, space_left), attr)
    return cursor_position, space_left - (cursor_position - x)

  def draw(self, width, height):
    vals = self.vals
    is_wide = width + 1 >= MIN_DUAL_COL_WIDTH

    # space available for content

    left_width = max(width / 2, 77) if is_wide else width
    right_width = width - left_width

    self._draw_platform_section(0, 0, left_width, vals)
    self._draw_ports_section(0, 1, left_width, vals)

    if is_wide:
      self._draw_resource_usage(left_width, 0, right_width, vals)
    else:
      self._draw_resource_usage(0, 2, left_width, vals)

    if vals.or_port:
      # Line 4 / Line 2 Right (fingerprint, and possibly file descriptor usage)

      y, x = (1, left_width) if is_wide else (3, 0)

      fingerprint_label = ui_tools.crop_str('fingerprint: %s' % vals.fingerprint, width)
      self.addstr(y, x, fingerprint_label)

      # if there's room and we're able to retrieve both the file descriptor
      # usage and limit then it might be presented

      if width - x - 59 >= 20 and vals.fd_used and vals.fd_limit:
        # display file descriptor usage if we're either configured to do so or
        # running out

        fd_percent = 100 * vals.fd_used / vals.fd_limit

        if fd_percent >= 60 or CONFIG['features.showFdUsage']:
          fd_percentLabel, fd_percent_format = '%i%%' % fd_percent, curses.A_NORMAL

          if fd_percent >= 95:
            fd_percent_format = curses.A_BOLD | ui_tools.get_color('red')
          elif fd_percent >= 90:
            fd_percent_format = ui_tools.get_color('red')
          elif fd_percent >= 60:
            fd_percent_format = ui_tools.get_color('yellow')

          base_label = 'file desc: %i / %i (' % (vals.fd_used, vals.fd_limit)

          self.addstr(y, x + 59, base_label)
          self.addstr(y, x + 59 + len(base_label), fd_percentLabel, fd_percent_format)
          self.addstr(y, x + 59 + len(base_label) + len(fd_percentLabel), ')')

      # Line 5 / Line 3 Left (flags)

      if self.vals.is_connected:
        y, x = (2 if is_wide else 4, 0)
        self.addstr(y, x, 'flags: ')
        x += 7

        if len(vals.flags) > 0:
          for i in range(len(vals.flags)):
            flag = vals.flags[i]
            flag_color = CONFIG['attr.flag_colors'].get(flag, 'white')

            self.addstr(y, x, flag, curses.A_BOLD | ui_tools.get_color(flag_color))
            x += len(flag)

            if i < len(vals.flags) - 1:
              self.addstr(y, x, ', ')
              x += 2
        else:
          self.addstr(y, x, 'none', curses.A_BOLD | ui_tools.get_color('cyan'))
      else:
        y = 2 if is_wide else 4
        status_time = tor_controller().get_latest_heartbeat()
        status_time_label = time.strftime('%H:%M %m/%d/%Y', time.localtime(status_time))
        self.addstr(y, 0, 'Tor Disconnected', curses.A_BOLD | ui_tools.get_color('red'))
        self.addstr(y, 16, ' (%s) - press r to reconnect' % status_time_label)

      # Undisplayed / Line 3 Right (exit policy)

      if is_wide:
        exit_policy = vals.exit_policy

        # adds note when default exit policy is appended

        if exit_policy == '':
          exit_policy = '<default>'
        elif not exit_policy.endswith((' *:*', ' *')):
          exit_policy += ', <default>'

        self.addstr(2, left_width, 'exit policy: ')
        x = left_width + 13

        # color codes accepts to be green, rejects to be red, and default marker to be cyan

        is_simple = len(exit_policy) > (right_width if is_wide else left_width) - 13
        policies = exit_policy.split(', ')

        for i in range(len(policies)):
          policy = policies[i].strip()
          policy_label = policy.replace('accept', '').replace('reject', '').strip() if is_simple else policy

          policy_color = 'white'

          if policy.startswith('accept'):
            policy_color = 'green'
          elif policy.startswith('reject'):
            policy_color = 'red'
          elif policy.startswith('<default>'):
            policy_color = 'cyan'

          self.addstr(2, x, policy_label, curses.A_BOLD | ui_tools.get_color(policy_color))
          x += len(policy_label)

          if i < len(policies) - 1:
            self.addstr(2, x, ', ')
            x += 2
    else:
      # (Client only) Undisplayed / Line 2 Right (new identity option)

      if is_wide:
        newnym_wait = tor_controller().get_newnym_wait()

        msg = "press 'n' for a new identity"

        if newnym_wait > 0:
          plural_label = 's' if newnym_wait > 1 else ''
          msg = 'building circuits, available again in %i second%s' % (newnym_wait, plural_label)

        self.addstr(1, left_width, msg)

  def _draw_platform_section(self, x, y, width, vals):
    """
    Section providing the user's hostname, platform, and version information...

      arm - odin (Linux 3.5.0-52-generic)        Tor 0.2.5.1-alpha-dev (unrecommended)
      |------ platform (40 characters) ------|   |----------- tor version -----------|
    """

    space_left = min(width, 40)
    x, space_left = self.addtstr(y, x, vals.format('arm - {hostname}'), space_left)

    if space_left >= 10:
      self.addstr(y, x, ' (%s)' % ui_tools.crop_str(vals.platform, space_left - 3, 4))

    x, space_left = 43, width - 43

    if vals.version != 'Unknown' and space_left >= 10:
      x, space_left = self.addtstr(y, x, vals.format('Tor {version}'), space_left)

      if space_left >= 7 + len(vals.version_status):
        version_color = CONFIG['attr.version_status_colors'].get(vals.version_status, 'white')

        x = self.addstr(y, x, ' (')
        x = self.addstr(y, x, vals.version_status, ui_tools.get_color(version_color))
        self.addstr(y, x, ')')

  def _draw_ports_section(self, x, y, width, vals):
    """
    Section providing our nickname, address, and port information...

      Unnamed - 0.0.0.0:7000, Control Port (cookie): 9051
    """

    if self.vals.is_connected:
      if not vals.or_port:
        x = self.addstr(y, x, 'Relaying Disabled', ui_tools.get_color('cyan'))
      else:
        x = self.addstr(y, x, vals.format('{nickname} - {or_address}:{or_port}'))

        if vals.dir_port != '0':
          x = self.addstr(y, x, vals.format(', Dir Port: {dir_port}'))
    else:
      x = self.addstr(y, x, 'Tor Disconnected', curses.A_BOLD | ui_tools.get_color('red'))
      x = self.addstr(y, x, ' (%s, press r to reconnect)' % self.last_heartbeat)

    if vals.control_port == '0':
      self.addstr(y, x, vals.format(', Control Socket: {socket_path}'))
    else:
      if width >= x + 19 + len(vals.control_port) + len(vals.auth_type):
        auth_color = 'red' if vals.auth_type == 'open' else 'green'

        x = self.addstr(y, x, ', Control Port (')
        x = self.addstr(y, x, vals.auth_type, ui_tools.get_color(auth_color))
        self.addstr(y, x, vals.format('): {control_port}'))
      else:
        self.addstr(y, x, ', Control Port: %s' % vals.control_port)

  def _draw_resource_usage(self, x, y, width, vals):
    """
    System resource usage of the tor process...

      cpu: 0.0% tor, 1.0% arm    mem: 0 (0.0%)       pid: 16329  uptime: 12-20:42:07
    """

    if vals.start_time:
      if not self.vals.is_connected:
        now = self._halt_time
      elif self.is_paused():
        now = self.get_pause_time()
      else:
        now = time.time()

      uptime = stem.util.str_tools.get_short_time_label(now - vals.start_time)
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

  def run(self):
    """
    Keeps stats updated, checking for new information at a set rate.
    """

    last_draw = time.time() - 1

    while not self._halt:
      current_time = time.time()

      if self.is_paused() or current_time - last_draw < 1 or not self.vals.is_connected:
        self._cond.acquire()

        if not self._halt:
          self._cond.wait(0.2)

        self._cond.release()
      else:
        # Update the volatile attributes (cpu, memory, flags, etc) if we have
        # a new resource usage sampling (the most dynamic stat) or its been
        # twenty seconds since last fetched (so we still refresh occasionally
        # when resource fetches fail).
        #
        # Otherwise, just redraw the panel to change the uptime field.

        is_changed = False

        if self.vals.pid:
          #resource_tracker = arm.util.tracker.get_resource_tracker()
          #is_changed = self._last_resource_fetch != resource_tracker.run_counter()
          is_changed = True  # TODO: we should decide to redraw or not based on if the sampling values have changed

        if is_changed or (self.vals and current_time - self.vals.retrieved >= 20):
          self.vals = Sampling(self.vals)

          if self.vals.fd_used and self.vals.fd_limit:
            fd_percent = 100 * self.vals.fd_used / self.vals.fd_limit
            msg = "Tor's file descriptor usage is at %i%%." % fd_percent

            if fd_percent >= 90 and not self._is_fd_ninety_percent_warned:
              self._is_fd_sixty_percent_warned, self._is_fd_ninety_percent_warned = True, True
              msg += ' If you run out Tor will be unable to continue functioning.'
              log.warn(msg)
            elif fd_percent >= 60 and not self._is_fd_sixty_percent_warned:
              self._is_fd_sixty_percent_warned = True
              log.notice(msg)

        self.redraw(True)
        last_draw += 1

  def stop(self):
    """
    Halts further resolutions and terminates the thread.
    """

    self._cond.acquire()
    self._halt = True
    self._cond.notifyAll()
    self._cond.release()

  def reset_listener(self, controller, event_type, _):
    """
    Updates static parameters on tor reload (sighup) events.
    """

    if event_type in (State.INIT, State.RESET):
      initial_height = self.get_height()
      self._halt_time = None

      self.vals = Sampling(self.vals)

      if self.get_height() != initial_height:
        # We're toggling between being a relay and client, causing the height
        # of this panel to change. Redraw all content so we don't get
        # overlapping content.

        arm.controller.get_controller().redraw()
      else:
        # just need to redraw ourselves
        self.redraw(True)
    elif event_type == State.CLOSED:
      self._halt_time = time.time()

      self.vals = Sampling(self.vals)

      self.redraw(True)


class Sampling(object):
  """
  Statistical information rendered by the header panel.
  """

  def __init__(self, last_sampling = None):
    controller = tor_controller()

    or_listeners = controller.get_listeners(Listener.OR, [])
    fd_limit = controller.get_info('process/descriptor-limit', '-1')

    uname_vals = os.uname()
    tor_resources = arm.util.tracker.get_resource_tracker().get_value()

    self.is_connected = controller.is_alive()
    self.last_heartbeat = time.strftime('%H:%M %m/%d/%Y', time.localtime(controller.get_latest_heartbeat()))
    self.retrieved = time.time()
    self.arm_total_cpu_time = sum(os.times()[:3])

    self.fingerprint = controller.get_info('fingerprint', 'Unknown')
    self.nickname = controller.get_conf('Nickname', '')
    self.or_address = or_listeners[0][0] if or_listeners else controller.get_info('address', 'Unknown')
    self.or_port = or_listeners[0][1] if or_listeners else ''
    self.dir_port = controller.get_conf('DirPort', '0')
    self.control_port = controller.get_conf('ControlPort', '0')
    self.socket_path = controller.get_conf('ControlSocket', '')

    if controller.get_conf('HashedControlPassword', None):
      self.auth_type = 'password'
    elif controller.get_conf('CookieAuthentication', None) == '1':
      self.auth_type = 'cookie'
    else:
      self.auth_type = 'open'

    self.exit_policy = str(controller.get_exit_policy(''))
    self.flags = self._get_flags(controller)
    self.version = str(controller.get_version('Unknown')).split()[0]
    self.version_status = controller.get_info('status/version/current', 'Unknown')

    self.pid = controller.get_pid('')
    self.start_time = stem.util.system.get_start_time(controller.get_pid(None))
    self.fd_limit = int(fd_limit) if fd_limit.isdigit() else None
    self.fd_used = self._get_fd_used(controller.get_pid(None)) if self.fd_limit else 0

    self.tor_cpu = '%0.1f' % (100 * tor_resources.cpu_sample)
    self.arm_cpu = '%0.1f' % (100 * self._get_cpu_percentage(last_sampling))
    self.memory = stem.util.str_tools.get_size_label(tor_resources.memory_bytes) if tor_resources.memory_bytes > 0 else 0
    self.memory_percent = '%0.1f' % (100 * tor_resources.memory_percent)
    self.hostname = uname_vals[1]
    self.platform = '%s %s' % (uname_vals[0], uname_vals[2])  # [platform name] [version]

  def format(self, msg):
    """
    Applies our attributes to the given string.
    """

    return msg.format(**self.__dict__)

  def _get_fd_used(self, pid):
    """
    Provides the number of file descriptors currently being used by this
    process.

    :param int pid: process id to look up

    :returns: **int** of the number of file descriptors used, **None** if this
      can't be determined
    """

    # The file descriptor usage is the size of the '/proc/<pid>/fd' contents...
    #
    #   http://linuxshellaccount.blogspot.com/2008/06/finding-number-of-open-file-descriptors.html
    #
    # I'm not sure about other platforms (like BSD) so erroring out there.

    if pid and stem.util.proc.is_available():
      try:
        return len(os.listdir('/proc/%s/fd' % pid))
      except:
        pass

    return None

  def _get_flags(self, controller):
    """
    Provides the flags held by our relay. This is an empty list if it can't be
    determined, likely because we don't have our own router status entry yet.

    :param stem.control.Controller controller: tor control connection

    :returns: **list** with the relays held by our relay
    """

    try:
      my_fingerprint = controller.get_info('fingerprint')
      return controller.get_network_status(my_fingerprint).flags
    except stem.ControllerError:
      return []

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
