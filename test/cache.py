"""
Unit tests for nyx.cache.
"""

import tempfile
import unittest

import nyx

from mock import Mock, patch


class TestCache(unittest.TestCase):
  def setUp(self):
    nyx.CACHE = None  # drop cached database reference

  @patch('nyx.data_directory', Mock(return_value = None))
  def test_memory_cache(self):
    with nyx.cache() as cache:
      self.assertEqual((0, 'main', ''), cache.execute("PRAGMA database_list").fetchone())

      cache.execute('CREATE TABLE aliases(alias TEXT, command TEXT)')
      cache.execute('INSERT INTO aliases(alias, command) VALUES (?,?)', ('l', 'ls -xF --color=auto'))
      cache.execute('INSERT INTO aliases(alias, command) VALUES (?,?)', ('ll', 'ls -hlA --color=auto'))
      self.assertEqual('ls -hlA --color=auto', cache.execute('SELECT command FROM aliases WHERE alias=?', ('ll',)).fetchone()[0])

  def test_file_cache(self):
    with tempfile.NamedTemporaryFile(suffix = '.sqlite') as tmp:
      with patch('nyx.data_directory', Mock(return_value = tmp.name)):
        with nyx.cache() as cache:
          self.assertEqual((0, 'main', tmp.name), cache.execute("PRAGMA database_list").fetchone())

          cache.execute('CREATE TABLE aliases(alias TEXT, command TEXT)')
          cache.execute('INSERT INTO aliases(alias, command) VALUES (?,?)', ('l', 'ls -xF --color=auto'))
          cache.execute('INSERT INTO aliases(alias, command) VALUES (?,?)', ('ll', 'ls -hlA --color=auto'))
          cache.commit()

        nyx.CACHE = None

        with nyx.cache() as cache:
          self.assertEqual('ls -hlA --color=auto', cache.execute('SELECT command FROM aliases WHERE alias=?', ('ll',)).fetchone()[0])
