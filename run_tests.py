#!/usr/bin/env python
# Copyright 2013-2017, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Runs nyx's unit tests. When running this you may notice your screen flicker.
This is because we're a curses application, and so testing requires us to
render to the screen.
"""

import os
import unittest

import stem.util.conf
import stem.util.system
import stem.util.test_tools

import nyx
import test

SRC_PATHS = [os.path.join(test.NYX_BASE, path) for path in (
  'nyx',
  'test',
  'run_tests.py',
  'setup.py',
  'run_nyx',
)]


@nyx.uses_settings
def main():
  nyx.PAUSE_TIME = 0.000001  # make pauses negligibly low since our tests trigger things rapidly
  test_config = stem.util.conf.get_config('test')
  test_config.load(os.path.join(test.NYX_BASE, 'test', 'settings.cfg'))

  orphaned_pyc = stem.util.test_tools.clean_orphaned_pyc([test.NYX_BASE])

  for path in orphaned_pyc:
    print('Deleted orphaned pyc file: %s' % path)

  pyflakes_task, pycodestyle_task = None, None

  if stem.util.test_tools.is_pyflakes_available():
    pyflakes_task = stem.util.system.DaemonTask(stem.util.test_tools.pyflakes_issues, (SRC_PATHS,), start = True)

  if stem.util.test_tools.is_pep8_available():
    pycodestyle_task = stem.util.system.DaemonTask(stem.util.test_tools.stylistic_issues, (SRC_PATHS,), start = True)

  tests = unittest.defaultTestLoader.discover('test', pattern = '*.py')
  test_runner = unittest.TextTestRunner()
  test_runner.run(tests)

  print('')

  static_check_issues = {}

  if pyflakes_task:
    for path, issues in pyflakes_task.join().items():
      for issue in issues:
        static_check_issues.setdefault(path, []).append(issue)

  if pycodestyle_task:
    for path, issues in pycodestyle_task.join().items():
      for issue in issues:
        static_check_issues.setdefault(path, []).append(issue)

  if static_check_issues:
    print('STATIC CHECKS')

    for file_path in static_check_issues:
      print('* %s' % file_path)

      for issue in static_check_issues[file_path]:
        print('  line %-4s - %-40s %s' % (issue.line_number, issue.message, issue.line))

      print


if __name__ == '__main__':
  main()
