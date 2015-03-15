import datetime
import io
import time
import unittest

from mock import Mock, patch

from seth.util import bandwidth_from_state

STATE_FILE = """\
# Tor state file last generated on 2014-07-20 13:05:10 local time
# Other times below are in UTC
# You *do not* need to edit this file.

EntryGuard mullbinde7 2546FD2B50165C1567A297B02AD73F62DEA127A0 DirCache
EntryGuardAddedBy 2546FD2B50165C1567A297B02AD73F62DEA127A0 0.2.4.10-alpha-dev 2014-07-11 01:18:47
EntryGuardPathBias 9.000000 9.000000 9.000000 0.000000 0.000000 1.000000
TorVersion Tor 0.2.4.10-alpha-dev (git-8be6058d8f31e578)
LastWritten 2014-07-20 20:05:10
TotalBuildTimes 68
CircuitBuildTimeBin 525 1
CircuitBuildTimeBin 575 1
CircuitBuildTimeBin 675 1
"""

STATE_FILE_WITH_ENTRIES = STATE_FILE + """\
BWHistoryReadValues 921600,1843200,2764800,3686400,4608000
BWHistoryWriteValues 46080000,46080000,92160000,92160000,92160000
BWHistoryReadEnds %s
BWHistoryWriteEnds %s
"""


class TestBandwidthFromState(unittest.TestCase):
  @patch('seth.util.tor_controller')
  def test_when_not_localhost(self, tor_controller_mock):
    tor_controller_mock().is_localhost.return_value = False

    try:
      bandwidth_from_state()
      self.fail('expected a ValueError')
    except ValueError as exc:
      self.assertEqual('we can only prepopulate bandwidth information for a local tor instance', str(exc))

  @patch('seth.util.tor_controller')
  def test_unknown_pid(self, tor_controller_mock):
    tor_controller_mock().is_localhost.return_value = True
    tor_controller_mock().get_pid.return_value = None

    try:
      bandwidth_from_state()
      self.fail('expected a ValueError')
    except ValueError as exc:
      self.assertEqual("unable to determine tor's uptime", str(exc))

  @patch('seth.util.tor_controller')
  @patch('stem.util.system.start_time')
  def test_insufficient_uptime(self, start_time_mock, tor_controller_mock):
    tor_controller_mock().is_localhost.return_value = True
    start_time_mock.return_value = time.time() - 60  # one minute of uptime

    try:
      bandwidth_from_state()
      self.fail('expected a ValueError')
    except ValueError as exc:
      self.assertEqual("insufficient uptime, tor must've been running for at least a day", str(exc))

  @patch('seth.util.tor_controller')
  @patch('stem.util.system.start_time', Mock(return_value = 50))
  def test_no_data_dir(self, tor_controller_mock):
    tor_controller_mock().is_localhost.return_value = True
    tor_controller_mock().get_conf.return_value = None

    try:
      bandwidth_from_state()
      self.fail('expected a ValueError')
    except ValueError as exc:
      self.assertEqual("unable to determine tor's data directory", str(exc))

  @patch('seth.util.tor_controller')
  @patch('seth.util.open', create = True)
  @patch('stem.util.system.start_time', Mock(return_value = 50))
  def test_no_bandwidth_entries(self, open_mock, tor_controller_mock):
    tor_controller_mock().is_localhost.return_value = True
    tor_controller_mock().get_conf.return_value = '/home/atagar/.tor'
    open_mock.return_value = io.BytesIO(STATE_FILE)

    try:
      bandwidth_from_state()
      self.fail('expected a ValueError')
    except ValueError as exc:
      self.assertEqual('bandwidth stats missing from state file', str(exc))

    open_mock.assert_called_once_with('/home/atagar/.tor/state')

  @patch('seth.util.tor_controller')
  @patch('seth.util.open', create = True)
  @patch('stem.util.system.start_time', Mock(return_value = 50))
  def test_when_successful(self, open_mock, tor_controller_mock):
    tor_controller_mock().is_localhost.return_value = True
    tor_controller_mock().get_conf.return_value = '/home/atagar/.tor'

    now = int(time.time())
    timestamp = datetime.datetime.utcfromtimestamp(now + 900).strftime('%Y-%m-%d %H:%M:%S')
    open_mock.return_value = io.BytesIO(STATE_FILE_WITH_ENTRIES % (timestamp, timestamp))

    stats = bandwidth_from_state()
    self.assertEqual([1024, 2048, 3072, 4096], stats.read_entries)
    self.assertEqual([51200, 51200, 102400, 102400], stats.write_entries)
    self.assertEqual(now, stats.last_read_time)
    self.assertEqual(now, stats.last_write_time)
