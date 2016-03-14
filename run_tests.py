#!/usr/bin/env python
# Copyright 2013, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Runs nyx's unit tests. This is a curses application so we're pretty limited on
the test coverage we can achieve, but exercising what we can.
"""

import os
import unittest

import stem.util.conf
import stem.util.test_tools

import nyx

NYX_BASE = os.path.dirname(__file__)

SRC_PATHS = [os.path.join(NYX_BASE, path) for path in (
  'nyx',
  'test',
  'run_tests.py',
  'run_nyx',
)]


@nyx.uses_settings
def main():
  nyx.TESTING = True
  test_config = stem.util.conf.get_config('test')
  test_config.load(os.path.join(NYX_BASE, 'test', 'settings.cfg'))

  orphaned_pyc = stem.util.test_tools.clean_orphaned_pyc(NYX_BASE)

  for path in orphaned_pyc:
    print('Deleted orphaned pyc file: %s' % path)

  tests = unittest.defaultTestLoader.discover('test', pattern='*.py')
  test_runner = unittest.TextTestRunner()
  test_runner.run(tests)

  print('')

  static_check_issues = {}

  if stem.util.test_tools.is_pyflakes_available():
    pyflakes_issues = stem.util.test_tools.pyflakes_issues(SRC_PATHS)

    for path, issues in pyflakes_issues.items():
      for issue in issues:
        static_check_issues.setdefault(path, []).append(issue)

  if stem.util.test_tools.is_pep8_available():
    pep8_issues = stem.util.test_tools.stylistic_issues(
      SRC_PATHS,
      check_newlines = True,
      check_exception_keyword = True,
      prefer_single_quotes = True,
    )

    for path, issues in pep8_issues.items():
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
