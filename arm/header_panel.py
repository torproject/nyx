"""
Top panel for every page, containing basic system and tor related information.
If there's room available then this expands to present its information in two
columns, otherwise it's laid out as follows:
  arm - <hostname> (<os> <sys/version>)         Tor <tor/version> (<new, old, recommended, etc>)
  <nickname> - <address>:<or_port>, [Dir Port: <dir_port>, ]Control Port (<open, password, cookie>): <control_port>
  cpu: <cpu%> mem: <mem> (<mem%>) uid: <uid> uptime: <upmin>:<upsec>
  fingerprint: <fingerprint>

Example:
  arm - odin (Linux 2.6.24-24-generic)         Tor 0.2.1.19 (recommended)
  odin - 76.104.132.98:9001, Dir Port: 9030, Control Port (cookie): 9051
  cpu: 14.6%    mem: 42 MB (4.2%)    pid: 20060   uptime: 48:27
  fingerprint: BDAD31F6F318E0413833E8EBDA956F76E4D66788
"""

import os
import time
import collections
import curses
import threading

import arm.util.tracker

from stem import Signal
from stem.control import Listener, State
from stem.util import conf, log, proc, str_tools, system

import arm.starter
import arm.popups
import arm.controller

from util import panel, ui_tools, tor_controller

# minimum width for which panel attempts to double up contents (two columns to
# better use screen real estate)

MIN_DUAL_COL_WIDTH = 141

CONFIG = conf.config_dict('arm', {
  'attr.flag_colors': {},
  'attr.version_status_colors': {},
  'features.showFdUsage': False,
})

Sampling = collections.namedtuple('Sampling', [
  'address',
  'fingerprint',
  'nickname',
  'or_address',
  'or_port',
  'dir_port',

  'control_port',
  'socket_path',
  'is_password_auth',
  'is_cookie_auth',

  'exit_policy',
  'flags',
  'version',
  'version_status',

  'pid',
  'start_time',
  'fd_limit',
  'fd_used',

  'tor_cpu',
  'arm_cpu',
  'rss',
  'memory',
  'hostname',
  'os_name',
  'os_version',
])


