"""
Unit tests for nyx.curses. Not entirely sure why this file can't be called
'curses.py' but doing so causes the unittest module to fail internally.
"""

import unittest

import test

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
