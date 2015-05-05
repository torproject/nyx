import unittest

from nyx.util.log import LogEntry


class TestLogEntry(unittest.TestCase):
  def test_deduplication_matches_identical_messages(self):
    # Simple case is that we match the same message but different timestamp.

    entry = LogEntry(1333738434, 'INFO', 'tor_lockfile_lock(): Locking "/home/atagar/.tor/lock"')
    self.assertTrue(entry.is_duplicate_of(LogEntry(1333738457, 'INFO', 'tor_lockfile_lock(): Locking "/home/atagar/.tor/lock"')))

    # ... but we shouldn't match if the runlevel differs.

    self.assertFalse(entry.is_duplicate_of(LogEntry(1333738457, 'DEBUG', 'tor_lockfile_lock(): Locking "/home/atagar/.tor/lock"')))

  def test_deduplication_matches_based_on_prefix(self):
    # matches using a prefix specified in dedup.cfg

    entry = LogEntry(1333738434, 'NYX_DEBUG', 'GETCONF MyFamily (runtime: 0.0007)')
    self.assertTrue(entry.is_duplicate_of(LogEntry(1333738457, 'NYX_DEBUG', 'GETCONF MyFamily (runtime: 0.0015)')))

  def test_deduplication_matches_with_wildcard(self):
    # matches using a wildcard specified in dedup.cfg

    entry = LogEntry(1333738434, 'NOTICE', 'Bootstrapped 72%: Loading relay descriptors.')
    self.assertTrue(entry.is_duplicate_of(LogEntry(1333738457, 'NOTICE', 'Bootstrapped 55%: Loading relay descriptors.')))