class HeaderPanel(panel.Panel, threading.Thread):
  """
  Top area contenting tor settings and system information. Stats are stored in
  the vals mapping, keys including:
    tor/  version, versionStatus, nickname, or_port, dir_port, control_port,
          socketPath, exit_policy, isAuthPassword (bool), isAuthCookie (bool),
          orListenAddr, *address, *fingerprint, *flags, pid, start_time,
          *fd_used, fd_limit
    sys/  hostname, os, version
    stat/ *%torCpu, *%armCpu, *rss, *%mem

  * volatile parameter that'll be reset on each update
  """

  def __init__(self, stdscr, start_time):
    panel.Panel.__init__(self, stdscr, "header", 0)
    threading.Thread.__init__(self)
    self.setDaemon(True)

    self._is_tor_connected = tor_controller().is_alive()
    self._last_update = -1       # time the content was last revised
    self._halt = False           # terminates thread if true
    self._cond = threading.Condition()  # used for pausing the thread

    # Time when the panel was paused or tor was stopped. This is used to
    # freeze the uptime statistic (uptime increments normally when None).

    self._halt_time = None

    # The last arm cpu usage sampling taken. This is a tuple of the form:
    # (total arm cpu time, sampling timestamp)
    #
    # The initial cpu total should be zero. However, at startup the cpu time
    # in practice is often greater than the real time causing the initially
    # reported cpu usage to be over 100% (which shouldn't be possible on
    # single core systems).
    #
    # Setting the initial cpu total to the value at this panel's init tends to
    # give smoother results (staying in the same ballpark as the second
    # sampling) so fudging the numbers this way for now.

    self._arm_cpu_sampling = (sum(os.times()[:3]), start_time)

    # Last sampling received from the ResourceTracker, used to detect when it
    # changes.

    self._last_resource_fetch = -1

    # flag to indicate if we've already given file descriptor warnings

    self._is_fd_sixty_percent_warned = False
    self._is_fd_ninety_percent_warned = False

    self.vals = {}
    self.vals_lock = threading.RLock()

    with self.vals_lock:
      self.vals = self._get_attributes()

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

    tor_controller().signal(Signal.NEWNYM)

    # If we're wide then the newnym label in this panel will give an
    # indication that the signal was sent. Otherwise use a msg.

    is_wide = self.get_parent().getmaxyx()[1] >= MIN_DUAL_COL_WIDTH

    if not is_wide:
      arm.popups.show_msg("Requesting a new identity", 1)

  def handle_key(self, key):
    is_keystroke_consumed = True

    if key in (ord('n'), ord('N')) and tor_controller().is_newnym_available():
      self.send_newnym()
    elif key in (ord('r'), ord('R')) and not self._is_tor_connected:
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

  def draw(self, width, height):
    self.vals_lock.acquire()
    is_wide = width + 1 >= MIN_DUAL_COL_WIDTH

    # space available for content

    if is_wide:
      left_width = max(width / 2, 77)
      right_width = width - left_width
    else:
      left_width = right_width = width

    # Line 1 / Line 1 Left (system and tor version information)

    sys_name_label = "arm - %s" % self.vals.hostname
    content_space = min(left_width, 40)

    if len(sys_name_label) + 10 <= content_space:
      sys_type_label = "%s %s" % (self.vals.os_name, self.vals.os_version)
      sys_type_label = ui_tools.crop_str(sys_type_label, content_space - len(sys_name_label) - 3, 4)
      self.addstr(0, 0, "%s (%s)" % (sys_name_label, sys_type_label))
    else:
      self.addstr(0, 0, ui_tools.crop_str(sys_name_label, content_space))

    content_space = left_width - 43

    if 7 + len(self.vals.version) + len(self.vals.version_status) <= content_space:
      if self.vals.version != "Unknown":
        version_color = CONFIG['attr.version_status_colors'].get(self.vals.version_status, 'white')

        label_prefix = "Tor %s (" % self.vals.version
        self.addstr(0, 43, label_prefix)
        self.addstr(0, 43 + len(label_prefix), self.vals.version_status, ui_tools.get_color(version_color))
        self.addstr(0, 43 + len(label_prefix) + len(self.vals.version_status), ")")
    elif 11 <= content_space:
      self.addstr(0, 43, ui_tools.crop_str("Tor %s" % self.vals.version, content_space, 4))

    # Line 2 / Line 2 Left (tor ip/port information)

    x, include_control_port = 0, True

    if self.vals.or_port:
      my_address = "Unknown"

      if self.vals.or_address:
        my_address = self.vals.or_address
      elif self.vals.address:
        my_address = self.vals.address

      # acting as a relay (we can assume certain parameters are set

      dir_port_label = ", Dir Port: %s" % self.vals.dir_port if self.vals.dir_port != "0" else ""

      for label in (self.vals.nickname, " - " + my_address, ":" + self.vals.or_port, dir_port_label):
        if x + len(label) <= left_width:
          self.addstr(1, x, label)
          x += len(label)
        else:
          break
    else:
      # non-relay (client only)

      if self._is_tor_connected:
        self.addstr(1, x, "Relaying Disabled", ui_tools.get_color("cyan"))
        x += 17
      else:
        status_time = tor_controller().get_latest_heartbeat()

        if status_time:
          status_time_label = time.strftime("%H:%M %m/%d/%Y, ", time.localtime(status_time))
        else:
          status_time_label = ""  # never connected to tor

        self.addstr(1, x, "Tor Disconnected", curses.A_BOLD | ui_tools.get_color("red"))
        self.addstr(1, x + 16, " (%spress r to reconnect)" % status_time_label)
        x += 39 + len(status_time_label)
        include_control_port = False

    if include_control_port:
      if self.vals.control_port == "0":
        # connected via a control socket
        self.addstr(1, x, ", Control Socket: %s" % self.vals.socket_path)
      else:
        if self.vals.is_password_auth:
          auth_type = "password"
        elif self.vals.is_cookie_auth:
          auth_type = "cookie"
        else:
          auth_type = "open"

        if x + 19 + len(self.vals.control_port) + len(auth_type) <= left_width:
          auth_color = "red" if auth_type == "open" else "green"
          self.addstr(1, x, ", Control Port (")
          self.addstr(1, x + 16, auth_type, ui_tools.get_color(auth_color))
          self.addstr(1, x + 16 + len(auth_type), "): %s" % self.vals.control_port)
        elif x + 16 + len(self.vals.control_port) <= left_width:
          self.addstr(1, 0, ", Control Port: %s" % self.vals.control_port)

    # Line 3 / Line 1 Right (system usage info)

    y, x = (0, left_width) if is_wide else (2, 0)

    if self.vals.rss != "0":
      memory_label = str_tools.get_size_label(int(self.vals.rss))
    else:
      memory_label = "0"

    uptime_label = ""

    if self.vals.start_time:
      if self.is_paused() or not self._is_tor_connected:
        # freeze the uptime when paused or the tor process is stopped
        uptime_label = str_tools.get_short_time_label(self.get_pause_time() - self.vals.start_time)
      else:
        uptime_label = str_tools.get_short_time_label(time.time() - self.vals.start_time)

    sys_fields = ((0, "cpu: %s%% tor, %s%% arm" % (self.vals.tor_cpu, self.vals.arm_cpu)),
                  (27, "mem: %s (%s%%)" % (memory_label, self.vals.memory)),
                  (47, "pid: %s" % (self.vals.pid if self._is_tor_connected else "")),
                  (59, "uptime: %s" % uptime_label))

    for (start, label) in sys_fields:
      if start + len(label) <= right_width:
        self.addstr(y, x + start, label)
      else:
        break

    if self.vals.or_port:
      # Line 4 / Line 2 Right (fingerprint, and possibly file descriptor usage)

      y, x = (1, left_width) if is_wide else (3, 0)

      fingerprint_label = ui_tools.crop_str("fingerprint: %s" % self.vals.fingerprint, width)
      self.addstr(y, x, fingerprint_label)

      # if there's room and we're able to retrieve both the file descriptor
      # usage and limit then it might be presented

      if width - x - 59 >= 20 and self.vals.fd_used and self.vals.fd_limit:
        # display file descriptor usage if we're either configured to do so or
        # running out

        fd_percent = 100 * self.vals.fd_used / self.vals.fd_limit

        if fd_percent >= 60 or CONFIG["features.showFdUsage"]:
          fd_percentLabel, fd_percent_format = "%i%%" % fd_percent, curses.A_NORMAL

          if fd_percent >= 95:
            fd_percent_format = curses.A_BOLD | ui_tools.get_color("red")
          elif fd_percent >= 90:
            fd_percent_format = ui_tools.get_color("red")
          elif fd_percent >= 60:
            fd_percent_format = ui_tools.get_color("yellow")

          base_label = "file desc: %i / %i (" % (self.vals.fd_used, self.vals.fd_limit)

          self.addstr(y, x + 59, base_label)
          self.addstr(y, x + 59 + len(base_label), fd_percentLabel, fd_percent_format)
          self.addstr(y, x + 59 + len(base_label) + len(fd_percentLabel), ")")

      # Line 5 / Line 3 Left (flags)

      if self._is_tor_connected:
        y, x = (2 if is_wide else 4, 0)
        self.addstr(y, x, "flags: ")
        x += 7

        if len(self.vals.flags) > 0:
          for i in range(len(self.vals.flags)):
            flag = self.vals.flags[i]
            flag_color = CONFIG['attr.flag_colors'].get(flag, 'white')

            self.addstr(y, x, flag, curses.A_BOLD | ui_tools.get_color(flag_color))
            x += len(flag)

            if i < len(self.vals.flags) - 1:
              self.addstr(y, x, ", ")
              x += 2
        else:
          self.addstr(y, x, "none", curses.A_BOLD | ui_tools.get_color("cyan"))
      else:
        y = 2 if is_wide else 4
        status_time = tor_controller().get_latest_heartbeat()
        status_time_label = time.strftime("%H:%M %m/%d/%Y", time.localtime(status_time))
        self.addstr(y, 0, "Tor Disconnected", curses.A_BOLD | ui_tools.get_color("red"))
        self.addstr(y, 16, " (%s) - press r to reconnect" % status_time_label)

      # Undisplayed / Line 3 Right (exit policy)

      if is_wide:
        exit_policy = self.vals.exit_policy

        # adds note when default exit policy is appended

        if exit_policy == "":
          exit_policy = "<default>"
        elif not exit_policy.endswith((" *:*", " *")):
          exit_policy += ", <default>"

        self.addstr(2, left_width, "exit policy: ")
        x = left_width + 13

        # color codes accepts to be green, rejects to be red, and default marker to be cyan

        is_simple = len(exit_policy) > right_width - 13
        policies = exit_policy.split(", ")

        for i in range(len(policies)):
          policy = policies[i].strip()
          policy_label = policy.replace("accept", "").replace("reject", "").strip() if is_simple else policy

          policy_color = "white"

          if policy.startswith("accept"):
            policy_color = "green"
          elif policy.startswith("reject"):
            policy_color = "red"
          elif policy.startswith("<default>"):
            policy_color = "cyan"

          self.addstr(2, x, policy_label, curses.A_BOLD | ui_tools.get_color(policy_color))
          x += len(policy_label)

          if i < len(policies) - 1:
            self.addstr(2, x, ", ")
            x += 2
    else:
      # (Client only) Undisplayed / Line 2 Right (new identity option)

      if is_wide:
        newnym_wait = tor_controller().get_newnym_wait()

        msg = "press 'n' for a new identity"

        if newnym_wait > 0:
          plural_label = "s" if newnym_wait > 1 else ""
          msg = "building circuits, available again in %i second%s" % (newnym_wait, plural_label)

        self.addstr(1, left_width, msg)

    self.vals_lock.release()

  def get_pause_time(self):
    """
    Provides the time Tor stopped if it isn't running. Otherwise this is the
    time we were last paused.
    """

    if self._halt_time:
      return self._halt_time
    else:
      return panel.Panel.get_pause_time(self)

  def run(self):
    """
    Keeps stats updated, checking for new information at a set rate.
    """

    last_draw = time.time() - 1

    while not self._halt:
      current_time = time.time()

      if self.is_paused() or current_time - last_draw < 1 or not self._is_tor_connected:
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
          resource_tracker = arm.util.tracker.get_resource_tracker()
          is_changed = self._last_resource_fetch != resource_tracker.run_counter()

        if is_changed or current_time - self._last_update >= 20:
          with self.vals_lock:
            self.vals = self._get_attributes()

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
      self._is_tor_connected = True
      self._halt_time = None

      with self.vals_lock:
        self.vals = self._get_attributes()

      if self.get_height() != initial_height:
        # We're toggling between being a relay and client, causing the height
        # of this panel to change. Redraw all content so we don't get
        # overlapping content.

        arm.controller.get_controller().redraw()
      else:
        # just need to redraw ourselves
        self.redraw(True)
    elif event_type == State.CLOSED:
      self._is_tor_connected = False
      self._halt_time = time.time()

      with self.vals_lock:
        self.vals = self._get_attributes()

      self.redraw(True)

  def _get_attributes(self):
    controller = tor_controller()

    or_listeners = controller.get_listeners(Listener.OR, [])

    if not or_listeners:
      or_address, or_port = '', ''
    else:
      # TODO: Relays can bind to multiple ports to listen for OR connections.
      # Not sure how we'd like to surface that...

      or_address, or_port = or_listeners[0]

    # file descriptor limit for the process, if this can't be determined
    # then the limit is None

    fd_limit = controller.get_info('process/descriptor-limit', '-1')

    if fd_limit != '-1' and fd_limit.isdigit():
      fd_limit = int(fd_limit)
    else:
      fd_limit = None

    uname_vals = os.uname()

    try:
      start_time = system.get_start_time(controller.get_pid())
    except:
      start_time = None

    flags = []
    my_fingerprint = controller.get_info('fingerprint', None)

    if my_fingerprint:
      my_status_entry = controller.get_network_status(my_fingerprint)

      if my_status_entry:
        flags = my_status_entry.flags

    # Updates file descriptor usage and logs if the usage is high. If we don't
    # have a known limit or it's obviously faulty (being lower than our
    # current usage) then omit file descriptor functionality.

    fd_used = 0

    if fd_limit:
      fd_used = get_file_descriptor_usage(controller.get_pid(None))

      if not fd_used or fd_used > fd_limit:
        fd_used = 0

    if fd_used and fd_limit:
      fd_percent = 100 * fd_used / fd_limit
      msg = "Tor's file descriptor usage is at %i%%." % fd_percent

      if fd_percent >= 90 and not self._is_fd_ninety_percent_warned:
        self._is_fd_sixty_percent_warned, self._is_fd_ninety_percent_warned = True, True
        msg += " If you run out Tor will be unable to continue functioning."
        log.warn(msg)
      elif fd_percent >= 60 and not self._is_fd_sixty_percent_warned:
        self._is_fd_sixty_percent_warned = True
        log.notice(msg)

    resource_tracker = arm.util.tracker.get_resource_tracker()

    resources = resource_tracker.get_resource_usage()
    self._last_resource_fetch = resource_tracker.run_counter()

    tor_cpu = '%0.1f' % (100 * resources.cpu_sample)
    tor_rss = str(resources.memory_bytes)
    tor_memory = '%0.1f' % (100 * resources.memory_percent)

    # determines the cpu time for the arm process (including user and system
    # time of both the primary and child processes)

    total_arm_cpu_time, current_time = sum(os.times()[:3]), time.time()
    arm_cpu_telta = total_arm_cpu_time - self._arm_cpu_sampling[0]
    arm_time_delta = current_time - self._arm_cpu_sampling[1]
    python_cpu_time = arm_cpu_telta / arm_time_delta
    sys_call_cpu_time = 0.0  # TODO: add a wrapper around call() to get this
    arm_cpu = '%0.1f' % (100 * (python_cpu_time + sys_call_cpu_time))
    self._arm_cpu_sampling = (total_arm_cpu_time, current_time)

    self._last_update = current_time

    return Sampling(
      address = controller.get_info('address', ''),
      fingerprint = controller.get_info('fingerprint', 'Unknown'),
      nickname = controller.get_conf('Nickname', ''),
      or_address = or_address,
      or_port = or_port,
      dir_port = controller.get_conf('DirPort', '0'),

      control_port = controller.get_conf('ControlPort', '0'),
      socket_path = controller.get_conf('ControlSocket', ''),
      is_password_auth = controller.get_conf('HashedControlPassword', None) is not None,
      is_cookie_auth = controller.get_conf('CookieAuthentication', None) == '1',

      exit_policy = str(controller.get_exit_policy()),
      flags = flags,
      version = str(controller.get_version('Unknown')).split()[0],
      version_status = controller.get_info('status/version/current', 'Unknown'),

      pid = controller.get_pid(''),
      start_time = start_time if start_time else '',
      fd_limit = fd_limit,
      fd_used = fd_used,

      tor_cpu = tor_cpu,
      arm_cpu = arm_cpu,
      rss = tor_rss,
      memory = tor_memory,
      hostname = uname_vals[1],
      os_name = uname_vals[0],
      os_version = uname_vals[2],
    )


def get_file_descriptor_usage(pid):
  """
  Provides the number of file descriptors currently being used by this
  process. This returns None if this can't be determined.
  """

  # The file descriptor usage is the size of the '/proc/<pid>/fd' contents
  # http://linuxshellaccount.blogspot.com/2008/06/finding-number-of-open-file-descriptors.html
  # I'm not sure about other platforms (like BSD) so erroring out there.

  if pid and proc.is_available():
    try:
      return len(os.listdir('/proc/%s/fd' % pid))
    except:
      return None
