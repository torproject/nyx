#!/usr/bin/env python
# Copyright 2013, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Runs arm's unit tests. This is a curses application so we're pretty limited on
the test coverage we can achieve, but exercising what we can.
"""

import os
import re
import unittest

import stem.util.conf
import stem.util.system

from arm.util import load_settings

CONFIG = stem.util.conf.config_dict("test", {
  "pep8.ignore": [],
  "pyflakes.ignore": [],
})

ARM_BASE = os.path.dirname(__file__)

SRC_PATHS = [os.path.join(ARM_BASE, path) for path in (
  'arm',
  'test',
)]


def clean_orphaned_pyc():
  for root, _, files in os.walk(os.path.dirname(__file__)):
    for filename in files:
      if filename.endswith('.pyc'):
        pyc_path = os.path.join(root, filename)

        if "__pycache__" in pyc_path:
          continue

        if not os.path.exists(pyc_path[:-1]):
          print "Deleting orphaned pyc file: %s" % pyc_path
          os.remove(pyc_path)


def get_pyflakes_issues(paths):
  """
  Performs static checks via pyflakes.

  :param list paths: paths to search for problems

  :returns: dict of the form ``path => [(line_number, message)...]``
  """

  pyflakes_ignore = {}

  for line in CONFIG["pyflakes.ignore"]:
    path, issue = line.split("=>")
    pyflakes_ignore.setdefault(path.strip(), []).append(issue.strip())

  def is_ignored(path, issue):
    # Paths in pyflakes_ignore are relative, so we need to check to see if our
    # path ends with any of them.

    for ignore_path in pyflakes_ignore:
      if path.endswith(ignore_path) and issue in pyflakes_ignore[ignore_path]:
        return True

    return False

  # Pyflakes issues are of the form...
  #
  #   FILE:LINE: ISSUE
  #
  # ... for instance...
  #
  #   stem/prereq.py:73: 'long_to_bytes' imported but unused
  #   stem/control.py:957: undefined name 'entry'

  issues = {}

  for path in paths:
    pyflakes_output = stem.util.system.call(
      "pyflakes %s" % path,
      ignore_exit_status = True,
    )

    for line in pyflakes_output:
      line_match = re.match("^(.*):(\d+): (.*)$", line)

      if line_match:
        path, line, issue = line_match.groups()

        if not is_ignored(path, issue):
          issues.setdefault(path, []).append((int(line), issue))

  return issues


def main():
  load_settings()

  clean_orphaned_pyc()

  tests = unittest.defaultTestLoader.discover('test', pattern='*.py')
  test_runner = unittest.TextTestRunner()
  test_runner.run(tests)

  print

  static_check_issues = {}

  if stem.util.system.is_available("pyflakes"):
    static_check_issues.update(get_pyflakes_issues(SRC_PATHS))

  if static_check_issues:
    print "STATIC CHECKS"

    for file_path in static_check_issues:
      print "* %s" % file_path

      for line_number, msg in static_check_issues[file_path]:
        line_count = "%-4s" % line_number
        print "  line %s - %s" % (line_count, msg)

      print

if __name__ == '__main__':
  main()
