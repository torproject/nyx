#!/usr/bin/env python
# Copyright 2013, Damian Johnson and The Tor Project
# See LICENSE for licensing information

"""
Runs arm's unit tests. This is a curses application so we're pretty limited on
the test coverage we can achieve, but exercising what we can.
"""

import os
import unittest


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


def main():
  clean_orphaned_pyc()

  tests = unittest.defaultTestLoader.discover('test', pattern='*.py')
  test_runner = unittest.TextTestRunner()
  test_runner.run(tests)


if __name__ == '__main__':
  main()
