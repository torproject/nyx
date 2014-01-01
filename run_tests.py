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
  "pep8.blacklist": [],
  "pyflakes.ignore": [],
})

ARM_BASE = os.path.dirname(__file__)

SRC_PATHS = [os.path.join(ARM_BASE, path) for path in (
  'arm',
  'test',
)]


def main():
  load_settings()

  test_config = stem.util.conf.get_config("test")
  test_config.load(os.path.join(ARM_BASE, "test", "settings.cfg"))

  clean_orphaned_pyc()

  tests = unittest.defaultTestLoader.discover('test', pattern='*.py')
  test_runner = unittest.TextTestRunner()
  test_runner.run(tests)

  print

  static_check_issues = {}

  if stem.util.system.is_available("pyflakes"):
    static_check_issues.update(get_pyflakes_issues(SRC_PATHS))

  if stem.util.system.is_available("pep8"):
    static_check_issues.update(get_stylistic_issues(SRC_PATHS))

  if static_check_issues:
    print "STATIC CHECKS"

    for file_path in static_check_issues:
      print "* %s" % file_path

      for line_number, msg in static_check_issues[file_path]:
        line_count = "%-4s" % line_number
        print "  line %s - %s" % (line_count, msg)

      print


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


def get_stylistic_issues(paths):
  """
  Checks for stylistic issues that are an issue according to the parts of PEP8
  we conform to. This alsochecks a few other stylistic issues:

  * two space indentations
  * tabs are the root of all evil and should be shot on sight
  * standard newlines (\\n), not windows (\\r\\n) nor classic mac (\\r)
  * checks that we're using 'as' for exceptions rather than a comma

  :param list paths: paths to search for stylistic issues

  :returns: **dict** of the form ``path => [(line_number, message)...]``
  """

  ignored_issues = ','.join(CONFIG["pep8.ignore"])
  issues = {}

  for path in paths:
    pep8_output = stem.util.system.call(
      "pep8 --ignore %s %s" % (ignored_issues, path),
      ignore_exit_status = True,
    )

    for line in pep8_output:
      line_match = re.match("^(.*):(\d+):(\d+): (.*)$", line)

      if line_match:
        path, line, _, issue = line_match.groups()

        if path in CONFIG["pep8.blacklist"]:
          continue

        issues.setdefault(path, []).append((int(line), issue))

    for file_path in _get_files_with_suffix(path):
      if file_path in CONFIG["pep8.blacklist"]:
        continue

      with open(file_path) as f:
        file_contents = f.read()

      lines, file_issues, prev_indent = file_contents.split("\n"), [], 0
      is_block_comment = False

      for index, line in enumerate(lines):
        whitespace, content = re.match("^(\s*)(.*)$", line).groups()

        if '"""' in content:
          is_block_comment = not is_block_comment

        if "\t" in whitespace:
          file_issues.append((index + 1, "indentation has a tab"))
        elif "\r" in content:
          file_issues.append((index + 1, "contains a windows newline"))
        elif content != content.rstrip():
          file_issues.append((index + 1, "line has trailing whitespace"))
        elif content.lstrip().startswith("except") and content.endswith(", exc:"):
          # Python 2.6 - 2.7 supports two forms for exceptions...
          #
          #   except ValueError, exc:
          #   except ValueError as exc:
          #
          # The former is the old method and no longer supported in python 3
          # going forward.

          file_issues.append((index + 1, "except clause should use 'as', not comma"))

      if file_issues:
        issues[file_path] = file_issues

  return issues


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


def _get_files_with_suffix(base_path, suffix = ".py"):
  """
  Iterates over files in a given directory, providing filenames with a certain
  suffix.

  :param str base_path: directory to be iterated over
  :param str suffix: filename suffix to look for

  :returns: iterator that yields the absolute path for files with the given suffix
  """

  if os.path.isfile(base_path):
    if base_path.endswith(suffix):
      yield base_path
  else:
    for root, _, files in os.walk(base_path):
      for filename in files:
        if filename.endswith(suffix):
          yield os.path.join(root, filename)


if __name__ == '__main__':
  main()
