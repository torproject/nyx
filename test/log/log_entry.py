import unittest

import nyx.log

from nyx.log import LogEntry


class TestLogEntry(unittest.TestCase):
  def setUp(self):
    nyx.log.GROUP_BY_DAY = False

  def tearDown(self):
    nyx.log.GROUP_BY_DAY = True

  def test_dedup_key_by_messages(self):
    entry = LogEntry(1333738434, 'INFO', 'tor_lockfile_lock(): Locking "/home/atagar/.tor/lock"')
    self.assertEqual('INFO:tor_lockfile_lock(): Locking "/home/atagar/.tor/lock"', entry.dedup_key)

  def test_dedup_key_by_prefix(self):
    # matches using a prefix specified in dedup.cfg

    entry = LogEntry(1333738434, 'NYX_DEBUG', 'GETCONF MyFamily (runtime: 0.0007)')
    self.assertEqual('NYX_DEBUG:GETCONF MyFamily (', entry.dedup_key)

  def test_dedup_key_with_wildcard(self):
    # matches using a wildcard specified in dedup.cfg

    entry = LogEntry(1333738434, 'NOTICE', 'Bootstrapped 72%: Loading relay descriptors.')
    self.assertEqual('NOTICE:*Loading relay descriptors.', entry.dedup_key)
