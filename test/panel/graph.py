"""
Unit tests for nyx.panel.graph.
"""

import datetime
import unittest

import stem.control

import nyx.panel.graph
import test

from test import require_curses
from mock import patch

EXPECTED_ACCOUNTING = """
Accounting (awake)                 Time to reset: 01:02
  37.7 Kb / 842.0 Kb                 16.0 Kb / 74.1 Kb
""".strip()


class TestGraph(unittest.TestCase):
  @require_curses
  @patch('nyx.panel.graph.tor_controller')
  def test_draw_accounting_stats(self, tor_controller_mock):
    tor_controller_mock().is_alive.return_value = True

    accounting_stat = stem.control.AccountingStats(
      1410723598.276578,
      'awake',
      datetime.datetime(2014, 9, 14, 19, 41),
      62,
      4837, 102944, 107781,
      2050, 7440, 9490,
    )

    rendered = test.render(nyx.panel.graph._draw_accounting_stats, 0, accounting_stat)
    self.assertEqual(EXPECTED_ACCOUNTING, rendered.content)

  @require_curses
  @patch('nyx.panel.graph.tor_controller')
  def test_draw_accounting_stats_disconnected(self, tor_controller_mock):
    tor_controller_mock().is_alive.return_value = False
    rendered = test.render(nyx.panel.graph._draw_accounting_stats, 0, None)
    self.assertEqual('Accounting: Connection Closed...', rendered.content)
