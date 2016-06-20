from nyx.curses import GREEN, CYAN, RED, MAGENTA, BOLD, HIGHLIGHT
from nyx import tor_controller


def handle_query(user_input):
  """
  Processes the given input. Requests starting with a '/' are special
  commands to the interpretor, and anything else is sent to the control port.
  This returns an input/output tuple, each entry being a list of lines, each
  line having a list of (msg, format) tuples for the content to be displayed.
  This raises a InterpretorClosed if the interpretor should be shut down.

  Arguments:
    user_input - user input to be processed
  """

  user_input = user_input.strip()

  input_entry, output_entry = [], []

  if " " in user_input: cmd, arg = user_input.split(" ", 1)
  else: cmd, arg = user_input, ""

  if cmd.startswith("/"):
    input_entry.append((cmd, MAGENTA, BOLD))
    if cmd == "/quit": raise InterpreterClosed()
    else:
      output_entry.append(("Not yet implemented...", RED, BOLD))
  else:
    cmd = cmd.upper()
    input_entry.append((cmd + " ", GREEN, BOLD))
    if arg:
      input_entry.append((arg, CYAN, BOLD))

    if cmd == "GETINFO":
      resp = tor_controller().get_info(arg)
      for line in resp.split('\n'):
        output_entry.append((line, CYAN,))
    
  return input_entry, output_entry


class InterpreterClosed(Exception):
  """
  Exception raised when the interpreter should be shut down.
  """

  pass
