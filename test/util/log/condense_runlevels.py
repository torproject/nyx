import unittest

from nyx.util.log import condense_runlevels


class TestCondenseRunlevels(unittest.TestCase):
  def test_condense_runlevels(self):
    self.assertEqual([], condense_runlevels())
    self.assertEqual(['BW'], condense_runlevels('BW'))
    self.assertEqual(['DEBUG', 'NOTICE', 'ERR'], condense_runlevels('DEBUG', 'NOTICE', 'ERR'))
    self.assertEqual(['DEBUG-NOTICE', 'NYX DEBUG-INFO'], condense_runlevels('DEBUG', 'NYX_DEBUG', 'INFO', 'NYX_INFO', 'NOTICE'))
    self.assertEqual(['TOR/NYX NOTICE-ERR'], condense_runlevels('NOTICE', 'WARN', 'ERR', 'NYX_NOTICE', 'NYX_WARN', 'NYX_ERR'))
    self.assertEqual(['DEBUG', 'TOR/NYX NOTICE-ERR', 'BW'], condense_runlevels('DEBUG', 'NOTICE', 'WARN', 'ERR', 'NYX_NOTICE', 'NYX_WARN', 'NYX_ERR', 'BW'))
