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

from mock import patch, call, Mock

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
DIMENSIONS = (40, 80)


def _textbox(x = 0):
  textbox = Mock()
  textbox.win.getyx.return_value = (0, x)
  return textbox


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

  def test_handle_key_with_text(self):
    self.assertEqual(ord('a'), nyx.curses._handle_key(_textbox(), ord('a')))

  def test_handle_key_with_esc(self):
    self.assertEqual(curses.ascii.BEL, nyx.curses._handle_key(_textbox(), 27))

  def test_handle_key_with_home(self):
    textbox = _textbox()
    nyx.curses._handle_key(textbox, curses.KEY_HOME)
    self.assertEquals(call(0, 0), textbox.win.move.call_args)

  def test_handle_key_with_end(self):
    textbox = _textbox()
    textbox.gather.return_value = 'Sample Text'
    nyx.curses._handle_key(textbox, curses.KEY_END)
    self.assertEquals(call(0, 10), textbox.win.move.call_args)

  def test_handle_key_with_right_arrow(self):
    textbox = _textbox()
    textbox.gather.return_value = 'Sample Text'
    nyx.curses._handle_key(textbox, curses.KEY_RIGHT)

    # move is called twice, to revert the gather() and move the cursor

    self.assertEquals(2, textbox.win.move.call_count)
    self.assertEquals(call(0, 1), textbox.win.move.call_args)

  def test_handle_key_with_right_arrow_at_end(self):
    textbox = _textbox(10)
    textbox.gather.return_value = 'Sample Text'
    nyx.curses._handle_key(textbox, curses.KEY_RIGHT)

    # move is only called to revert the gather()

    self.assertEquals(1, textbox.win.move.call_count)
    self.assertEquals(call(0, 10), textbox.win.move.call_args)

  def test_handle_key_when_resized(self):
    self.assertEqual(curses.ascii.BEL, nyx.curses._handle_key(_textbox(), 410))

  def test_handle_history_key(self):
    backlog = ['GETINFO version']

    textbox = Mock()
    textbox.win.getyx.return_value = DIMENSIONS
    self.assertIsNone(nyx.curses._handle_history_key(NO_OP_HANDLER, [], textbox, curses.KEY_UP))

    textbox = Mock()
    textbox.win.getyx.return_value = DIMENSIONS
    textbox.win.getmaxyx.return_value = DIMENSIONS
    textbox.win.addstr = Mock()
    textbox.win.move = Mock()
    nyx.curses._handle_history_key(NO_OP_HANDLER, backlog, textbox, curses.KEY_UP)
    self.assertTrue(textbox.win.clear.called)
    expected_addstr_call = call(DIMENSIONS[0], 0, backlog[0])
    self.assertEqual(expected_addstr_call, textbox.win.addstr.call_args)
    expected_move_call = call(DIMENSIONS[0], len(backlog[0]))
    self.assertEqual(expected_move_call, textbox.win.move.call_args)

    textbox = Mock()
    mock_handle_key = Mock()
    nyx.curses._handle_history_key(mock_handle_key, [], textbox, curses.KEY_LEFT)
    self.assertTrue(mock_handle_key.called)

  @patch('nyx.curses._handle_history_key')
  def test_handle_tab_completion(self, mock_handle_history_key):
    tab_completion_content = 'GETINFO version'

    textbox = Mock()
    textbox.win.getyx.return_value = DIMENSIONS
    textbox.win.getmaxyx.return_value = DIMENSIONS
    textbox.win.addstr = Mock()
    textbox.win.move = Mock()
    tab_completion = Mock()
    tab_completion.return_value = [tab_completion_content]
    nyx.curses._handle_tab_completion(NO_OP_HANDLER, tab_completion, textbox, 9)
    self.assertTrue(textbox.win.clear.called)
    expected_addstr_call = call(DIMENSIONS[0], 0, tab_completion_content)
    self.assertEqual(expected_addstr_call, textbox.win.addstr.call_args)
    expected_move_call = call(DIMENSIONS[0], len(tab_completion_content))
    self.assertTrue(expected_move_call, textbox.win.move.call_args)
