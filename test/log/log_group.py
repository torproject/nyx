import os
import unittest

from nyx.log import LogGroup, LogEntry, read_tor_log


class TestLogGroup(unittest.TestCase):
  def test_maintains_certain_size(self):
    group = LogGroup(5)
    self.assertEqual(0, len(group))

    group.add(LogEntry(1333738410, 'INFO', 'tor_lockfile_lock(): Locking "/home/atagar/.tor/lock"'))
    self.assertEqual([LogEntry(1333738410, 'INFO', 'tor_lockfile_lock(): Locking "/home/atagar/.tor/lock"')], list(group))
    self.assertEqual(1, len(group))

    group.add(LogEntry(1333738420, 'NYX_DEBUG', 'GETCONF MyFamily (runtime: 0.0007)'))
    group.add(LogEntry(1333738430, 'NOTICE', 'Bootstrapped 72%: Loading relay descriptors.'))
    group.add(LogEntry(1333738440, 'NOTICE', 'Bootstrapped 75%: Loading relay descriptors.'))
    group.add(LogEntry(1333738450, 'NOTICE', 'Bootstrapped 78%: Loading relay descriptors.'))
    self.assertEqual(5, len(group))

    # group should now be full, adding more entries pops others off

    group.add(LogEntry(1333738460, 'NOTICE', 'Bootstrapped 80%: Loading relay descriptors.'))
    self.assertFalse(LogEntry(1333738410, 'INFO', 'tor_lockfile_lock(): Locking "/home/atagar/.tor/lock"') in list(group))
    self.assertEqual(5, len(group))

    # try adding a bunch that will be deduplicated, and make sure we still maintain the size

    group.add(LogEntry(1333738510, 'NOTICE', 'Bootstrapped 80%: Loading relay descriptors.'))
    group.add(LogEntry(1333738520, 'NOTICE', 'Bootstrapped 80%: Loading relay descriptors.'))
    group.add(LogEntry(1333738530, 'NOTICE', 'Bootstrapped 80%: Loading relay descriptors.'))
    group.add(LogEntry(1333738540, 'NOTICE', 'Bootstrapped 80%: Loading relay descriptors.'))
    group.add(LogEntry(1333738550, 'NOTICE', 'Bootstrapped 80%: Loading relay descriptors.'))
    group.add(LogEntry(1333738560, 'NOTICE', 'Bootstrapped 80%: Loading relay descriptors.'))
    group.add(LogEntry(1333738570, 'NOTICE', 'Bootstrapped 80%: Loading relay descriptors.'))
    self.assertEqual([1333738570, 1333738560, 1333738550, 1333738540, 1333738530], [e.timestamp for e in group])
    self.assertEqual(5, len(group))

  def test_deduplication(self):
    group = LogGroup(5)
    group.add(LogEntry(1333738410, 'NOTICE', 'Bootstrapped 72%: Loading relay descriptors.'))
    group.add(LogEntry(1333738420, 'NOTICE', 'Bootstrapped 75%: Loading relay descriptors.'))
    group.add(LogEntry(1333738430, 'NYX_DEBUG', 'GETCONF MyFamily (runtime: 0.0007)'))
    group.add(LogEntry(1333738440, 'NOTICE', 'Bootstrapped 78%: Loading relay descriptors.'))
    group.add(LogEntry(1333738450, 'NOTICE', 'Bootstrapped 80%: Loading relay descriptors.'))
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

    group.add(LogEntry(1333738460, 'NOTICE', 'Bootstrapped 90%: Loading relay descriptors.'))

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

    group.add(LogEntry(1333738470, 'INFO', 'tor_lockfile_lock(): Locking "/home/atagar/.tor/lock"'))

    bootstrap_messages = [
      'Bootstrapped 90%: Loading relay descriptors.',
      'Bootstrapped 80%: Loading relay descriptors.',
      'Bootstrapped 78%: Loading relay descriptors.',
    ]

    group_items = list(group)
    self.assertEqual(None, group_items[0].duplicates)
    self.assertEqual(bootstrap_messages, [e.message for e in group_items[1].duplicates])
    self.assertEqual([False, False, True, True, False], [e.is_duplicate for e in group_items])

  def test_deduplication_with_daybreaks(self):
    group = LogGroup(100, group_by_day = True)
    test_log_path = os.path.join(os.path.dirname(__file__), 'data', 'daybreak_deduplication')

    for entry in reversed(list(read_tor_log(test_log_path))):
      group.add(entry)

    # Entries should consist of two days of results...
    #
    # Day 1:
    # 10:24:27 [NOTICE] New control connection opened from 127.0.0.1.
    # 10:21:31 [NOTICE] New control connection opened from 127.0.0.1.
    # 10:19:24 [NOTICE] New control connection opened from 127.0.0.1.
    # 10:16:38 [NOTICE] New control connection opened from 127.0.0.1.
    # 10:16:38 [NOTICE] New control connection opened from 127.0.0.1.
    # 05:44:40 [NOTICE] Heartbeat: Tor's uptime is 18:00 hours, with 0 circuits open. I've sent 862 kB and received 9.05 MB.
    #
    # Day 2:
    # 23:44:40 [NOTICE] Heartbeat: Tor's uptime is 12:00 hours, with 1 circuits open. I've sent 794 kB and received 7.32 MB.
    # 19:02:44 [NOTICE] New control connection opened from 127.0.0.1.
    # 18:52:47 [NOTICE] New control connection opened from 127.0.0.1.
    # 18:11:56 [NOTICE] New control connection opened from 127.0.0.1.
    # 17:44:40 [NOTICE] Heartbeat: Tor's uptime is 6:00 hours, with 0 circuits open. I've sent 539 kB and received 4.25 MB.
    # 11:45:03 [NOTICE] New control connection opened from 127.0.0.1.
    # 11:44:49 [NOTICE] Bootstrapped 100%: Done
    # ... etc...

    group_items = list(group)

    # First day

    self.assertEqual('New control connection opened from 127.0.0.1.', group_items[0].message)
    self.assertEqual(5, len(group_items[0].duplicates))
    self.assertFalse(group_items[0].is_duplicate)

    for entry in group_items[1:5]:
      self.assertEqual('New control connection opened from 127.0.0.1.', entry.message)
      self.assertEqual(5, len(entry.duplicates))
      self.assertTrue(entry.is_duplicate)

    self.assertEqual("Heartbeat: Tor's uptime is 18:00 hours, with 0 circuits open. I've sent 862 kB and received 9.05 MB.", group_items[5].message)
    self.assertEqual(None, group_items[5].duplicates)
    self.assertFalse(group_items[5].is_duplicate)

    # Second day

    self.assertEqual("Heartbeat: Tor's uptime is 12:00 hours, with 1 circuits open. I've sent 794 kB and received 7.32 MB.", group_items[6].message)
    self.assertEqual(2, len(group_items[6].duplicates))
    self.assertFalse(group_items[6].is_duplicate)

    self.assertEqual('New control connection opened from 127.0.0.1.', group_items[8].message)
    self.assertEqual(4, len(group_items[8].duplicates))
    self.assertTrue(group_items[8].is_duplicate)

    self.assertEqual("Heartbeat: Tor's uptime is 6:00 hours, with 0 circuits open. I've sent 539 kB and received 4.25 MB.", group_items[10].message)
    self.assertEqual(2, len(group_items[10].duplicates))
    self.assertTrue(group_items[10].is_duplicate)
