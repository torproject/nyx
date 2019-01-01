# Copyright 2016-2019, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Panel providing raw control port access with syntax hilighting, usage
information, tab completion, and other usability features.
"""

import curses
import nyx.curses
import nyx.panel

import stem
import stem.interpreter.autocomplete
import stem.interpreter.commands

from nyx import tor_controller
from nyx.curses import GREEN, MAGENTA, CYAN, BOLD, HIGHLIGHT

USER_INPUT_BACKLOG_LIMIT = 100

PROMPT = ('>>> ', (GREEN, BOLD))
MULTILINE_PROMPT = ('... ', ())
PROMPT_USAGE = ('to use this panel press enter', (CYAN, BOLD))


def _format_prompt_input(user_input, prompt = PROMPT):
  line = [prompt]
  cmd, arg = user_input.split(' ', 1) if ' ' in user_input else (user_input, '')

  if cmd.startswith('/'):
    line.append((user_input, (MAGENTA, BOLD)))
  else:
    line.append((cmd + ' ', (GREEN, BOLD)))

    if arg:
      line.append((arg, (CYAN, BOLD)))

  return line


class InterpreterPanel(nyx.panel.Panel):
  """
  Prompt with raw control port access.
  """

  def __init__(self):
    nyx.panel.Panel.__init__(self)

    self._is_input_mode = False
    self._x_offset = 0
    self._scroller = nyx.curses.Scroller()
    self._lines = []
    self._user_inputs = []  # previous user inputs

    controller = tor_controller()
    self._autocompleter = stem.interpreter.autocomplete.Autocompleter(controller)
    self._interpreter = stem.interpreter.commands.ControlInterpreter(controller)

  def key_handlers(self):
    def _scroll(key):
      page_height = self.get_height() - 1
      is_changed = self._scroller.handle_key(key, len(self._lines) + 1, page_height)

      if is_changed:
        self.redraw()

    def _prompt_input():
      _scroll(nyx.curses.KeyInput(curses.KEY_END))  # scroll to bottom
      self.redraw()

      return nyx.curses.str_input(
        4 + self._x_offset,
        self.get_top() + max(1, min(len(self._lines) + 1, self.get_height() - 1)),
        backlog = self._user_inputs,
        tab_completion = self._autocompleter.matches
      )

    def _start_input_mode():
      self._is_input_mode = True

      while self._is_input_mode:
        user_input = _prompt_input()

        if not user_input and not self._interpreter.is_multiline_context:
          self._is_input_mode = False
          break

        self._user_inputs.append(user_input)
        prompt = MULTILINE_PROMPT if self._interpreter.is_multiline_context else PROMPT

        if len(self._user_inputs) > USER_INPUT_BACKLOG_LIMIT:
          self._user_inputs = self._user_inputs[-USER_INPUT_BACKLOG_LIMIT:]

        try:
          response = self._interpreter.run_command(user_input)
        except stem.SocketClosed:
          self._is_input_mode = False
          break

        self._lines.append(_format_prompt_input(user_input, prompt))

        if response:
          for line in response.split('\n'):
            self._lines.append([(text, attr) for text, attr in nyx.curses.asci_to_curses(line)])

      self.redraw()

    return (
      nyx.panel.KeyHandler('enter', 'execute a command', _start_input_mode, key_func = lambda key: key.is_selection()),
      nyx.panel.KeyHandler('arrows', 'scroll up and down', _scroll, key_func = lambda key: key.is_scroll()),
    )

  def _draw(self, subwindow):
    if self._is_input_mode:
      subwindow.addstr(0, 0, 'Control Interpreter (enter "/help" for usage or a blank line to stop):', HIGHLIGHT)
    else:
      subwindow.addstr(0, 0, 'Control Interpreter:', HIGHLIGHT)

    scroll = self._scroller.location(len(self._lines) + 1, subwindow.height - 1)

    if self._interpreter.is_multiline_context:
      prompt = [MULTILINE_PROMPT]
    elif self._is_input_mode:
      prompt = [PROMPT]
    else:
      prompt = [PROMPT, PROMPT_USAGE]

    if len(self._lines) > subwindow.height - 2:
      self._x_offset = 2
      subwindow.scrollbar(1, scroll, len(self._lines) + 1)

    for i, line in enumerate(self._lines + [prompt]):
      x, y = self._x_offset, i + 1 - scroll

      if y > 0:
        for text, attr in line:
          x = subwindow.addstr(x, y, text, *attr)
