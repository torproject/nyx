# Copyright 2009-2016, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Top panel for every page, containing basic system and tor related information.
This expands the information it presents to two columns if there's room
available.
"""

import os
import time

import stem
import stem.control
import stem.util.proc
import stem.util.str_tools
import stem.util.system

import nyx.controller
import nyx.curses
import nyx.panel
import nyx.popups
import nyx.tracker

from stem.util import conf, log
from nyx import msg, tor_controller

from nyx.curses import RED, GREEN, YELLOW, CYAN, WHITE, BOLD, HIGHLIGHT

MIN_DUAL_COL_WIDTH = 141  # minimum width where we'll show two columns
SHOW_FD_THRESHOLD = 60  # show file descriptor usage if usage is over this percentage
UPDATE_RATE = 5  # rate in seconds at which we refresh

CONFIG = conf.config_dict('nyx', {
  'attr.flag_colors': {},
  'attr.version_status_colors': {},
  'tor.chroot': '',
})


class HeaderPanel(nyx.panel.DaemonPanel):
  """
  Top area containing tor settings and system information.
  """

  def __init__(self):
    nyx.panel.DaemonPanel.__init__(self, UPDATE_RATE)
    self._vals = Sampling.create()

    self._last_width = nyx.curses.screen_size().width
    self._reported_inactive = False

    self._message = None
    self._message_attr = []

    tor_controller().add_status_listener(self.reset_listener)

  def show_message(self, message = None, *attr, **kwargs):
    """
    Sets the message displayed at the bottom of the header. If not called with
    anything it clears the override.

    :param str message: message to be displayed
    :param list attr: text attributes to apply
    :param int max_wait: seconds to wait for user input, no limit if **None**

    :returns: :class:`~nyx.curses.KeyInput` user pressed if provided a
      **max_wait**, **None** otherwise or if prompt was canceled
    """

    self._message = message
    self._message_attr = attr
    self.redraw()

    if 'max_wait' in kwargs:
      user_input = nyx.curses.key_input(kwargs['max_wait'])
      self.show_message()  # clear override
      return user_input

  def is_wide(self):
    """
    True if we should show two columns of information, False otherwise.
    """

    return self._last_width >= MIN_DUAL_COL_WIDTH

  def get_height(self):
    """
    Provides the height of the content, which is dynamically determined by the
    panel's maximum width.
    """

    if self._vals.is_relay:
      return 5 if self.is_wide() else 7
    else:
      return 4 if self.is_wide() else 5

  def send_newnym(self):
    """
    Requests a new identity and provides a visual queue.
    """

    controller = tor_controller()

    if not controller.is_newnym_available():
      return

    controller.signal(stem.Signal.NEWNYM)

    # If we're wide then the newnym label in this panel will give an
    # indication that the signal was sent. Otherwise use a msg.

    if not self.is_wide():
      self.show_message('Requesting a new identity', HIGHLIGHT, max_wait = 1)

  def key_handlers(self):
    def _reconnect():
      if self._vals.is_connected:
        return

      controller = tor_controller()
      self.show_message('Reconnecting...', HIGHLIGHT)

      try:
        try:
          controller.reconnect(chroot_path = CONFIG['tor.chroot'])
        except stem.connection.MissingPassword:
          password = nyx.controller.input_prompt('Controller Password: ')

          if password:
            controller.authenticate(password)

        log.notice("Reconnected to Tor's control port")
        self.show_message('Tor reconnected', HIGHLIGHT, max_wait = 1)
      except Exception as exc:
        self.show_message('Unable to reconnect (%s)' % exc, HIGHLIGHT, max_wait = 3)
        controller.close()

    return (
      nyx.panel.KeyHandler('n', action = self.send_newnym),
      nyx.panel.KeyHandler('r', action = _reconnect),
    )

  def draw(self, subwindow):
    vals = self._vals  # local reference to avoid concurrency concerns
    self._last_width = subwindow.width
    is_wide = self.is_wide()

    # space available for content

    nyx_controller = nyx.controller.get_controller()
    left_width = max(subwindow.width / 2, 77) if is_wide else subwindow.width
    right_width = subwindow.width - left_width
    pause_time = nyx_controller.get_pause_time() if nyx_controller.is_paused() else None

    _draw_platform_section(subwindow, 0, 0, left_width, vals)

    if vals.is_connected:
      _draw_ports_section(subwindow, 0, 1, left_width, vals)
    else:
      _draw_disconnected(subwindow, 0, 1, vals.last_heartbeat)

    if is_wide:
      _draw_resource_usage(subwindow, left_width, 0, right_width, vals, pause_time)

      if vals.is_relay:
        _draw_fingerprint_and_fd_usage(subwindow, left_width, 1, right_width, vals)
        _draw_flags(subwindow, 0, 2, vals.flags)
        _draw_exit_policy(subwindow, left_width, 2, vals.exit_policy)
      elif vals.is_connected:
        _draw_newnym_option(subwindow, left_width, 1, vals.newnym_wait)
    else:
      _draw_resource_usage(subwindow, 0, 2, left_width, vals, pause_time)

      if vals.is_relay:
        _draw_fingerprint_and_fd_usage(subwindow, 0, 3, left_width, vals)
        _draw_flags(subwindow, 0, 4, vals.flags)

    _draw_status(subwindow, 0, self.get_height() - 1, nyx_controller.is_paused(), self._message, *self._message_attr)

  def reset_listener(self, controller, event_type, _):
    self._update()

    if event_type == stem.control.State.CLOSED:
      log.notice('Tor control port closed')

  def _update(self):
    self._vals = Sampling.create(self._vals)

    if self._vals.fd_used and self._vals.fd_limit != -1:
      fd_percent = 100 * self._vals.fd_used / self._vals.fd_limit

      if fd_percent >= 90:
        log_msg = msg('panel.header.fd_used_at_ninety_percent', percentage = fd_percent)
        log.log_once('fd_used_at_ninety_percent', log.WARN, log_msg)
        log.DEDUPLICATION_MESSAGE_IDS.add('fd_used_at_sixty_percent')
      elif fd_percent >= 60:
        log_msg = msg('panel.header.fd_used_at_sixty_percent', percentage = fd_percent)
        log.log_once('fd_used_at_sixty_percent', log.NOTICE, log_msg)

    if self._vals.is_connected:
      if not self._reported_inactive and (time.time() - self._vals.last_heartbeat) >= 10:
        self._reported_inactive = True
        log.notice('Relay unresponsive (last heartbeat: %s)' % time.ctime(self._vals.last_heartbeat))
      elif self._reported_inactive and (time.time() - self._vals.last_heartbeat) < 10:
        self._reported_inactive = False
        log.notice('Relay resumed')

    self.redraw()


class Sampling(object):
  def __init__(self, **attr):
    self._attr = attr

    for key, value in attr.items():
      setattr(self, key, value)

  @staticmethod
  def create(last_sampling = None):
    controller = tor_controller()
    retrieved = time.time()

    pid = controller.get_pid('')
    tor_resources = nyx.tracker.get_resource_tracker().get_value()
    nyx_total_cpu_time = sum(os.times()[:3], stem.util.system.SYSTEM_CALL_TIME)

    or_listeners = controller.get_listeners(stem.control.Listener.OR, [])
    control_listeners = controller.get_listeners(stem.control.Listener.CONTROL, [])

    if controller.get_conf('HashedControlPassword', None):
      auth_type = 'password'
    elif controller.get_conf('CookieAuthentication', None) == '1':
      auth_type = 'cookie'
    else:
      auth_type = 'open'

    try:
      fd_used = stem.util.proc.file_descriptors_used(pid)
    except IOError:
      fd_used = None

    if last_sampling:
      nyx_cpu_delta = nyx_total_cpu_time - last_sampling.nyx_total_cpu_time
      nyx_time_delta = retrieved - last_sampling.retrieved
      nyx_cpu = nyx_cpu_delta / nyx_time_delta
    else:
      nyx_cpu = 0.0

    attr = {
      'retrieved': retrieved,
      'is_connected': controller.is_alive(),
      'connection_time': controller.connection_time(),
      'last_heartbeat': controller.get_latest_heartbeat(),

      'fingerprint': controller.get_info('fingerprint', 'Unknown'),
      'nickname': controller.get_conf('Nickname', ''),
      'newnym_wait': controller.get_newnym_wait(),
      'exit_policy': controller.get_exit_policy(None),
      'flags': getattr(controller.get_network_status(default = None), 'flags', []),

      'version': str(controller.get_version('Unknown')).split()[0],
      'version_status': controller.get_info('status/version/current', 'Unknown'),

      'address': or_listeners[0][0] if (or_listeners and or_listeners[0][0] != '0.0.0.0') else controller.get_info('address', 'Unknown'),
      'or_port': or_listeners[0][1] if or_listeners else '',
      'dir_port': controller.get_conf('DirPort', '0'),
      'control_port': str(control_listeners[0][1]) if control_listeners else None,
      'socket_path': controller.get_conf('ControlSocket', None),
      'is_relay': bool(or_listeners),

      'auth_type': auth_type,
      'pid': pid,
      'start_time': stem.util.system.start_time(pid),
      'fd_limit': int(controller.get_info('process/descriptor-limit', '-1')),
      'fd_used': fd_used,

      'nyx_total_cpu_time': nyx_total_cpu_time,
      'tor_cpu': '%0.1f' % (100 * tor_resources.cpu_sample),
      'nyx_cpu': '%0.1f' % (nyx_cpu),
      'memory': stem.util.str_tools.size_label(tor_resources.memory_bytes) if tor_resources.memory_bytes > 0 else 0,
      'memory_percent': '%0.1f' % (100 * tor_resources.memory_percent),

      'hostname': os.uname()[1],
      'platform': '%s %s' % (os.uname()[0], os.uname()[2]),  # [platform name] [version]
    }

    return Sampling(**attr)

  def format(self, message, crop_width = None):
    formatted_msg = message.format(**self._attr)

    if crop_width is not None:
      formatted_msg = stem.util.str_tools.crop(formatted_msg, crop_width)

    return formatted_msg


def _draw_platform_section(subwindow, x, y, width, vals):
  """
  Section providing the user's hostname, platform, and version information...

    nyx - odin (Linux 3.5.0-52-generic)        Tor 0.2.5.1-alpha-dev (unrecommended)
    |------ platform (40 characters) ------|   |----------- tor version -----------|
  """

  initial_x, space_left = x, min(width, 40)

  x = subwindow.addstr(x, y, vals.format('nyx - {hostname}', space_left))
  space_left -= x - initial_x

  if space_left >= 10:
    subwindow.addstr(x, y, ' (%s)' % vals.format('{platform}', space_left - 3))

  x, space_left = initial_x + 43, width - 43

  if vals.version != 'Unknown' and space_left >= 10:
    x = subwindow.addstr(x, y, vals.format('Tor {version}', space_left))
    space_left -= x - 43 - initial_x

    if space_left >= 7 + len(vals.version_status):
      version_color = CONFIG['attr.version_status_colors'].get(vals.version_status, WHITE)

      x = subwindow.addstr(x, y, ' (')
      x = subwindow.addstr(x, y, vals.version_status, version_color)
      subwindow.addstr(x, y, ')')


def _draw_ports_section(subwindow, x, y, width, vals):
  """
  Section providing our nickname, address, and port information...

    Unnamed - 0.0.0.0:7000, Control Port (cookie): 9051
  """

  if not vals.is_relay:
    x = subwindow.addstr(x, y, 'Relaying Disabled', CYAN)
  else:
    x = subwindow.addstr(x, y, vals.format('{nickname} - {address}:{or_port}'))

    if vals.dir_port != '0':
      x = subwindow.addstr(x, y, vals.format(', Dir Port: {dir_port}'))

  if vals.control_port:
    if width >= x + 19 + len(vals.control_port) + len(vals.auth_type):
      auth_color = RED if vals.auth_type == 'open' else GREEN

      x = subwindow.addstr(x, y, ', Control Port (')
      x = subwindow.addstr(x, y, vals.auth_type, auth_color)
      subwindow.addstr(x, y, vals.format('): {control_port}'))
    else:
      subwindow.addstr(x, y, vals.format(', Control Port: {control_port}'))
  elif vals.socket_path:
    subwindow.addstr(x, y, vals.format(', Control Socket: {socket_path}'))


def _draw_disconnected(subwindow, x, y, last_heartbeat):
  """
  Message indicating that tor is disconnected...

    Tor Disconnected (15:21 07/13/2014, press r to reconnect)
  """

  x = subwindow.addstr(x, y, 'Tor Disconnected', RED, BOLD)
  last_heartbeat_str = time.strftime('%H:%M %m/%d/%Y', time.localtime(last_heartbeat))
  subwindow.addstr(x, y, ' (%s, press r to reconnect)' % last_heartbeat_str)


def _draw_resource_usage(subwindow, x, y, width, vals, pause_time):
  """
  System resource usage of the tor process...

    cpu: 0.0% tor, 1.0% nyx    mem: 0 (0.0%)       pid: 16329  uptime: 12-20:42:07
  """

  if vals.start_time:
    if not vals.is_connected:
      now = vals.connection_time
    elif pause_time:
      now = pause_time
    else:
      now = time.time()

    uptime = stem.util.str_tools.short_time_label(now - vals.start_time)
  else:
    uptime = ''

  sys_fields = (
    (0, vals.format('cpu: {tor_cpu}% tor, {nyx_cpu}% nyx')),
    (27, vals.format('mem: {memory} ({memory_percent}%)')),
    (47, vals.format('pid: {pid}')),
    (59, 'uptime: %s' % uptime),
  )

  for (start, label) in sys_fields:
    if width >= start + len(label):
      subwindow.addstr(x + start, y, label)
    else:
      break


def _draw_fingerprint_and_fd_usage(subwindow, x, y, width, vals):
  """
  Presents our fingerprint, and our file descriptor usage if we're running
  out...

    fingerprint: 1A94D1A794FCB2F8B6CBC179EF8FDD4008A98D3B, file desc: 900 / 1000 (90%)
  """

  initial_x, space_left = x, width

  x = subwindow.addstr(x, y, vals.format('fingerprint: {fingerprint}', width))
  space_left -= x - initial_x

  if space_left >= 30 and vals.fd_used and vals.fd_limit != -1:
    fd_percent = 100 * vals.fd_used / vals.fd_limit

    if fd_percent >= SHOW_FD_THRESHOLD:
      if fd_percent >= 95:
        percentage_format = (RED, BOLD)
      elif fd_percent >= 90:
        percentage_format = (RED,)
      elif fd_percent >= 60:
        percentage_format = (YELLOW,)
      else:
        percentage_format = ()

      x = subwindow.addstr(x, y, ', file descriptors' if space_left >= 37 else ', file desc')
      x = subwindow.addstr(x, y, vals.format(': {fd_used} / {fd_limit} ('))
      x = subwindow.addstr(x, y, '%i%%' % fd_percent, *percentage_format)
      subwindow.addstr(x, y, ')')


def _draw_flags(subwindow, x, y, flags):
  """
  Presents flags held by our relay...

    flags: Running, Valid
  """

  x = subwindow.addstr(x, y, 'flags: ')

  if flags:
    for i, flag in enumerate(flags):
      flag_color = CONFIG['attr.flag_colors'].get(flag, WHITE)
      x = subwindow.addstr(x, y, flag, flag_color, BOLD)

      if i < len(flags) - 1:
        x = subwindow.addstr(x, y, ', ')
  else:
    subwindow.addstr(x, y, 'none', CYAN, BOLD)


def _draw_exit_policy(subwindow, x, y, exit_policy):
  """
  Presents our exit policy...

    exit policy: reject *:*
  """

  x = subwindow.addstr(x, y, 'exit policy: ')

  if not exit_policy:
    return

  rules = list(exit_policy.strip_private().strip_default())

  for i, rule in enumerate(rules):
    policy_color = GREEN if rule.is_accept else RED
    x = subwindow.addstr(x, y, str(rule), policy_color, BOLD)

    if i < len(rules) - 1:
      x = subwindow.addstr(x, y, ', ')

  if exit_policy.has_default():
    if rules:
      x = subwindow.addstr(x, y, ', ')

    subwindow.addstr(x, y, '<default>', CYAN, BOLD)


def _draw_newnym_option(subwindow, x, y, newnym_wait):
  """
  Provide a notice for requiesting a new identity, and time until it's next
  available if in the process of building circuits.
  """

  if newnym_wait == 0:
    subwindow.addstr(x, y, "press 'n' for a new identity")
  else:
    plural = 's' if newnym_wait > 1 else ''
    subwindow.addstr(x, y, 'building circuits, available again in %i second%s' % (newnym_wait, plural))


def _draw_status(subwindow, x, y, is_paused, message, *attr):
  """
  Provides general usage information or a custom message.
  """

  if message:
    subwindow.addstr(x, y, message, *attr)
  elif not is_paused:
    controller = nyx.controller.get_controller()
    subwindow.addstr(x, y, 'page %i / %i - m: menu, p: pause, h: page help, q: quit' % (controller.get_page() + 1, controller.get_page_count()))
  else:
    subwindow.addstr(x, y, 'Paused', HIGHLIGHT)
