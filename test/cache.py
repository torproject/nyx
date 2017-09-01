"""
Unit tests for nyx.cache.
"""

import re
import tempfile
import unittest

import nyx

from mock import Mock, patch


class TestCache(unittest.TestCase):
  def setUp(self):
    nyx.CACHE = None  # drop cached database reference

  @patch('nyx.data_directory', Mock(return_value = None))
  def test_memory_cache(self):
    """
    Create a cache in memory.
    """

    cache = nyx.cache()
    self.assertEqual((0, 'main', ''), cache._query('PRAGMA database_list').fetchone())

    with cache.write() as writer:
      writer.record_relay('3EA8E960F6B94CE30062AA8EF02894C00F8D1E66', '208.113.165.162', 1443, 'caersidi')

    self.assertEqual('caersidi', cache.relay_nickname('3EA8E960F6B94CE30062AA8EF02894C00F8D1E66'))

  def test_file_cache(self):
    """
    Create a new cache file, and ensure we can reload cached results.
    """

    with tempfile.NamedTemporaryFile(suffix = '.sqlite') as tmp:
      with patch('nyx.data_directory', Mock(return_value = tmp.name)):
        cache = nyx.cache()
        self.assertEqual((0, 'main', tmp.name), cache._query('PRAGMA database_list').fetchone())

        with cache.write() as writer:
          writer.record_relay('3EA8E960F6B94CE30062AA8EF02894C00F8D1E66', '208.113.165.162', 1443, 'caersidi')

        nyx.CACHE = None
        cache = nyx.cache()
        self.assertEqual('caersidi', cache.relay_nickname('3EA8E960F6B94CE30062AA8EF02894C00F8D1E66'))

  @patch('nyx.data_directory', Mock(return_value = None))
  def test_relay_nickname(self):
    """
    Basic checks for registering and fetching nicknames.
    """

    cache = nyx.cache()

    with cache.write() as writer:
      writer.record_relay('3EA8E960F6B94CE30062AA8EF02894C00F8D1E66', '208.113.165.162', 1443, 'caersidi')
      writer.record_relay('9695DFC35FFEB861329B9F1AB04C46397020CE31', '128.31.0.34', 9101, 'moria1')
      writer.record_relay('74A910646BCEEFBCD2E874FC1DC997430F968145', '199.254.238.53', 443, 'longclaw')

    self.assertEqual('moria1', cache.relay_nickname('9695DFC35FFEB861329B9F1AB04C46397020CE31'))
    self.assertEqual('longclaw', cache.relay_nickname('74A910646BCEEFBCD2E874FC1DC997430F968145'))
    self.assertEqual('caersidi', cache.relay_nickname('3EA8E960F6B94CE30062AA8EF02894C00F8D1E66'))

    self.assertEqual(None, cache.relay_nickname('66E1D8F00C49820FE8AA26003EC49B6F069E8AE3'))

  @patch('nyx.data_directory', Mock(return_value = None))
  def test_relay_address(self):
    """
    Basic checks for registering and fetching nicknames.
    """

    cache = nyx.cache()

    with cache.write() as writer:
      writer.record_relay('3EA8E960F6B94CE30062AA8EF02894C00F8D1E66', '208.113.165.162', 1443, 'caersidi')
      writer.record_relay('9695DFC35FFEB861329B9F1AB04C46397020CE31', '128.31.0.34', 9101, 'moria1')
      writer.record_relay('74A910646BCEEFBCD2E874FC1DC997430F968145', '199.254.238.53', 443, 'longclaw')

    self.assertEqual(('128.31.0.34', 9101), cache.relay_address('9695DFC35FFEB861329B9F1AB04C46397020CE31'))
    self.assertEqual(('199.254.238.53', 443), cache.relay_address('74A910646BCEEFBCD2E874FC1DC997430F968145'))
    self.assertEqual(('208.113.165.162', 1443), cache.relay_address('3EA8E960F6B94CE30062AA8EF02894C00F8D1E66'))

    self.assertEqual(None, cache.relay_address('66E1D8F00C49820FE8AA26003EC49B6F069E8AE3'))

  @patch('nyx.data_directory', Mock(return_value = None))
  def test_record_relay_when_updating(self):
    cache = nyx.cache()

    with cache.write() as writer:
      writer.record_relay('3EA8E960F6B94CE30062AA8EF02894C00F8D1E66', '208.113.165.162', 1443, 'caersidi')

    self.assertEqual('caersidi', cache.relay_nickname('3EA8E960F6B94CE30062AA8EF02894C00F8D1E66'))

    with cache.write() as writer:
      writer.record_relay('3EA8E960F6B94CE30062AA8EF02894C00F8D1E66', '128.31.0.34', 9101, 'moria1')

    self.assertEqual('moria1', cache.relay_nickname('3EA8E960F6B94CE30062AA8EF02894C00F8D1E66'))

  @patch('nyx.data_directory', Mock(return_value = None))
  def test_record_relay_when_invalid(self):
    """
    Provide malformed information to record_relay.
    """

    with nyx.cache().write() as writer:
      self.assertRaisesRegexp(ValueError, re.escape("'blarg' isn't a valid fingerprint"), writer.record_relay, 'blarg', '208.113.165.162', 1443, 'caersidi')
      self.assertRaisesRegexp(ValueError, re.escape("'blarg' isn't a valid address"), writer.record_relay, '3EA8E960F6B94CE30062AA8EF02894C00F8D1E66', 'blarg', 1443, 'caersidi')
      self.assertRaisesRegexp(ValueError, re.escape("'blarg' isn't a valid port"), writer.record_relay, '3EA8E960F6B94CE30062AA8EF02894C00F8D1E66', '208.113.165.162', 'blarg', 'caersidi')
      self.assertRaisesRegexp(ValueError, re.escape("'~blarg' isn't a valid nickname"), writer.record_relay, '3EA8E960F6B94CE30062AA8EF02894C00F8D1E66', '208.113.165.162', 1443, '~blarg')
