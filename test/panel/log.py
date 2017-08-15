"""
Unit tests for nyx.panel.log.
"""

import time
import unittest

import nyx.panel.log
import test

from nyx.log import LogEntry, LogFilters
from test import require_curses
from mock import patch, Mock

EXPECTED_WRAPPED_MSG = """\
16:41:37 [NOTICE] ho hum, ho hum, ho hum, ho hum, ho hum, ho hum, ho hum, ho
  hum, ho hum, ho hum, ho hum, ho hum, ho hum, ho hum, ho hum, ho hum, ho hum,
  ho hum, ho hum, ho hum, ho hum...
""".rstrip()

EXPECTED_ENTRIES = """\
 16:41:37 [NYX_WARNING] Tor's geoip database is unavailable.
 16:41:37 [NYX_NOTICE] No nyxrc loaded, using defaults. You can customize nyx by
   placing a configuration file at /home/atagar/.nyx/nyxrc (see the nyxrc.sample
   for its options).
 16:41:37 [NOTICE] New control connection opened from 127.0.0.1.
 16:41:37 [NOTICE] Opening OR listener on 0.0.0.0:7000
 16:41:37 [NOTICE] Opening Control listener on 127.0.0.1:9051
 16:41:37 [NOTICE] Opening Socks listener on 127.0.0.1:9050
 16:41:37 [NOTICE] Tor v0.2.9.0-alpha-dev (git-44ea3dc3311564a9) running on
   Linux with Libevent 2.0.16-stable, OpenSSL 1.0.1 and Zlib 1.2.3.4.
 16:41:37 [NOTICE] Tor 0.2.9.0-alpha-dev (git-44ea3dc3311564a9) opening log
   file.
""".rstrip()

EXPECTED_ENTRIES_WITH_BORDER = """\
+-October 26, 2011-------------------------------------------------------------+
|16:41:37 [NYX_WARNING] Tor's geoip database is unavailable.                   |
|16:41:37 [NYX_NOTICE] No nyxrc loaded, using defaults. You can customize nyx  |
|  by placing a configuration file at /home/atagar/.nyx/nyxrc (see the         |
|  nyxrc.sample for its options).                                              |
|16:41:37 [NOTICE] New control connection opened from 127.0.0.1.               |
|16:41:37 [NOTICE] Opening OR listener on 0.0.0.0:7000                         |
|16:41:37 [NOTICE] Opening Control listener on 127.0.0.1:9051                  |
|16:41:37 [NOTICE] Opening Socks listener on 127.0.0.1:9050                    |
|16:41:37 [NOTICE] Tor v0.2.9.0-alpha-dev (git-44ea3dc3311564a9) running on    |
|  Linux with Libevent 2.0.16-stable, OpenSSL 1.0.1 and Zlib 1.2.3.4.          |
|16:41:37 [NOTICE] Tor 0.2.9.0-alpha-dev (git-44ea3dc3311564a9) opening log    |
|  file.                                                                       |
+------------------------------------------------------------------------------+
""".rstrip()

NOW = 467656897.08663
TIME_STRUCT = time.gmtime(NOW)


def entries():
  return [
    LogEntry(NOW, 'NYX_WARNING', "Tor's geoip database is unavailable."),
    LogEntry(NOW, 'NYX_NOTICE', 'No nyxrc loaded, using defaults. You can customize nyx by placing a configuration file at /home/atagar/.nyx/nyxrc (see the nyxrc.sample for its options).'),
    LogEntry(NOW, 'NOTICE', 'New control connection opened from 127.0.0.1.'),
    LogEntry(NOW, 'NOTICE', 'Opening OR listener on 0.0.0.0:7000'),
    LogEntry(NOW, 'NOTICE', 'Opening Control listener on 127.0.0.1:9051'),
    LogEntry(NOW, 'NOTICE', 'Opening Socks listener on 127.0.0.1:9050'),
    LogEntry(NOW, 'NOTICE', 'Tor v0.2.9.0-alpha-dev (git-44ea3dc3311564a9) running on Linux with Libevent 2.0.16-stable, OpenSSL 1.0.1 and Zlib 1.2.3.4.'),
    LogEntry(NOW, 'NOTICE', 'Tor 0.2.9.0-alpha-dev (git-44ea3dc3311564a9) opening log file.'),
  ]


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
  @patch('time.localtime', Mock(return_value = TIME_STRUCT))
  def test_draw_entry(self):
    entry = LogEntry(NOW, 'NOTICE', 'feeding sulfur to baby dragons is just mean...')
    rendered = test.render(nyx.panel.log._draw_entry, 0, 0, 80, entry, True)
    self.assertEqual('16:41:37 [NOTICE] feeding sulfur to baby dragons is just mean...', rendered.content)

  @require_curses
  @patch('time.localtime', Mock(return_value = TIME_STRUCT))
  def test_draw_entry_that_wraps(self):
    entry = LogEntry(NOW, 'NOTICE', 'ho hum%s...' % (', ho hum' * 20))
    rendered = test.render(nyx.panel.log._draw_entry, 0, 0, 80, entry, True)
    self.assertEqual(EXPECTED_WRAPPED_MSG, rendered.content)

  @require_curses
  @patch('time.localtime', Mock(return_value = TIME_STRUCT))
  def test_draw_entry_with_duplicates(self):
    entry = LogEntry(NOW, 'NOTICE', 'feeding sulfur to baby dragons is just mean...')
    entry.duplicates = [1, 2]  # only care about the count, not the content
    rendered = test.render(nyx.panel.log._draw_entry, 0, 0, 80, entry, True)
    self.assertEqual('16:41:37 [NOTICE] feeding sulfur to baby dragons is just mean...', rendered.content)

    rendered = test.render(nyx.panel.log._draw_entry, 0, 0, 80, entry, False)
    self.assertEqual('16:41:37 [NOTICE] feeding sulfur to baby dragons is just mean... [1 duplicate\n  hidden]', rendered.content)

    entry.duplicates = [1, 2, 3, 4, 5, 6]
    rendered = test.render(nyx.panel.log._draw_entry, 0, 0, 80, entry, False)
    self.assertEqual('16:41:37 [NOTICE] feeding sulfur to baby dragons is just mean... [5 duplicates\n  hidden]', rendered.content)

  @require_curses
  @patch('time.localtime', Mock(return_value = TIME_STRUCT))
  @patch('nyx.log.day_count', Mock(return_value = 5))
  def test_draw_entries(self):
    rendered = test.render(nyx.panel.log._draw_entries, 0, 0, entries(), True)
    self.assertEqual(EXPECTED_ENTRIES, rendered.content)

  @require_curses
  @patch('time.localtime', Mock(return_value = TIME_STRUCT))
  @patch('time.strftime', Mock(return_value = 'October 26, 2011'))
  def test_draw_entries_day_dividers(self):
    rendered = test.render(nyx.panel.log._draw_entries, 0, 0, entries(), True)
    self.assertEqual(EXPECTED_ENTRIES_WITH_BORDER, rendered.content)
