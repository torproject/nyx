"""
Unit tests for nyx.panel.interpreter.
"""

import unittest

import nyx.curses
import nyx.panel.interpreter
import test

from test import require_curses

try:
  # added in python 3.3
  from unittest.mock import patch
except ImportError:
  from mock import patch

EXPECTED_PANEL = """
Control Interpreter:
>>> to use this panel press enter
""".strip()

EXPECTED_PANEL_INPUT_MODE = """
Control Interpreter (enter "/help" for usage or a blank line to stop):
>>>
""".strip()

EXPECTED_MULTILINE_PANEL = """
Control Interpreter:
>>> GETINFO version
250-version=0.2.4.27 (git-412e3f7dc9c6c01a)
>>> to use this panel press enter
""".strip()

EXPECTED_WITH_SCROLLBAR = """
Control Interpreter:
 |>>> GETINFO version
 |250-version=0.2.4.27 (git-412e3f7dc9c6c01a)
 |
 |
 |
 |
 |
 |
 |
 |
 |
 |
 |
 |
 |
 |
 |
 |
 |
 |
 |
 |
 |
-+
""".strip()


class TestInterpreter(unittest.TestCase):
  def test_format_prompt_input_with_interperter_command(self):
    output = nyx.panel.interpreter._format_prompt_input('/help')
    self.assertEqual(2, len(output))
    self.assertEqual(('>>> ', ('Green', 'Bold')), output[0])
    self.assertEqual(('/help', ('Magenta', 'Bold')), output[1])

  def test_format_prompt_input_with_command(self):
    output = nyx.panel.interpreter._format_prompt_input('GETINFO')
    self.assertEqual(2, len(output))
    self.assertEqual(('>>> ', ('Green', 'Bold')), output[0])
    self.assertEqual(('GETINFO ', ('Green', 'Bold')), output[1])

  def test_format_prompt_input_with_command_and_arg(self):
    output = nyx.panel.interpreter._format_prompt_input('GETINFO version')
    self.assertEqual(3, len(output))
    self.assertEqual(('>>> ', ('Green', 'Bold')), output[0])
    self.assertEqual(('GETINFO ', ('Green', 'Bold')), output[1])
    self.assertEqual(('version', ('Cyan', 'Bold')), output[2])

  @require_curses
  @patch('nyx.panel.interpreter.tor_controller')
  def test_blank_panel(self, tor_controller_mock):
    tor_controller_mock()._handle_event = lambda event: None

    panel = nyx.panel.interpreter.InterpreterPanel()
    self.assertEqual(EXPECTED_PANEL, test.render(panel._draw).content)

    panel._is_input_mode = True
    self.assertEqual(EXPECTED_PANEL_INPUT_MODE, test.render(panel._draw).content)

  @require_curses
  @patch('nyx.panel.interpreter.tor_controller')
  def test_multiline_panel(self, tor_controller_mock):
    tor_controller_mock()._handle_event = lambda event: None

    panel = nyx.panel.interpreter.InterpreterPanel()
    panel._lines = [
      [('>>> ', ('Green', 'Bold')), ('GETINFO', ('Green', 'Bold')), (' version', ('Cyan',))],
      [('250-version=0.2.4.27 (git-412e3f7dc9c6c01a)', ('Blue',))]
    ]

    self.assertEqual(EXPECTED_MULTILINE_PANEL, test.render(panel._draw).content)

  @require_curses
  @patch('nyx.panel.interpreter.tor_controller')
  def test_scrollbar(self, tor_controller_mock):
    tor_controller_mock()._handle_event = lambda event: None

    panel = nyx.panel.interpreter.InterpreterPanel()
    panel._lines = [
      [('>>> ', ('Green', 'Bold')), ('GETINFO', ('Green', 'Bold')), (' version', ('Cyan',))],
      [('250-version=0.2.4.27 (git-412e3f7dc9c6c01a)', ('Blue',))]
    ] + [()] * (panel.get_height() - 2)

    self.assertEqual(EXPECTED_WITH_SCROLLBAR, test.render(panel._draw).content)
