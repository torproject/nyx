"""
Unit tests for nyx.panel.connection.
"""

import unittest

import nyx.panel.connection
import test

from nyx.panel.connection import Category, Entry
from test import require_curses


class MockEntry(Entry):
  def __init__(self, lines = [], entry_type = Category.INBOUND, is_private = False):
    self._lines = lines
    self._type = entry_type
    self._is_private = is_private

  def lines(self):
    return self._lines

  def get_type(self):
    return self._type

  def is_private(self):
    return self._is_private


class TestConnectionPanel(unittest.TestCase):
  @require_curses
  def test_draw_title(self):
    self.assertEqual('Connection Details:', test.render(nyx.panel.connection._draw_title, [], True).content)
    self.assertEqual('Connections:', test.render(nyx.panel.connection._draw_title, [], False).content)

    entries = [
      MockEntry(entry_type = Category.INBOUND),
      MockEntry(entry_type = Category.INBOUND),
      MockEntry(entry_type = Category.OUTBOUND),
      MockEntry(entry_type = Category.INBOUND),
      MockEntry(entry_type = Category.CONTROL),
    ]

    self.assertEqual('Connections (3 inbound, 1 outbound, 1 control):', test.render(nyx.panel.connection._draw_title, entries, False).content)
