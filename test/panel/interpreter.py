"""
Unit tests for nyx.panel.interpreter.
"""

import unittest

import curses
import curses.ascii
import nyx.curses
import nyx.panel.interpreter
import test

from mock import call, Mock, patch

EXPECTED_PANEL = """
Control Interpreter:
>>> to use this panel press enter
""".strip()

EXPECTED_PANEL_INPUT_MODE = """
Control Interpreter (enter "/help" for usage or a blank line to stop):
>>> to use this panel press enter
""".strip()

EXPECTED_MULTILINE_PANEL = """
Control Interpreter:
>>> GETINFO version
250-version=0.2.4.27 (git-412e3f7dc9c6c01a)
""".strip()

EXPECTED_SCROLLBAR_PANEL = ' |>>> to use this panel press enter'


class TestInterpreter(unittest.TestCase):
  def test_ansi_to_output(self):
    ansi_text = '\x1b[32;1mthis is some sample text'
    output_line, attrs = nyx.panel.interpreter.ansi_to_output(ansi_text, [])

    self.assertEqual('this is some sample text', output_line[0][0])
    self.assertEqual('Green', output_line[0][1])
    self.assertEqual('Bold', output_line[0][2])
    self.assertEqual(['Green', 'Bold'], attrs)

  def test_format_input(self):
    user_input = 'getinfo'
    output = nyx.panel.interpreter.format_input(user_input)
    self.assertEqual(2, len(output))
    self.assertEqual(('>>> ', 'Green', 'Bold'), output[0])
    self.assertEqual(('getinfo ', 'Green', 'Bold'), output[1])

    user_input = 'getinfo version'
    output = nyx.panel.interpreter.format_input(user_input)
    self.assertEqual(3, len(output))
    self.assertEqual(('>>> ', 'Green', 'Bold'), output[0])
    self.assertEqual(('getinfo ', 'Green', 'Bold'), output[1])
    self.assertEqual(('version', 'Cyan', 'Bold'), output[2])

    user_input = '/help'
    output = nyx.panel.interpreter.format_input(user_input)
    self.assertEqual(2, len(output))
    self.assertEqual(('>>> ', 'Green', 'Bold'), output[0])
    self.assertEqual(('/help', 'Magenta', 'Bold'), output[1])

  def test_panel_name(self):
    panel = nyx.panel.interpreter.InterpreterPanel()
    self.assertEqual(panel.get_name(), 'interpreter')

  def test_rendering_panel(self):
    panel = nyx.panel.interpreter.InterpreterPanel()
    self.assertEqual(EXPECTED_PANEL, test.render(panel.draw).content)

    panel._is_input_mode = True
    self.assertEqual(EXPECTED_PANEL_INPUT_MODE, test.render(panel.draw).content)

  def test_rendering_multiline_panel(self):
    panel = nyx.panel.interpreter.InterpreterPanel()
    panel.prompt_line = [[('>>> ', 'Green', 'Bold'), ('GETINFO', 'Green', 'Bold'), (' version', 'Cyan')]]
    panel.prompt_line.append([('250-version=0.2.4.27 (git-412e3f7dc9c6c01a)', 'Blue')])
    self.assertEqual(EXPECTED_MULTILINE_PANEL, test.render(panel.draw).content)

  def test_scrollbar(self):
    panel = nyx.panel.interpreter.InterpreterPanel()
    self.assertIsInstance(panel._scroller, nyx.curses.Scroller)

    height = panel.get_preferred_size()[0]
    panel._last_content_height = height
    output_lines = test.render(panel.draw).content.split('\n')
    self.assertEqual(height, len(output_lines))
    self.assertEqual(EXPECTED_SCROLLBAR_PANEL, output_lines[1])

  def test_key_handlers(self):
    panel = nyx.panel.interpreter.InterpreterPanel()
    output = panel.key_handlers()
    self.assertEqual(2, len(output))
    self.assertEqual('enter', output[0].key)
    self.assertEqual('arrows', output[1].key)

  def test_str_input_handle_key(self):
    dimensions = (40, 80)

    textbox = Mock()
    textbox.win.getyx.return_value = dimensions
    self.assertEqual(curses.ascii.BEL, nyx.curses.str_input_handle_key(textbox, 27))

    textbox = Mock()
    textbox.win.getyx.return_value = dimensions
    textbox.win.move = Mock()
    expected_call = call(dimensions[0], 0)
    nyx.curses.str_input_handle_key(textbox, curses.KEY_HOME)
    self.assertTrue(textbox.win.move.called)
    self.assertEquals(expected_call, textbox.win.move.call_args)

    textbox = Mock()
    textbox.win.getyx.return_value = dimensions
    textbox.gather.return_value = 'Sample Text'
    textbox.win.move = Mock()
    expected_call = call(*dimensions)
    nyx.curses.str_input_handle_key(textbox, curses.KEY_RIGHT)
    self.assertTrue(textbox.win.move.called)
    self.assertEquals(expected_call, textbox.win.move.call_args)

    textbox = Mock()
    textbox.win.getyx.return_value = dimensions
    self.assertEqual(curses.ascii.BEL, nyx.curses.str_input_handle_key(textbox, 410))

    textbox = Mock()
    textbox.win.getyx.return_value = dimensions
    key_pressed = ord('a')
    self.assertEqual(key_pressed, nyx.curses.str_input_handle_key(textbox, key_pressed))

  @patch('nyx.curses.str_input_handle_key')
  def test_str_input_handle_history_key(self, mock_str_input_handle_key):
    backlog = ['GETINFO version']
    dimensions = (40, 80)

    textbox = Mock()
    textbox.win.getyx.return_value = dimensions
    self.assertIsNone(nyx.curses.str_input_handle_history_key(textbox, curses.KEY_UP, []))

    textbox = Mock()
    textbox.win.getyx.return_value = dimensions
    textbox.win.getmaxyx.return_value = dimensions
    textbox.win.addstr = Mock()
    textbox.win.move = Mock()
    nyx.curses.str_input_handle_history_key(textbox, curses.KEY_UP, backlog)
    self.assertTrue(textbox.win.clear.called)
    expected_addstr_call = call(dimensions[0], 0, backlog[0])
    self.assertEqual(expected_addstr_call, textbox.win.addstr.call_args)
    expected_move_call = call(dimensions[0], len(backlog[0]))
    self.assertEqual(expected_move_call, textbox.win.move.call_args)

    textbox = Mock()
    nyx.curses.str_input_handle_history_key(textbox, curses.KEY_LEFT, [])
    self.assertTrue(mock_str_input_handle_key.called)

  @patch('nyx.curses.str_input_handle_history_key')
  def test_str_input_handle_tab_completion(self, mock_str_input_handle_history_key):
    dimensions = (40, 80)
    tab_completion_content = 'GETINFO version'

    textbox = Mock()
    textbox.win.getyx.return_value = dimensions
    textbox.win.getmaxyx.return_value = dimensions
    textbox.win.addstr = Mock()
    textbox.win.move = Mock()
    tab_completion = Mock()
    tab_completion.return_value = [tab_completion_content]
    nyx.curses.str_input_handle_tab_completion(textbox, 9, [], tab_completion)
    self.assertTrue(textbox.win.clear.called)
    expected_addstr_call = call(dimensions[0], 0, tab_completion_content)
    self.assertEqual(expected_addstr_call, textbox.win.addstr.call_args)
    expected_move_call = call(dimensions[0], len(tab_completion_content))
    self.assertTrue(expected_move_call, textbox.win.move.call_args)
