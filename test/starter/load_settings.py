import io
import unittest

from mock import patch

from arm.starter import _load_settings


class TestArgumentParsing(unittest.TestCase):
  def test_we_can_load_the_settings(self):
    config = _load_settings(self.id())
    self.assertEqual(config.get('settings_loaded'), 'true')

  @patch('stem.util.conf.open', create = True)
  def test_when_file_doesnt_exist(self, open_mock):
    open_mock.side_effect = IOError("No such file or directory")

    try:
      _load_settings(self.id())
      self.fail("We didn't raise an exception for a missing settings.cfg")
    except ValueError as exc:
      self.assertTrue("Unable to load arm's internal configuration" in str(exc))

  @patch('stem.util.conf.open', create = True)
  def test_that_repeated_calls_are_ignored(self, open_mock):
    open_mock.return_value = io.BytesIO("settings_loaded true")

    _load_settings(self.id())
    _load_settings(self.id())
    _load_settings(self.id())
    self.assertEqual(1, open_mock.call_count)
