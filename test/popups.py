"""
Unit tests for nyx.popups.
"""

import curses
import unittest

import nyx.panel
import nyx.popups
import test

from mock import patch, Mock

EXPECTED_HELP_POPUP = """
Page 1 Commands:---------------------------------------------------------------+
| arrows: scroll up and down             a: save snapshot of the log           |
| e: change logged events                f: log regex filter (disabled)        |
| u: duplicate log entries (hidden)      c: clear event log                    |
| r: resize graph                        s: graphed stats (bandwidth)          |
| b: graph bounds (local max)            i: graph update interval (each second)|
|                                                                              |
| Press any key...                                                             |
+------------------------------------------------------------------------------+
""".strip()

EXPECTED_ABOUT_POPUP = """
About:-------------------------------------------------------------------------+
| Nyx, version 1.4.6-dev (released April 28, 2011)                             |
|   Written by Damian Johnson (atagar@torproject.org)                          |
|   Project page: http://www.atagar.com/arm/                                   |
|                                                                              |
| Released under the GPL v3 (http://www.gnu.org/licenses/gpl.html)             |
|                                                                              |
| Press any key...                                                             |
+------------------------------------------------------------------------------+
""".strip()

EXPECTED_EMPTY_COUNTS = """
Client Locales---------------------------------------+
| Usage stats aren't available yet, press any key... |
+----------------------------------------------------+
""".strip()

EXPECTED_COUNTS = """
Client Locales-----------------------------------------------------------------+
| de  41 (43%) ***************************                                     |
| ru  32 (33%) *********************                                           |
| ca  11 (11%) *******                                                         |
| us   6 (6 %) ****                                                            |
| fr   5 (5 %) ***                                                             |
|                                                                              |
| Press any key...                                                             |
+------------------------------------------------------------------------------+
""".strip()

EXPECTED_LIST_SELECTOR = """
Update Interval:---+
| >  each second   |
|    5 seconds     |
|    30 seconds    |
|    minutely      |
|    15 minute     |
|    30 minute     |
|    hourly        |
|    daily         |
+------------------+
""".strip()

EXPECTED_SORT_DIALOG_START = """
Config Option Ordering:--------------------------------------------------------+
| Current Order: Man Page Entry, Name, Is Set                                  |
| New Order:                                                                   |
|                                                                              |
| Name               Value              Value Type         Category            |
| Usage              Summary            Description        Man Page Entry      |
| Is Set             Cancel                                                    |
|                                                                              |
+------------------------------------------------------------------------------+
""".strip()

EXPECTED_SORT_DIALOG_END = """
Config Option Ordering:--------------------------------------------------------+
| Current Order: Man Page Entry, Name, Is Set                                  |
| New Order: Name, Summary                                                     |
|                                                                              |
| Value              Value Type         Category           Usage               |
| Description        Man Page Entry     Is Set             Cancel              |
|                                                                              |
|                                                                              |
+------------------------------------------------------------------------------+
""".strip()

EXPECTED_EVENT_SELECTOR = """
Event Types:-------------------------------------------------------------------+
|  d DEBUG      a ADDRMAP           r CLIENTS_SEEN      C SIGNAL               |
|  i INFO       f AUTHDIR_NEWDESCS  u DESCCHANGED       F STREAM_BW            |
|  n NOTICE     j BUILDTIMEOUT_SET  g GUARD             G STATUS_CLIENT        |
|  w WARN       b BW                h HS_DESC           H STATUS_GENERAL       |
|  e ERR        k CELL_STATS        v HS_DESC_CONTENT   I STATUS_SERVER        |
|               c CIRC              x NETWORK_LIVENESS  s STREAM               |
|               l CIRC_BW           y NEWCONSENSUS      J TB_EMPTY             |
|               m CIRC_MINOR        z NEWDESC           t TRANSPORT_LAUNCHED   |
|               p CONF_CHANGED      B NS                                       |
|               q CONN_BW           o ORCONN                                   |
|                                                                              |
|    DINWE tor runlevel+            A All Events                               |
|    12345 nyx runlevel+            X No Events                                |
|                                   U Unknown Events                           |
+------------------------------------------------------------------------------+
""".strip()

