"""
Unit tests for nyx.panel.graph.
"""

import datetime
import unittest

import stem.control

import nyx.curses
import nyx.panel.graph
import test

from test import require_curses
from mock import patch

EXPECTED_BLANK_GRAPH = """
Download:
0 b



0 b
        5s   10   15
""".rstrip()

EXPECTED_ACCOUNTING = """
Accounting (awake)                 Time to reset: 01:02
  37.7 Kb / 842.0 Kb                 16.0 Kb / 74.1 Kb
""".strip()


class TestGraph(unittest.TestCase):
  @require_curses
  @patch('nyx.panel.graph.tor_controller')
  def test_draw_subgraph_blank(self, tor_controller_mock):
    tor_controller_mock().get_info.return_value = None

    attr = nyx.panel.graph.DrawAttributes(
      stat = None,
      subgraph_height = 7,
      subgraph_width = 30,
      interval = nyx.panel.graph.Interval.EACH_SECOND,
      bounds_type = nyx.panel.graph.Bounds.LOCAL_MAX,
      right_to_left = False,
    )

    data = nyx.panel.graph.BandwidthStats()

    rendered = test.render(nyx.panel.graph._draw_subgraph, attr, data.primary, 0, nyx.curses.Color.CYAN)
    self.assertEqual(EXPECTED_BLANK_GRAPH, rendered.content)

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
