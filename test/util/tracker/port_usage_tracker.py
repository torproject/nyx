import unittest

from arm.util.tracker import _process_for_ports

from mock import Mock, patch

LSOF_OUTPUT = """\
COMMAND  PID   USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
tor     2001 atagar   14u  IPv4  14048      0t0  TCP localhost:9051->localhost:37277 (ESTABLISHED)
tor     2001 atagar   15u  IPv4  22024      0t0  TCP localhost:9051->localhost:51849 (ESTABLISHED)
python  2462 atagar    3u  IPv4  14047      0t0  TCP localhost:37277->localhost:9051 (ESTABLISHED)
python  3444 atagar    3u  IPv4  22023      0t0  TCP localhost:51849->localhost:9051 (ESTABLISHED)
"""

BAD_LSOF_OUTPUT_NO_ENTRY = """\
COMMAND  PID   USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
"""

BAD_LSOF_OUTPUT_NOT_ESTABLISHED = """\
COMMAND  PID   USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
tor     2001 atagar   14u  IPv4  14048      0t0  TCP localhost:9051->localhost:37277 (CLOSE_WAIT)
"""

BAD_LSOF_OUTPUT_MISSING_FIELD = """\
COMMAND  PID   USER   TYPE DEVICE SIZE/OFF NODE NAME
tor     2001 atagar   IPv4  14048      0t0  TCP localhost:9051->localhost:37277 (ESTABLISHED)
"""

BAD_LSOF_OUTPUT_UNRECOGNIZED_MAPPING = """\
COMMAND  PID   USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
tor     2001 atagar   14u  IPv4  14048      0t0  TCP localhost:9051=>localhost:37277 (ESTABLISHED)
"""

BAD_LSOF_OUTPUT_NO_ADDRESS = """\
COMMAND  PID   USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
tor     2001 atagar   14u  IPv4  14048      0t0  TCP 9051->localhost:37277 (ESTABLISHED)
"""

BAD_LSOF_OUTPUT_INVALID_PORT = """\
COMMAND  PID   USER   FD   TYPE DEVICE SIZE/OFF NODE NAME
tor     2001 atagar   14u  IPv4  14048      0t0  TCP localhost:9037351->localhost:37277 (ESTABLISHED)
"""


class TestPortUsageTracker(unittest.TestCase):
  @patch('arm.util.tracker.system.call', Mock(return_value = LSOF_OUTPUT.split('\n')))
  def test_process_for_ports(self):
    self.assertEqual({}, _process_for_ports([], []))
    self.assertEqual({}, _process_for_ports([80, 443], []))
    self.assertEqual({}, _process_for_ports([], [80, 443]))

    self.assertEqual({37277: 'python', 51849: 'tor'}, _process_for_ports([37277], [51849]))

  @patch('arm.util.tracker.system.call')
  def test_process_for_ports_malformed(self, call_mock):
    # Issues that are valid, but should result in us not having any content.

    test_inputs = (
      BAD_LSOF_OUTPUT_NO_ENTRY,
      BAD_LSOF_OUTPUT_NOT_ESTABLISHED,
    )

    for test_input in test_inputs:
      call_mock.return_value = test_input.split('\n')
      self.assertEqual({}, _process_for_ports([80], [443]))

    # Isuses that are reported as errors.

    call_mock.return_value = []
    self.assertRaises(IOError, _process_for_ports, [80], [443])

    test_inputs = (
      BAD_LSOF_OUTPUT_MISSING_FIELD,
      BAD_LSOF_OUTPUT_UNRECOGNIZED_MAPPING,
      BAD_LSOF_OUTPUT_NO_ADDRESS,
      BAD_LSOF_OUTPUT_INVALID_PORT,
    )

    for test_input in test_inputs:
      call_mock.return_value = test_input.split('\n')
      self.assertRaises(IOError, _process_for_ports, [80], [443])
