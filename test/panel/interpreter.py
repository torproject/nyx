"""
Unit tests for nyx.panel.interpreter.
"""

import unittest

import nyx.curses
import nyx.panel.interpreter
import test

from mock import patch

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
>>> to use this panel press enter
""".strip()

EXPECTED_SCROLLBAR_PANEL = ' |>>> to use this panel press enter'


class TestInterpreter(unittest.TestCase):
  def test_format_input(self):
    user_input = 'getinfo'
    output = nyx.panel.interpreter.format_input(user_input)
    self.assertEqual(2, len(output))
    self.assertEqual(('>>> ', ('Green', 'Bold')), output[0])
    self.assertEqual(('getinfo ', ('Green', 'Bold')), output[1])

    user_input = 'getinfo version'
    output = nyx.panel.interpreter.format_input(user_input)
    self.assertEqual(3, len(output))
    self.assertEqual(('>>> ', ('Green', 'Bold')), output[0])
    self.assertEqual(('getinfo ', ('Green', 'Bold')), output[1])
    self.assertEqual(('version', ('Cyan', 'Bold')), output[2])

    user_input = '/help'
    output = nyx.panel.interpreter.format_input(user_input)
    self.assertEqual(2, len(output))
    self.assertEqual(('>>> ', ('Green', 'Bold')), output[0])
    self.assertEqual(('/help', ('Magenta', 'Bold')), output[1])

  @patch('nyx.panel.interpreter.tor_controller')
  def test_rendering_panel(self, tor_controller_mock):
    tor_controller_mock()._handle_event = lambda event: None
    panel = nyx.panel.interpreter.InterpreterPanel()
    self.assertEqual(EXPECTED_PANEL, test.render(panel._draw).content)

    panel._is_input_mode = True
    self.assertEqual(EXPECTED_PANEL_INPUT_MODE, test.render(panel._draw).content)

  @patch('nyx.panel.interpreter.tor_controller')
  def test_rendering_multiline_panel(self, tor_controller_mock):
    tor_controller_mock()._handle_event = lambda event: None
    panel = nyx.panel.interpreter.InterpreterPanel()
    panel._lines = [[('>>> ', ('Green', 'Bold')), ('GETINFO', ('Green', 'Bold')), (' version', ('Cyan',))]]
    panel._lines.append([('250-version=0.2.4.27 (git-412e3f7dc9c6c01a)', ('Blue',))])
    self.assertEqual(EXPECTED_MULTILINE_PANEL, test.render(panel._draw).content)

  @patch('nyx.panel.interpreter.tor_controller')
  def test_scrollbar(self, tor_controller_mock):
    tor_controller_mock()._handle_event = lambda event: None
    panel = nyx.panel.interpreter.InterpreterPanel()
    self.assertIsInstance(panel._scroller, nyx.curses.Scroller)

    height = panel.get_height()
    panel._last_content_height = height
    output_lines = test.render(panel._draw).content.split('\n')
    self.assertEqual(height, len(output_lines))
    self.assertEqual(EXPECTED_SCROLLBAR_PANEL, output_lines[1])

  @patch('nyx.panel.interpreter.tor_controller')
  def test_key_handlers(self, tor_controller_mock):
    tor_controller_mock()._handle_event = lambda event: None
    panel = nyx.panel.interpreter.InterpreterPanel()
    output = panel.key_handlers()
    self.assertEqual(2, len(output))
    self.assertEqual('enter', output[0].key)
    self.assertEqual('arrows', output[1].key)
