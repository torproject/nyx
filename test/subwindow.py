"""
Unit tests for nyx.curses. Not entirely sure why this file can't be called
'curses.py' but doing so causes the unittest module to fail internally.
"""

import unittest

import curses
import curses.ascii
import nyx.curses
import nyx.panel.interpreter
import test

from mock import call, Mock, patch

from test import require_curses

EXPECTED_ADDSTR_WRAP = """
0123456789 0123456789
0123456789 0123456789
0123456789 0123456789
0123456789 0123456789
0123456789 0123456789
""".strip()

EXPECTED_BOX = """
+---+
|   |
+---+
""".strip()

EXPECTED_SCROLLBAR_TOP = """
*|
*|
*|
 |
 |
 |
 |
 |
 |
-+
""".strip()

EXPECTED_SCROLLBAR_MIDDLE = """
 |
*|
*|
*|
 |
 |
 |
 |
 |
-+
""".strip()

EXPECTED_SCROLLBAR_BOTTOM = """
 |
 |
 |
 |
 |
 |
*|
*|
*|
-+
""".strip()

NO_OP_HANDLER = lambda key, textbox: None


class TestCurses(unittest.TestCase):
  @require_curses
  def test_addstr(self):
    def _draw(subwindow):
      subwindow.addstr(0, 0, '0123456789' * 10)

    # should be trimmed to the subwindow width (80 columns)

    self.assertEqual('01234567890123456789012345678901234567890123456789012345678901234567890123456789', test.render(_draw).content)

  @require_curses
  def test_addstr_wrap(self):
    def _draw(subwindow):
      subwindow.addstr_wrap(0, 0, '0123456789 ' * 10, 25)

    self.assertEqual(EXPECTED_ADDSTR_WRAP, test.render(_draw).content)

  @require_curses
  def test_addstr_wrap_single_long_word(self):
    def _draw(subwindow):
      subwindow.addstr_wrap(0, 0, '0123456789' * 10, 20)

    self.assertEqual('01234567890123456...', test.render(_draw).content)

  @require_curses
  def test_box(self):
    def _draw(subwindow):
      subwindow.box(width = 5, height = 3)

    self.assertEqual(EXPECTED_BOX, test.render(_draw).content)

  @require_curses
  def test_scrollbar_top(self):
    def _draw(subwindow):
      subwindow.scrollbar(15, 0, 30, fill_char = '*')

    self.assertEqual(EXPECTED_SCROLLBAR_TOP, test.render(_draw).content.strip())

  @require_curses
  def test_scrollbar_middle(self):
    def _draw(subwindow):
      subwindow.scrollbar(15, 1, 30, fill_char = '*')

    # even scrolling down just one index should be visible

    self.assertEqual(EXPECTED_SCROLLBAR_MIDDLE, test.render(_draw).content.strip())

  @require_curses
  def test_scrollbar_bottom(self):
    def _draw(subwindow):
      subwindow.scrollbar(15, 21, 30, fill_char = '*')

    self.assertEqual(EXPECTED_SCROLLBAR_BOTTOM, test.render(_draw).content.strip())

  def test_handle_key(self):
    dimensions = (40, 80)

    textbox = Mock()
    textbox.win.getyx.return_value = dimensions
    self.assertEqual(curses.ascii.BEL, nyx.curses._handle_key(textbox, 27))

    textbox = Mock()
    textbox.win.getyx.return_value = dimensions
    textbox.win.move = Mock()
    expected_call = call(dimensions[0], 0)
    nyx.curses._handle_key(textbox, curses.KEY_HOME)
    self.assertTrue(textbox.win.move.called)
    self.assertEquals(expected_call, textbox.win.move.call_args)

    textbox = Mock()
    textbox.win.getyx.return_value = dimensions
    textbox.gather.return_value = 'Sample Text'
    textbox.win.move = Mock()
    expected_call = call(*dimensions)
    nyx.curses._handle_key(textbox, curses.KEY_RIGHT)
    self.assertTrue(textbox.win.move.called)
    self.assertEquals(expected_call, textbox.win.move.call_args)

    textbox = Mock()
    textbox.win.getyx.return_value = dimensions
    self.assertEqual(curses.ascii.BEL, nyx.curses._handle_key(textbox, 410))

    textbox = Mock()
    textbox.win.getyx.return_value = dimensions
    key_pressed = ord('a')
    self.assertEqual(key_pressed, nyx.curses._handle_key(textbox, key_pressed))

  def test_handle_history_key(self):
    backlog = ['GETINFO version']
    dimensions = (40, 80)

    textbox = Mock()
    textbox.win.getyx.return_value = dimensions
    self.assertIsNone(nyx.curses._handle_history_key(NO_OP_HANDLER, [], textbox, curses.KEY_UP))

    textbox = Mock()
    textbox.win.getyx.return_value = dimensions
    textbox.win.getmaxyx.return_value = dimensions
    textbox.win.addstr = Mock()
    textbox.win.move = Mock()
    nyx.curses._handle_history_key(NO_OP_HANDLER, backlog, textbox, curses.KEY_UP)
    self.assertTrue(textbox.win.clear.called)
    expected_addstr_call = call(dimensions[0], 0, backlog[0])
    self.assertEqual(expected_addstr_call, textbox.win.addstr.call_args)
    expected_move_call = call(dimensions[0], len(backlog[0]))
    self.assertEqual(expected_move_call, textbox.win.move.call_args)

    textbox = Mock()
    mock_handle_key = Mock()
    nyx.curses._handle_history_key(mock_handle_key, [], textbox, curses.KEY_LEFT)
    self.assertTrue(mock_handle_key.called)

  @patch('nyx.curses._handle_history_key')
  def test_handle_tab_completion(self, mock_handle_history_key):
    dimensions = (40, 80)
    tab_completion_content = 'GETINFO version'

    textbox = Mock()
    textbox.win.getyx.return_value = dimensions
    textbox.win.getmaxyx.return_value = dimensions
    textbox.win.addstr = Mock()
    textbox.win.move = Mock()
    tab_completion = Mock()
    tab_completion.return_value = [tab_completion_content]
    nyx.curses._handle_tab_completion(NO_OP_HANDLER, tab_completion, textbox, 9)
    self.assertTrue(textbox.win.clear.called)
    expected_addstr_call = call(dimensions[0], 0, tab_completion_content)
    self.assertEqual(expected_addstr_call, textbox.win.addstr.call_args)
    expected_move_call = call(dimensions[0], len(tab_completion_content))
    self.assertTrue(expected_move_call, textbox.win.move.call_args)
