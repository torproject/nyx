import os
import unittest

from nyx.util.log import LogEntry, read_tor_log


def data_path(filename):
  return os.path.join(os.path.dirname(__file__), 'data', filename)


class TestReadTorLog(unittest.TestCase):
  def test_general_log(self):
    entries = list(read_tor_log(data_path('tor_log')))
    self.assertEqual(21, len(entries))

    self.assertEqual(LogEntry(1333738426, 'NOTICE', 'Interrupt: exiting cleanly.'), entries[0])
    self.assertEqual(LogEntry(1333735419, 'NOTICE', 'Tor 0.2.7.0-alpha-dev (git-4247ce99e5d9b7b2) opening new log file.'), entries[-1])

  def test_with_multiple_tor_instances(self):
    entries = list(read_tor_log(data_path('multiple_tor_instances')))
    self.assertEqual(12, len(entries))

    self.assertEqual(LogEntry(1333738434, 'DEBUG', 'parse_dir_authority_line(): Trusted 100 dirserver at 128.31.0.39:9131 (9695)'), entries[0])
    self.assertEqual(LogEntry(1333738434, 'INFO', 'tor_lockfile_lock(): Locking "/home/atagar/.tor/lock"'), entries[1])
    self.assertEqual(LogEntry(1333738434, 'NOTICE', 'Tor 0.2.7.0-alpha-dev (git-4247ce99e5d9b7b2) opening log file.'), entries[-1])

  def test_with_read_limit(self):
    entries = list(read_tor_log(data_path('tor_log'), 5))
    self.assertEqual(5, len(entries))
    self.assertEqual('Interrupt: exiting cleanly.', entries[0].message)
    self.assertEqual('Bootstrapped 90%: Establishing a Tor circuit', entries[-1].message)

  def test_with_empty_file(self):
    entries = list(read_tor_log(data_path('empty_file')))
    self.assertEqual(0, len(entries))

  def test_with_missing_path(self):
    self.assertRaises(IOError, list, read_tor_log(data_path('no_such_path')))

  def test_with_malformed_line(self):
    try:
      list(read_tor_log(data_path('malformed_line')))
      self.fail("Malformed content should've raised a ValueError")
    except ValueError as exc:
      self.assertTrue("has a line that doesn't match the format we expect: Apr 06 11:03:53.000" in str(exc))

  def test_with_malformed_runlevel(self):
    try:
      list(read_tor_log(data_path('malformed_runlevel')))
      self.fail("Malformed content should've raised a ValueError")
    except ValueError as exc:
      self.assertTrue('has an unrecognized runlevel: [unrecognized]' in str(exc))

  def test_with_malformed_date(self):
    try:
      list(read_tor_log(data_path('malformed_date')))
      self.fail("Malformed content should've raised a ValueError")
    except ValueError as exc:
      self.assertTrue("has a timestamp we don't recognize: Zed 06 11:03:52.000" in str(exc))
