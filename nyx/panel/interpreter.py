"""
Panel providing raw control port access with syntax hilighting, usage
information, tab completion, and other usability features.
"""

import nyx.controller
import nyx.curses

from nyx.curses import GREEN, CYAN, BOLD, HIGHLIGHT
from nyx import panel, tor_interpreter


USAGE_INFO = "to use this panel press enter"
PROMPT = ">>> "
PROMPT_LINE = [[(PROMPT, GREEN, BOLD), (USAGE_INFO, CYAN, BOLD)]]

class InterpreterPanel(panel.Panel):
  """
  Renders the interpreter panel with a prompt providing raw control port
  access.
  """

  def __init__(self):
    panel.Panel.__init__(self, 'interpreter')

    self._is_input_mode = False
    self.interpreter = tor_interpreter.ControlInterpreter()

  def key_handlers(self):
    def _execute_command():
      self._is_input_mode = True

      while self._is_input_mode:
        self.redraw(True)
        user_input = nyx.curses.str_input(len(PROMPT), self.top + len(PROMPT_LINE))
        user_input, is_done = user_input.strip(), False

        if not user_input:
          is_done = True
        else:
          try:
            input_entry, output_entry = self.interpreter.handle_query(user_input)
            input_entry.insert(0, (PROMPT, GREEN, BOLD))
            PROMPT_LINE.insert(len(PROMPT_LINE) - 1, input_entry)
            for line in output_entry:
              PROMPT_LINE.insert(len(PROMPT_LINE) - 1, line)
          except tor_interpreter.InterpreterClosed:
            is_done = True

        if is_done:
          self._is_input_mode = False
          self.redraw(True)

    return (
      nyx.panel.KeyHandler('enter', 'execute a command', _execute_command, key_func = lambda key: key.is_selection()),
    )

  def draw(self, width, height):
    usage_msg = " (enter \"/help\" for usage or a blank line to stop)" if self._is_input_mode else ""
    self.addstr(0, 0, 'Control Interpreter%s:' % usage_msg, HIGHLIGHT)

    x_offset = 0
    draw_line = 1
    for entry in PROMPT_LINE:
      cursor = x_offset

      msg, color, attr = None, None, None
      for line in entry:
        if len(line) == 2:
          self.addstr(draw_line, cursor, line[0], line[1])
        elif len(line) == 3:
          self.addstr(draw_line, cursor, line[0], line[1], line[2])
        cursor += len(line[0])

      draw_line += 1
