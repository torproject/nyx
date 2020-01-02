# Copyright 2016-2020, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Panel providing raw control port access with syntax hilighting, usage
information, tab completion, and other usability features.
"""

import curses
import threading
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

    # cache for the line wrapped content we display

    self._wrapped_lines = []
    self._wrapped_line_lock = threading.RLock()
    self._wrapped_line_width = 80

    controller = tor_controller()
    self._autocompleter = stem.interpreter.autocomplete.Autocompleter(controller)
    self._interpreter = stem.interpreter.commands.ControlInterpreter(controller)

  def key_handlers(self):
    def _scroll(key):
      page_height = self.get_height() - 1
      is_changed = self._scroller.handle_key(key, len(self._get_lines()) + 1, page_height)

      if is_changed:
        self.redraw()

    def _prompt_input():
      _scroll(nyx.curses.KeyInput(curses.KEY_END))  # scroll to bottom
      self.redraw()

      return nyx.curses.str_input(
        4 + self._x_offset,
        self.get_top() + max(1, min(len(self._get_lines()) + 1, self.get_height() - 1)),
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

        self._add_line(_format_prompt_input(user_input, prompt))

        if response:
          for line in response.split('\n'):
            self._add_line([(text, attr) for text, attr in nyx.curses.asci_to_curses(line)])

      self.redraw()

    return (
      nyx.panel.KeyHandler('enter', 'execute a command', _start_input_mode, key_func = lambda key: key.is_selection()),
      nyx.panel.KeyHandler('arrows', 'scroll up and down', _scroll, key_func = lambda key: key.is_scroll()),
    )

  def _add_line(self, line):
    with self._wrapped_line_lock:
      self._lines.append(line)

      wrapped_line = []

      for text, attr in line:
        while text:
          wrapped_line.append((text[:self._wrapped_line_width], attr))
          text = text[self._wrapped_line_width:]

          if text:
            text = '  ' + text  # indent wrapped lines
            self._wrapped_lines.append(wrapped_line)
            wrapped_line = []

      self._wrapped_lines.append(wrapped_line)

  def _get_lines(self, width = None):
    with self._wrapped_line_lock:
      if width and width != self._wrapped_line_width:
        # Our panel size has changed. As such, line wrapping needs to be re-cached.

        lines = self._lines

        self._lines = []
        self._wrapped_lines = []
        self._wrapped_line_width = width

        for line in lines:
          self._add_line(line)

      return self._wrapped_lines

  def _draw(self, subwindow):
    if self._is_input_mode:
      subwindow.addstr(0, 0, 'Control Interpreter (enter "/help" for usage or a blank line to stop):', HIGHLIGHT)
    else:
      subwindow.addstr(0, 0, 'Control Interpreter:', HIGHLIGHT)

    lines = self._get_lines(subwindow.width - self._x_offset)

    scroll = self._scroller.location(len(lines) + 1, subwindow.height - 1)

    if self._interpreter.is_multiline_context:
      prompt = [MULTILINE_PROMPT]
    elif self._is_input_mode:
      prompt = [PROMPT]
    else:
      prompt = [PROMPT, PROMPT_USAGE]

    if len(lines) > subwindow.height - 2:
      self._x_offset = 2
      subwindow.scrollbar(1, scroll, len(lines) + 1)

    visible_lines = lines[scroll:scroll + subwindow.height - 1]

    if len(visible_lines) < subwindow.height - 1:
      visible_lines.append(prompt)

    for y, line in enumerate(visible_lines):
      x = self._x_offset

      for text, attr in line:
        x = subwindow.addstr(x, y + 1, text, *attr)