TORRC = """
ControlPort 9051
CookieAuthentication 1
ExitPolicy reject *:*
DataDirectory /home/atagar/.tor
Log notice file /home/atagar/.tor/log
ORPort 7000
""".strip()

EXPECTED_SAVE_TORRC_CONFIRMATION = """
Torrc to save:-----------------------------------------------------------------+
|ControlPort 9051                                                              |
|CookieAuthentication 1                                                        |
|ExitPolicy reject *:*                                                         |
|DataDirectory /home/atagar/.tor                                               |
|Log notice file /home/atagar/.tor/log                                         |
|ORPort 7000                                                    [Save] [Cancel]|
+------------------------------------------------------------------------------+
""".strip()

DESCRIPTOR_TEXT = """
Consensus:

r cyberphunk KXh3YBRc0aRzVSovxkxyqaEwgg4 VjdJThHuYj0jDY2tkkDJkCa8s1s 2016-04-04 19:03:16 94.23.150.191 8080 0
s Fast Guard Running Stable Valid
w Bandwidth=8410
p reject 1-65535

Server Descriptor:

router cyberphunk 94.23.150.191 8080 0 0
platform Tor 0.2.4.27 on Linux
protocols Link 1 2 Circuit 1
published 2016-04-04 19:03:16
fingerprint 2978 7760 145C D1A4 7355 2A2F C64C 72A9 A130 820E
uptime 3899791
bandwidth 10240000 10444800 6482376
extra-info-digest 9DC532664DDFD238A4119D623D30F136A3B851BF
reject *:*
router-signature
-----BEGIN SIGNATURE-----
EUFm38gONCoDuY7ZWHyJtBKuvk6Xi1MPuKuecS5frP3fX0wiZSrOVcpX0X8J+4Hr
Fb5i+yuMIAXeEn6UhtjqhhZBbY9PW9GdZOMTH8hJpG+evURyr+10PZq6UElg86rA
NCGI042p6+7UgCVT1x3WcLnq3ScV//s1wXHrUXa7vi0=
-----END SIGNATURE-----
""".strip().split('\n')

EXPECTED_DESCRIPTOR_WITHOUT_FINGERPRINT = """
Consensus Descriptor:----------+
|  No consensus data available |
+------------------------------+
""".strip()

EXPECTED_DESCRIPTOR = """
Consensus Descriptor (29787760145CD1A473552A2FC64C72A9A130820E):---------------------------------------------------+
|  1 Consensus:                                                                                                    |
|  2                                                                                                               |
|  3 r cyberphunk KXh3YBRc0aRzVSovxkxyqaEwgg4 VjdJThHuYj0jDY2tkkDJkCa8s1s 2016-04-04 19:03:16 94.23.150.191 8080 0 |
|  4 s Fast Guard Running Stable Valid                                                                             |
|  5 w Bandwidth=8410                                                                                              |
|  6 p reject 1-65535                                                                                              |
|  7                                                                                                               |
|  8 Server Descriptor:                                                                                            |
|  9                                                                                                               |
| 10 router cyberphunk 94.23.150.191 8080 0 0                                                                      |
| 11 platform Tor 0.2.4.27 on Linux                                                                                |
| 12 protocols Link 1 2 Circuit 1                                                                                  |
| 13 published 2016-04-04 19:03:16                                                                                 |
| 14 fingerprint 2978 7760 145C D1A4 7355 2A2F C64C 72A9 A130 820E                                                 |
| 15 uptime 3899791                                                                                                |
| 16 bandwidth 10240000 10444800 6482376                                                                           |
| 17 extra-info-digest 9DC532664DDFD238A4119D623D30F136A3B851BF                                                    |
| 18 reject *:*                                                                                                    |
| 19 router-signature                                                                                              |
| 20 -----BEGIN SIGNATURE-----                                                                                     |
| 21 EUFm38gONCoDuY7ZWHyJtBKuvk6Xi1MPuKuecS5frP3fX0wiZSrOVcpX0X8J+4Hr                                              |
| 22 Fb5i+yuMIAXeEn6UhtjqhhZBbY9PW9GdZOMTH8hJpG+evURyr+10PZq6UElg86rA                                              |
| 23 NCGI042p6+7UgCVT1x3WcLnq3ScV//s1wXHrUXa7vi0=                                                                  |
| 24 -----END SIGNATURE-----                                                                                       |
+------------------------------------------------------------------------------------------------------------------+
""".strip()


