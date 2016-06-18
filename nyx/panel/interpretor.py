"""
Panel providing raw control port access with syntax hilighting, usage
information, tab completion, and other usability features.
"""

import nyx.curses

from nyx.curses import GREEN, CYAN, BOLD, HIGHLIGHT
from nyx import panel


USAGE_INFO = "to use this panel press enter"
PROMPT = ">>> "
PROMPT_LINE = [[(PROMPT, GREEN, BOLD), (USAGE_INFO, CYAN, BOLD)]]

class InterpretorPanel(panel.Panel):
  """
  Renders the current torrc or nyxrc with syntax highlighting in a scrollable
  area.
  """

  def __init__(self):
    panel.Panel.__init__(self, 'interpretor')

    self._is_input_mode = False

  def key_handlers(self):
    def _execute_command():
      self._is_input_mode ^= True
      self.redraw(True)

    return (
      nyx.panel.KeyHandler('enter', 'execute a command', _execute_command, key_func = lambda key: key.is_selection()),
    )

  def draw(self, width, height):
    usage_msg = " (enter \"/help\" for usage or a blank line to stop)" if self._is_input_mode else ""
    self.addstr(0, 0, 'Control Interpretor%s:' % usage_msg, HIGHLIGHT)

    x_offset = 0
    draw_line = 1
    for entry in PROMPT_LINE:
      cursor = x_offset

      for msg, color, attr in entry:
        self.addstr(draw_line, cursor, msg, color, attr)
        cursor += len(msg)

      draw_line += 1
