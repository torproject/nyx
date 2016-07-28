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


BACKLOG_LIMIT = 100
PROMPT = [('>>> ', (GREEN, BOLD)), ('to use this panel press enter', (CYAN, BOLD))]


def format_input(user_input):
  output = [('>>> ', (GREEN, BOLD))]

  if ' ' in user_input:
    cmd, arg = user_input.split(' ', 1)
  else:
    cmd, arg = user_input, ''

  if cmd.startswith('/'):
    output.append((user_input, (MAGENTA, BOLD)))
  else:
    output.append((cmd + ' ', (GREEN, BOLD)))
    if arg:
      output.append((arg, (CYAN, BOLD)))

  return output


class InterpreterPanel(panel.Panel):
  """
  Renders the interpreter panel with a prompt providing raw control port
  access.
  """

  def __init__(self):
    panel.Panel.__init__(self)

    self._is_input_mode = False
    self._x_offset = 0
    self._scroller = nyx.curses.Scroller()
    self._lines = []
    self._backlog = []  # previous user inputs

    controller = tor_controller()
    self._autocompleter = stem.interpreter.autocomplete.Autocompleter(controller)
    self._interpreter = stem.interpreter.commands.ControlInterpretor(controller)

  def key_handlers(self):
    def _scroll(key):
      page_height = self.get_height() - 1
      is_changed = self._scroller.handle_key(key, len(self._lines) + 1, page_height)

      if is_changed:
        self.redraw()

    def _execute_command():
      self._is_input_mode = True

      while self._is_input_mode:
        self.redraw()
        _scroll(nyx.curses.KeyInput(curses.KEY_END))
        page_height = self.get_height() - 1
        user_input = nyx.curses.str_input(4 + self._x_offset, self.get_top() + max(len(self._lines[-page_height:]), 1), '', self._backlog, self._autocompleter.matches)
        user_input, is_done = user_input.strip(), False

        if not user_input:
          is_done = True
        else:
          self._backlog.append(user_input)

          if len(self._backlog) > BACKLOG_LIMIT:
            self._backlog = self._backlog[-BACKLOG_LIMIT:]

          try:
            console_called = False

            with patch('stem.interpreter.commands.code.InteractiveConsole.push') as console_push:
              response = self._interpreter.run_command(user_input)

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
              self._lines.append(format_input(user_input))

              for line in response.split('\n'):
                self._lines.append([(text, attr) for text, attr in nyx.curses.asci_to_curses(line)])
          except stem.SocketClosed:
            is_done = True

        if is_done:
          self._is_input_mode = False
          self.redraw()

    return (
      nyx.panel.KeyHandler('enter', 'execute a command', _execute_command, key_func = lambda key: key.is_selection()),
      nyx.panel.KeyHandler('arrows', 'scroll up and down', _scroll, key_func = lambda key: key.is_scroll()),
    )

  def _draw(self, subwindow):
    if self._is_input_mode:
      subwindow.addstr(0, 0, 'Control Interpreter (enter "/help" for usage or a blank line to stop):', HIGHLIGHT)
    else:
      subwindow.addstr(0, 0, 'Control Interpreter:', HIGHLIGHT)

    scroll = self._scroller.location(len(self._lines) + 1, subwindow.height - 1)

    if len(self._lines) > subwindow.height - 2:
      self._x_offset = 2
      subwindow.scrollbar(1, scroll, len(self._lines))

    for i, line in enumerate(self._lines + [PROMPT]):
      x, y = self._x_offset, i + 1 - scroll

      if y > 0:
        for text, attr in line:
          x = subwindow.addstr(x, y, text, *attr)