class TestPopups(unittest.TestCase):
  @patch('nyx.controller.get_controller')
  @patch('nyx.popups._top', Mock(return_value = 0))
  def test_help(self, get_controller_mock):
    header_panel = Mock()

    header_panel.key_handlers.return_value = (
      nyx.panel.KeyHandler('n'),
      nyx.panel.KeyHandler('r'),
    )

    graph_panel = Mock()

    graph_panel.key_handlers.return_value = (
      nyx.panel.KeyHandler('r', 'resize graph'),
      nyx.panel.KeyHandler('s', 'graphed stats', current = 'bandwidth'),
      nyx.panel.KeyHandler('b', 'graph bounds', current = 'local max'),
      nyx.panel.KeyHandler('i', 'graph update interval', current = 'each second'),
    )

    log_panel = Mock()

    log_panel.key_handlers.return_value = (
      nyx.panel.KeyHandler('arrows', 'scroll up and down'),
      nyx.panel.KeyHandler('a', 'save snapshot of the log'),
      nyx.panel.KeyHandler('e', 'change logged events'),
      nyx.panel.KeyHandler('f', 'log regex filter', current = 'disabled'),
      nyx.panel.KeyHandler('u', 'duplicate log entries', current = 'hidden'),
      nyx.panel.KeyHandler('c', 'clear event log'),
    )

    get_controller_mock().get_display_panels.return_value = [header_panel, graph_panel, log_panel]

    rendered = test.render(nyx.popups.show_help)
    self.assertEqual(EXPECTED_HELP_POPUP, rendered.content)

  @patch('nyx.popups._top', Mock(return_value = 0))
  def test_about(self):
    rendered = test.render(nyx.popups.show_about)
    self.assertEqual(EXPECTED_ABOUT_POPUP, rendered.content)

  @patch('nyx.popups._top', Mock(return_value = 0))
  def test_counts_when_empty(self):
    rendered = test.render(nyx.popups.show_counts, 'Client Locales', {})
    self.assertEqual(EXPECTED_EMPTY_COUNTS, rendered.content)

  @patch('nyx.popups._top', Mock(return_value = 0))
  def test_counts(self):
    clients = {
      'fr': 5,
      'us': 6,
      'ca': 11,
      'ru': 32,
      'de': 41,
    }

    rendered = test.render(nyx.popups.show_counts, 'Client Locales', clients, fill_char = '*')
    self.assertEqual(EXPECTED_COUNTS, rendered.content)

  @patch('nyx.popups._top', Mock(return_value = 0))
  def test_select_from_list(self):
    options = ['each second', '5 seconds', '30 seconds', 'minutely', '15 minute', '30 minute', 'hourly', 'daily']
    rendered = test.render(nyx.popups.select_from_list, 'Update Interval:', options, 'each second')
    self.assertEqual(EXPECTED_LIST_SELECTOR, rendered.content)
    self.assertEqual('each second', rendered.return_value)

  @patch('nyx.popups._top', Mock(return_value = 0))
  def test_select_sort_order(self):
    previous_order = ['Man Page Entry', 'Name', 'Is Set']
    options = ['Name', 'Value', 'Value Type', 'Category', 'Usage', 'Summary', 'Description', 'Man Page Entry', 'Is Set']

    rendered = test.render(nyx.popups.select_sort_order, 'Config Option Ordering:', options, previous_order, {})
    self.assertEqual(EXPECTED_SORT_DIALOG_START, rendered.content)
    self.assertEqual(None, rendered.return_value)

  @patch('nyx.popups._top', Mock(return_value = 0))
  def test_select_sort_order_usage(self):
    # Use the dialog to make a selection. At the end we render two options as
    # being selected (rather than three) because the act of selecing the third
    # closed the popup.

    keypresses = [
      nyx.curses.KeyInput(curses.KEY_ENTER),
      nyx.curses.KeyInput(curses.KEY_DOWN),
      nyx.curses.KeyInput(curses.KEY_ENTER),
      nyx.curses.KeyInput(curses.KEY_ENTER),
    ]

    def draw_func():
      with patch('nyx.curses.key_input', side_effect = keypresses):
        return nyx.popups.select_sort_order('Config Option Ordering:', options, previous_order, {})

    previous_order = ['Man Page Entry', 'Name', 'Is Set']
    options = ['Name', 'Value', 'Value Type', 'Category', 'Usage', 'Summary', 'Description', 'Man Page Entry', 'Is Set']

    rendered = test.render(draw_func)
    self.assertEqual(EXPECTED_SORT_DIALOG_END, rendered.content)
    self.assertEqual(['Name', 'Summary', 'Description'], rendered.return_value)

  @patch('nyx.popups._top', Mock(return_value = 0))
  @patch('nyx.controller.input_prompt', Mock(return_value = None))
  def test_select_event_types_when_canceled(self):
    rendered = test.render(nyx.popups.select_event_types)
    self.assertEqual(EXPECTED_EVENT_SELECTOR, rendered.content)
    self.assertEqual(None, rendered.return_value)

  @patch('nyx.popups._top', Mock(return_value = 0))
  @patch('nyx.controller.input_prompt', Mock(return_value = '2bwe'))
  def test_select_event_types_with_input(self):
    rendered = test.render(nyx.popups.select_event_types)
    self.assertEqual(EXPECTED_EVENT_SELECTOR, rendered.content)
    self.assertEqual(set(['NYX_INFO', 'ERR', 'WARN', 'BW', 'NYX_ERR', 'NYX_WARN', 'NYX_NOTICE']), rendered.return_value)

  @patch('nyx.curses.screen_size', Mock(return_value = nyx.curses.Dimensions(80, 60)))
  @patch('nyx.popups._top', Mock(return_value = 0))
  def test_confirm_save_torrc(self):
    rendered = test.render(nyx.popups.confirm_save_torrc, TORRC)
    self.assertEqual(EXPECTED_SAVE_TORRC_CONFIRMATION, rendered.content)
    self.assertEqual(False, rendered.return_value)

    def draw_func():
      with patch('nyx.curses.key_input', side_effect = [nyx.curses.KeyInput(curses.KEY_LEFT), nyx.curses.KeyInput(curses.KEY_ENTER)]):
        return nyx.popups.confirm_save_torrc(TORRC)

    rendered = test.render(draw_func)
    self.assertEqual(EXPECTED_SAVE_TORRC_CONFIRMATION, rendered.content)
    self.assertEqual(True, rendered.return_value)

  @patch('nyx.popups._top', Mock(return_value = 0))
  def test_descriptor_without_fingerprint(self):
    rendered = test.render(nyx.popups.show_descriptor, None, nyx.curses.Color.RED, lambda key: key.match('esc'))
    self.assertEqual(EXPECTED_DESCRIPTOR_WITHOUT_FINGERPRINT, rendered.content)
    self.assertEqual(nyx.curses.KeyInput(27), rendered.return_value)

  @patch('nyx.popups._top', Mock(return_value = 0))
  @patch('nyx.popups._descriptor_text', Mock(return_value = DESCRIPTOR_TEXT))
  def test_descriptor(self):
    rendered = test.render(nyx.popups.show_descriptor, '29787760145CD1A473552A2FC64C72A9A130820E', nyx.curses.Color.RED, lambda key: key.match('esc'))
    self.assertEqual(EXPECTED_DESCRIPTOR, rendered.content)
    self.assertEqual(nyx.curses.KeyInput(27), rendered.return_value)
