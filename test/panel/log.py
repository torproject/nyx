"""
Unit tests for nyx.panel.log.
"""

import unittest

import nyx.panel.log
import test

from nyx.log import LogEntry, LogFilters
from test import require_curses

EXPECTED_WRAPPED_MSG = """\
[NOTICE] ho hum, ho hum, ho hum, ho hum, ho hum, ho hum, ho hum, ho
  hum, ho hum, ho hum, ho hum, ho hum, ho hum, ho hum, ho hum, ho hum, ho hum,
  ho hum, ho hum, ho hum, ho hum...
""".rstrip()


class TestLogPanel(unittest.TestCase):
  @require_curses
  def test_draw_title(self):
    rendered = test.render(nyx.panel.log._draw_title, ['NOTICE', 'WARN', 'ERR'], LogFilters())
    self.assertEqual('Events (NOTICE-ERR):', rendered.content)

    rendered = test.render(nyx.panel.log._draw_title, ['NYX_NOTICE', 'NYX_WARNING', 'NYX_ERROR', 'NOTICE', 'WARN', 'ERR'], LogFilters())
    self.assertEqual('Events (TOR/NYX NOTICE-ERR):', rendered.content)

    rendered = test.render(nyx.panel.log._draw_title, ['NYX_DEBUG', 'NYX_INFO', 'NYX_NOTICE', 'NYX_WARNING', 'NYX_ERROR', 'NOTICE', 'WARN', 'ERR'], LogFilters())
    self.assertEqual('Events (NOTICE-ERR, NYX DEBUG-ERR):', rendered.content)

  @require_curses
  def test_draw_title_with_filter(self):
    log_filter = LogFilters()
    log_filter.select('stuff*')

    rendered = test.render(nyx.panel.log._draw_title, ['NOTICE', 'WARN', 'ERR'], log_filter)
    self.assertEqual('Events (NOTICE-ERR, filter: stuff*):', rendered.content)

  @require_curses
  def test_draw_entry(self):
    entry = LogEntry(1467656897.08663, 'NOTICE', 'feeding sulfur to baby dragons is just mean...')
    rendered = test.render(nyx.panel.log._draw_entry, 0, 0, entry, True)
    self.assertEqual('[NOTICE] feeding sulfur to baby dragons is just mean...', rendered.content.split(' ', 1)[1])

  @require_curses
  def test_draw_entry_that_wraps(self):
    entry = LogEntry(1467656897.08663, 'NOTICE', 'ho hum%s...' % (', ho hum' * 20))
    rendered = test.render(nyx.panel.log._draw_entry, 0, 0, entry, True)
    self.assertEqual(EXPECTED_WRAPPED_MSG, rendered.content.split(' ', 1)[1])

  @require_curses
  def test_draw_entry_with_duplicates(self):
    entry = LogEntry(1467656897.08663, 'NOTICE', 'feeding sulfur to baby dragons is just mean...')
    entry.duplicates = [1, 2]  # only care about the count, not the content
    rendered = test.render(nyx.panel.log._draw_entry, 0, 0, entry, True)
    self.assertEqual('[NOTICE] feeding sulfur to baby dragons is just mean...', rendered.content.split(' ', 1)[1])

    rendered = test.render(nyx.panel.log._draw_entry, 0, 0, entry, False)
    self.assertEqual('[NOTICE] feeding sulfur to baby dragons is just mean... [1 duplicate\n  hidden]', rendered.content.split(' ', 1)[1])

    entry.duplicates = [1, 2, 3, 4, 5, 6]
    rendered = test.render(nyx.panel.log._draw_entry, 0, 0, entry, False)
    self.assertEqual('[NOTICE] feeding sulfur to baby dragons is just mean... [5 duplicates\n  hidden]', rendered.content.split(' ', 1)[1])
