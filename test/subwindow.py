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

from test import require_curses
from nyx.curses import Color, Attr

try:
  # added in python 3.3
  from unittest.mock import call, Mock
except ImportError:
  from mock import call, Mock

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
*|
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
*|
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
*|
*|
*|
*|
-+
""".strip()

DIMENSIONS = (40, 80)


def no_op_handler(textbox, key):
  return key


def _textbox(x = 0, text = ''):
  textbox = Mock()
  textbox.win.getyx.return_value = (0, x)
  textbox.win.getmaxyx.return_value = (0, 40)  # allow up to forty characters
  textbox.gather.return_value = text
  return textbox


class TestCurses(unittest.TestCase):
  def test_asci_to_curses(self):
    self.assertEqual([], nyx.curses.asci_to_curses(''))
    self.assertEqual([('hi!', ())], nyx.curses.asci_to_curses('hi!'))
    self.assertEqual([('hi!', (Color.RED,))], nyx.curses.asci_to_curses('\x1b[31mhi!\x1b[0m'))
    self.assertEqual([('boo', ()), ('hi!', (Color.RED, Attr.BOLD))], nyx.curses.asci_to_curses('boo\x1b[31;1mhi!\x1b[0m'))
    self.assertEqual([('boo', ()), ('hi', (Color.RED,)), (' dami!', (Color.RED, Attr.BOLD))], nyx.curses.asci_to_curses('boo\x1b[31mhi\x1b[1m dami!\x1b[0m'))
    self.assertEqual([('boo', ()), ('hi', (Color.RED,)), (' dami!', (Color.BLUE,))], nyx.curses.asci_to_curses('boo\x1b[31mhi\x1b[34m dami!\x1b[0m'))
    self.assertEqual([('boo', ()), ('hi!', (Color.RED, Attr.BOLD)), ('and bye!', ())], nyx.curses.asci_to_curses('boo\x1b[31;1mhi!\x1b[0mand bye!'))

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
      subwindow.scrollbar(15, 20, 30, fill_char = '*')

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
    textbox = _textbox(x = 10)
    textbox.gather.return_value = 'Sample Text'
    nyx.curses._handle_key(textbox, curses.KEY_RIGHT)

    # move is only called to revert the gather()

    self.assertEquals(1, textbox.win.move.call_count)
    self.assertEquals(call(0, 10), textbox.win.move.call_args)

  def test_handle_key_when_resized(self):
    self.assertEqual(curses.ascii.BEL, nyx.curses._handle_key(_textbox(), 410))

  def test_handle_tab_completion_no_op(self):
    result = nyx.curses._handle_tab_completion(no_op_handler, lambda txt_input: ['GETINFO version'], _textbox(), ord('a'))
    self.assertEqual(ord('a'), result)

  def test_handle_tab_completion_no_matches(self):
    textbox = _textbox(text = 'GETINF')
    result = nyx.curses._handle_tab_completion(no_op_handler, lambda txt_input: [], textbox, 9)

    self.assertEqual(None, result)  # consumes input
    self.assertFalse(textbox.win.addstr.called)

  def test_handle_tab_completion_single_match(self):
    textbox = _textbox(text = 'GETINF')
    result = nyx.curses._handle_tab_completion(no_op_handler, lambda txt_input: ['GETINFO version'], textbox, 9)

    self.assertEqual(None, result)  # consumes input
    self.assertEquals(call(0, 15), textbox.win.move.call_args)  # move cursor to end
    self.assertEqual(call(0, 0, 'GETINFO version'), textbox.win.addstr.call_args)

  def test_handle_tab_completion_multiple_matches(self):
    textbox = _textbox(text = 'GETINF')
    result = nyx.curses._handle_tab_completion(no_op_handler, lambda txt_input: ['GETINFO version', 'GETINFO info/events'], textbox, 9)

    self.assertEqual(None, result)  # consumes input
    self.assertEquals(call(0, 8), textbox.win.move.call_args)  # move cursor to end
    self.assertEqual(call(0, 0, 'GETINFO '), textbox.win.addstr.call_args)

  def test_text_backlog_no_op(self):
    backlog = nyx.curses._TextBacklog(['GETINFO version'])
    textbox = _textbox()

    self.assertEqual(ord('a'), backlog._handler(no_op_handler, textbox, ord('a')))
    self.assertFalse(textbox.win.addstr.called)

  def test_text_backlog_fills_history(self):
    backlog = nyx.curses._TextBacklog(['GETINFO version'])
    textbox = _textbox()

    self.assertEqual(None, backlog._handler(no_op_handler, textbox, curses.KEY_UP))
    self.assertEqual(call(0, 0, 'GETINFO version'), textbox.win.addstr.call_args)

  def test_text_backlog_remembers_custom_input(self):
    backlog = nyx.curses._TextBacklog(['GETINFO version'])
    textbox = _textbox(text = 'hello')

    self.assertEqual(None, backlog._handler(no_op_handler, textbox, curses.KEY_UP))
    self.assertEqual(call(0, 0, 'GETINFO version'), textbox.win.addstr.call_args)

    self.assertEqual(None, backlog._handler(no_op_handler, textbox, curses.KEY_DOWN))
    self.assertEqual(call(0, 0, 'hello'), textbox.win.addstr.call_args)
