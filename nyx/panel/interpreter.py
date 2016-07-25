"""
Panel providing raw control port access with syntax hilighting, usage
information, tab completion, and other usability features.
"""

import code
import curses
import nyx.controller
import nyx.curses
import sys

from cStringIO import StringIO
from mock import patch
from nyx.curses import GREEN, MAGENTA, CYAN, BOLD, HIGHLIGHT
from nyx import tor_controller, panel

import stem
import stem.interpreter.autocomplete
import stem.interpreter.commands


USAGE_INFO = 'to use this panel press enter'
PROMPT = '>>> '
BACKLOG_LIMIT = 100


def format_input(user_input):
  output = [(PROMPT, GREEN, BOLD)]

  if ' ' in user_input:
    cmd, arg = user_input.split(' ', 1)
  else:
    cmd, arg = user_input, ''

  if cmd.startswith('/'):
    output.append((user_input, MAGENTA, BOLD))
  else:
    output.append((cmd + ' ', GREEN, BOLD))
    if arg:
      output.append((arg, CYAN, BOLD))

  return output


class InterpreterPanel(panel.Panel):
  """
  Renders the interpreter panel with a prompt providing raw control port
  access.
  """

  def __init__(self):
    panel.Panel.__init__(self)

    self._is_input_mode = False
    self._last_content_height = 0
    self._x_offset = 0
    self._scroller = nyx.curses.Scroller()
    self._backlog = []

    controller = tor_controller()
    self.autocompleter = stem.interpreter.autocomplete.Autocompleter(controller)
    self.interpreter = stem.interpreter.commands.ControlInterpretor(controller)
    self.prompt_line = [[(PROMPT, GREEN, BOLD), (USAGE_INFO, CYAN, BOLD)]]

  def key_handlers(self):
    def _scroll(key):
      page_height = self.get_height() - 1
      is_changed = self._scroller.handle_key(key, self._last_content_height, page_height)

      if is_changed:
        self.redraw(True)

    def _execute_command():
      self._is_input_mode = True

      while self._is_input_mode:
        self.redraw(True)
        _scroll(nyx.curses.KeyInput(curses.KEY_END))
        page_height = self.get_height() - 1
        user_input = nyx.curses.str_input(len(PROMPT) + self._x_offset, self.get_top() + len(self.prompt_line[-page_height:]), '', list(reversed(self._backlog)), self.autocompleter.matches)
        user_input, is_done = user_input.strip(), False

        if not user_input:
          is_done = True
        else:
          self._backlog.append(user_input)
          backlog_crop = len(self._backlog) - BACKLOG_LIMIT
          if backlog_crop > 0:
            raise Exception(self._backlog)
            self._backlog = self._backlog[backlog_crop:]

          try:
            console_called = False
            with patch('stem.interpreter.commands.code.InteractiveConsole.push') as console_push:
              response = self.interpreter.run_command(user_input)
              if console_push.called:
                console_called = True

            if console_called:
              old_stderr = sys.stderr
              sys.stderr = new_stderr = StringIO()
              interactive_console = code.InteractiveConsole()
              interactive_console.push(user_input)
              sys.stderr = old_stderr
              response = '\x1b[31;1m' + new_stderr.getvalue()
              sys.stderr = old_stderr
            if response:
              self.prompt_line.insert(len(self.prompt_line) - 1, format_input(user_input))

              for line in response.split('\n'):
                new_line = []

                for text, attr in nyx.curses.asci_to_curses(line):
                  new_line.append([text] + list(attr))

                self.prompt_line.insert(len(self.prompt_line) - 1, new_line)
          except stem.SocketClosed:
            is_done = True

        if is_done:
          self._is_input_mode = False
          self.redraw(True)

    return (
      nyx.panel.KeyHandler('enter', 'execute a command', _execute_command, key_func = lambda key: key.is_selection()),
      nyx.panel.KeyHandler('arrows', 'scroll up and down', _scroll, key_func = lambda key: key.is_scroll()),
    )

  def _draw(self, subwindow):
    scroll = self._scroller.location(self._last_content_height, subwindow.height - 1)

    if self._last_content_height > subwindow.height - 1:
      self._x_offset = 2
      subwindow.scrollbar(1, scroll, self._last_content_height - 1)

    y = 1 - scroll
    for entry in self.prompt_line:
      cursor = self._x_offset

      for line in entry:
        if len(line) == 2:
          subwindow.addstr(cursor, y, line[0], line[1])
        elif len(line) == 3:
          subwindow.addstr(cursor, y, line[0], line[1], line[2])
        try:
          cursor += len(line[0])
        except:
          pass

      y += 1

    subwindow.addstr(0, 0, ' ' * subwindow.width)
    usage_msg = ' (enter \"/help\" for usage or a blank line to stop)' if self._is_input_mode else ""
    subwindow.addstr(0, 0, 'Control Interpreter%s:' % usage_msg, HIGHLIGHT)

    new_content_height = y + scroll - 1
    if new_content_height != self._last_content_height:
      self._last_content_height = new_content_height
      self.redraw(True)
