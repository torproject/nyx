"""
Unit tests for nyx.panel.interpreter.
"""

import unittest

import nyx.panel.interpreter


class TestInterpreter(unittest.TestCase):
  def test_ansi_to_output(self):
    ansi_text = '\x1b[32;1mthis is some sample text'
    output_line, attrs = nyx.panel.interpreter.ansi_to_output(ansi_text, [])

    self.assertEqual('this is some sample text', output_line[0][0])
    self.assertEqual('Green', output_line[0][1])
    self.assertEqual('Bold', output_line[0][2])
    self.assertEqual(['Green', 'Bold'], attrs)
