from nyx.curses import GREEN, CYAN, BOLD, HIGHLIGHT
from nyx import tor_controller


def handle_query(user_input):
  user_input = user_input.strip()

  input_entry, output_entry = [], []

  if " " in user_input: cmd, arg = user_input.split(" ", 1)
  else: cmd, arg = user_input, ""

  cmd = cmd.upper()
  input_entry.append((cmd + " ", GREEN, BOLD))
  if arg:
    input_entry.append((arg, CYAN, BOLD))

  if cmd == "GETINFO":
    resp = tor_controller().get_info(arg)
    for line in resp.split('\n'):
      output_entry.append((line, CYAN,))
    
  return input_entry, output_entry
