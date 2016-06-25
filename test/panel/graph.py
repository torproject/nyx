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
  def test_x_axis_labels(self):
    test_inputs = {
      0: {},
      7: {},
      10: {5: '25s'},
      15: {5: '25s', 10: '50'},
      20: {5: '25s', 10: '50', 15: '1m'},
      25: {5: '25s', 10: '50', 15: '1m', 20: '1.6'},
      45: {5: '25s', 10: '50', 15: '1m', 20: '1.6', 25: '2.0', 30: '2.5', 35: '2.9', 40: '3.3'},
      80: {10: '50s', 20: '1m', 30: '2.5', 40: '3.3', 50: '4.1', 60: '5.0', 70: '5.8'},  # spaced more since wide
    }

    for width, expected in test_inputs.items():
      self.assertEqual(expected, nyx.panel.graph._x_axis_labels(nyx.panel.graph.Interval.FIVE_SECONDS, width))

    test_inputs = {
      nyx.panel.graph.Interval.EACH_SECOND: {
        10: '10s', 20: '20', 30: '30', 40: '40', 50: '50', 60: '1m', 70: '1.1'
      }, nyx.panel.graph.Interval.FIVE_SECONDS: {
        10: '50s', 20: '1m', 30: '2.5', 40: '3.3', 50: '4.1', 60: '5.0', 70: '5.8'
      }, nyx.panel.graph.Interval.THIRTY_SECONDS: {
        10: '5m', 20: '10', 30: '15', 40: '20', 50: '25', 60: '30', 70: '35'
      }, nyx.panel.graph.Interval.MINUTELY: {
        10: '10m', 20: '20', 30: '30', 40: '40', 50: '50', 60: '1h', 70: '1.1'
      }, nyx.panel.graph.Interval.FIFTEEN_MINUTE: {
        10: '2h', 20: '5', 30: '7', 40: '10', 50: '12', 60: '15', 70: '17'
      }, nyx.panel.graph.Interval.THIRTY_MINUTE: {
        10: '5h', 20: '10', 30: '15', 40: '20', 50: '1d', 60: '1.2', 70: '1.4'
      }, nyx.panel.graph.Interval.HOURLY: {
        10: '10h', 20: '20', 30: '1d', 40: '1.6', 50: '2.0', 60: '2.5', 70: '2.9'
      }, nyx.panel.graph.Interval.DAILY: {
        10: '10d', 20: '20', 30: '30', 40: '40', 50: '50', 60: '60', 70: '70'
      },
    }

    for interval, expected in test_inputs.items():
      self.assertEqual(expected, nyx.panel.graph._x_axis_labels(interval, 80))

  def test_y_axis_labels(self):
    data = nyx.panel.graph.ConnectionStats()

    # check with both even and odd height since that determines an offset in the middle

    self.assertEqual({2: '10', 4: '7', 6: '5', 9: '2', 11: '0'}, nyx.panel.graph._y_axis_labels(12, data.primary, 0, 10))
    self.assertEqual({2: '10', 4: '6', 6: '3', 8: '0'}, nyx.panel.graph._y_axis_labels(9, data.primary, 0, 10))

    # check where the min and max are the same

    self.assertEqual({2: '0', 11: '0'}, nyx.panel.graph._y_axis_labels(12, data.primary, 0, 0))

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
