"""
Unit tests for arm's initialization module.
"""

import unittest

from arm.starter import _get_args, ARGS

class TestArgumentParsing(unittest.TestCase):
  def test_that_we_get_default_values(self):
    args = _get_args([])

    for attr in ARGS:
      self.assertEqual(ARGS[attr], getattr(args, attr))

  def test_that_we_load_arguments(self):
    args = _get_args(['--interface', '10.0.0.25:80'])
    self.assertEqual('10.0.0.25', args.control_address)
    self.assertEqual(80, args.control_port)

    args = _get_args(['--interface', '80'])
    self.assertEqual(ARGS['control_address'], args.control_address)
    self.assertEqual(80, args.control_port)

    args = _get_args(['--socket', '/tmp/my_socket', '--config', '/tmp/my_config'])
    self.assertEqual('/tmp/my_socket', args.control_socket)
    self.assertEqual('/tmp/my_config', args.config)

    args = _get_args(['--debug', '--blind'])
    self.assertEqual(True, args.debug)
    self.assertEqual(True, args.blind)

    args = _get_args(['--event', 'D1'])
    self.assertEqual('D1', args.logged_events)

    args = _get_args(['--version'])
    self.assertEqual(True, args.print_version)

    args = _get_args(['--help'])
    self.assertEqual(True, args.print_help)

  def test_examples(self):
    args = _get_args(['-b', '-i', '1643'])
    self.assertEqual(True, args.blind)
    self.assertEqual(1643, args.control_port)

    args = _get_args(['-e', 'we', '-c', '/tmp/cfg'])
    self.assertEqual('we', args.logged_events)
    self.assertEqual('/tmp/cfg', args.config)

  def test_that_we_reject_invalid_interfaces(self):
    invalid_inputs = (
      '',
      '    ',
      'blarg',
      '127.0.0.1',
      '127.0.0.1:',
      ':80',
      '400.0.0.1:80',
      '127.0.0.1:-5',
      '127.0.0.1:500000',
    )

    for invalid_input in invalid_inputs:
      self.assertRaises(ValueError, _get_args, ['--interface', invalid_input])

