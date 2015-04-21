import unittest

from nyx.util.log import LogGroup, LogEntry


class TestLogGroup(unittest.TestCase):
  def test_maintains_certain_size(self):
    group = LogGroup(5)
    self.assertEqual(0, len(group))

    group.add(1333738410, 'INFO', 'tor_lockfile_lock(): Locking "/home/atagar/.tor/lock"')
    self.assertEqual([LogEntry(1333738410, 'INFO', 'tor_lockfile_lock(): Locking "/home/atagar/.tor/lock"')], list(group))
    self.assertEqual(1, len(group))

    group.add(1333738420, 'NYX_DEBUG', 'GETCONF MyFamily (runtime: 0.0007)')
    group.add(1333738430, 'NOTICE', 'Bootstrapped 72%: Loading relay descriptors.')
    group.add(1333738440, 'NOTICE', 'Bootstrapped 75%: Loading relay descriptors.')
    group.add(1333738450, 'NOTICE', 'Bootstrapped 78%: Loading relay descriptors.')
    self.assertEqual(5, len(group))

    # group should now be full, adding more entries pops others off

    group.add(1333738460, 'NOTICE', 'Bootstrapped 80%: Loading relay descriptors.')
    self.assertFalse(LogEntry(1333738410, 'INFO', 'tor_lockfile_lock(): Locking "/home/atagar/.tor/lock"') in list(group))
    self.assertEqual(5, len(group))

    # try adding a bunch that will be deduplicated, and make sure we still maintain the size

    group.add(1333738510, 'NOTICE', 'Bootstrapped 80%: Loading relay descriptors.')
    group.add(1333738520, 'NOTICE', 'Bootstrapped 80%: Loading relay descriptors.')
    group.add(1333738530, 'NOTICE', 'Bootstrapped 80%: Loading relay descriptors.')
    group.add(1333738540, 'NOTICE', 'Bootstrapped 80%: Loading relay descriptors.')
    group.add(1333738550, 'NOTICE', 'Bootstrapped 80%: Loading relay descriptors.')
    group.add(1333738560, 'NOTICE', 'Bootstrapped 80%: Loading relay descriptors.')
    group.add(1333738570, 'NOTICE', 'Bootstrapped 80%: Loading relay descriptors.')
    self.assertEqual([1333738570, 1333738560, 1333738550, 1333738540, 1333738530], [e.timestamp for e in group])
    self.assertEqual(5, len(group))

  def test_deduplication(self):
    group = LogGroup(5)
    group.add(1333738410, 'NOTICE', 'Bootstrapped 72%: Loading relay descriptors.')
    group.add(1333738420, 'NOTICE', 'Bootstrapped 75%: Loading relay descriptors.')
    group.add(1333738430, 'NYX_DEBUG', 'GETCONF MyFamily (runtime: 0.0007)')
    group.add(1333738440, 'NOTICE', 'Bootstrapped 78%: Loading relay descriptors.')
    group.add(1333738450, 'NOTICE', 'Bootstrapped 80%: Loading relay descriptors.')
    self.assertEqual([1333738450, 1333738440, 1333738430, 1333738420, 1333738410], [e.timestamp for e in group])

    bootstrap_messages = [
      'Bootstrapped 80%: Loading relay descriptors.',
      'Bootstrapped 78%: Loading relay descriptors.',
      'Bootstrapped 75%: Loading relay descriptors.',
      'Bootstrapped 72%: Loading relay descriptors.',
    ]

    group_items = list(group)
    self.assertEqual(bootstrap_messages, [e.message for e in group_items[0].duplicates])
    self.assertEqual([False, True, False, True, True], [e.is_duplicate for e in group_items])

    # add another duplicate message that pops the last

    group.add(1333738460, 'NOTICE', 'Bootstrapped 90%: Loading relay descriptors.')

    bootstrap_messages = [
      'Bootstrapped 90%: Loading relay descriptors.',
      'Bootstrapped 80%: Loading relay descriptors.',
      'Bootstrapped 78%: Loading relay descriptors.',
      'Bootstrapped 75%: Loading relay descriptors.',
    ]

    group_items = list(group)
    self.assertEqual(bootstrap_messages, [e.message for e in group_items[0].duplicates])
    self.assertEqual([False, True, True, False, True], [e.is_duplicate for e in group_items])

    # add another non-duplicate message that pops the last

    group.add(1333738470, 'INFO', 'tor_lockfile_lock(): Locking "/home/atagar/.tor/lock"')

    bootstrap_messages = [
      'Bootstrapped 90%: Loading relay descriptors.',
      'Bootstrapped 80%: Loading relay descriptors.',
      'Bootstrapped 78%: Loading relay descriptors.',
    ]

    group_items = list(group)
    self.assertEqual(None, group_items[0].duplicates)
    self.assertEqual(bootstrap_messages, [e.message for e in group_items[1].duplicates])
    self.assertEqual([False, False, True, True, False], [e.is_duplicate for e in group_items])
