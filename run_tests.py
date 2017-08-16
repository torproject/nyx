#!/usr/bin/env python
# Copyright 2013-2017, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Runs nyx's unit tests. When running this you may notice your screen flicker.
This is because we're a curses application, and so testing requires us to
render to the screen.
"""

import os
import multiprocessing
import unittest

import stem.util.conf
import stem.util.test_tools

import nyx

NYX_BASE = os.path.dirname(__file__)

SRC_PATHS = [os.path.join(NYX_BASE, path) for path in (
  'nyx',
  'test',
  'run_tests.py',
  'setup.py',
  'run_nyx',
)]


def _run_wrapper(conn, runner, args):
  os.nice(15)
  conn.send(runner(*args) if args else runner())
  conn.close()


@nyx.uses_settings
def main():
  nyx.TESTING = True
  test_config = stem.util.conf.get_config('test')
  test_config.load(os.path.join(NYX_BASE, 'test', 'settings.cfg'))

  orphaned_pyc = stem.util.test_tools.clean_orphaned_pyc(NYX_BASE)

  for path in orphaned_pyc:
    print('Deleted orphaned pyc file: %s' % path)

  pyflakes_task, pyflakes_pipe = None, None
  pycodestyle_task, pycodestyle_pipe = None, None

  if stem.util.test_tools.is_pyflakes_available():
    pyflakes_pipe, child_pipe = multiprocessing.Pipe()
    pyflakes_task = multiprocessing.Process(target = _run_wrapper, args = (child_pipe, stem.util.test_tools.pyflakes_issues, (SRC_PATHS,)))
    pyflakes_task.start()

  if stem.util.test_tools.is_pep8_available():
    pycodestyle_pipe, child_pipe = multiprocessing.Pipe()
    pycodestyle_task = multiprocessing.Process(target = _run_wrapper, args = (child_pipe, stem.util.test_tools.stylistic_issues, (SRC_PATHS, True, True, True)))
    pycodestyle_task.start()

  tests = unittest.defaultTestLoader.discover('test', pattern = '*.py')
  test_runner = unittest.TextTestRunner()
  test_runner.run(tests)

  print('')

  static_check_issues = {}

  if pyflakes_task:
    pyflakes_issues = pyflakes_pipe.recv()
    pyflakes_task.join()

    for path, issues in pyflakes_issues.items():
      for issue in issues:
        static_check_issues.setdefault(path, []).append(issue)

  if pycodestyle_task:
    pycodestyle_issues = pycodestyle_pipe.recv()
    pycodestyle_task.join()

    for path, issues in pycodestyle_issues.items():
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
