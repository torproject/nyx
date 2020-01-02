#!/usr/bin/env python
# Copyright 2010-2020, Damian Johnson and The Tor Project
# See LICENSE for licensing information

import setuptools
import os
import re
import sys

if '--dryrun' in sys.argv:
  DRY_RUN = True
  sys.argv.remove('--dryrun')
else:
  DRY_RUN = False

SUMMARY = 'Terminal status monitor for Tor (https://www.torproject.org/).'
DRY_RUN_SUMMARY = 'Ignore this package. This is dry-run release creation to work around PyPI limitations (https://github.com/pypa/packaging-problems/issues/74#issuecomment-260716129).'

DESCRIPTION = """
Nyx is a command-line monitor for Tor. With this you can get detailed real-time information about your relay such as bandwidth usage, connections, logs, and much more. For more information see `Nyx's homepage <https://nyx.torproject.org/>`_.

Quick Start
-----------

To install you can either use...

::

  pip install nyx

... or install from the source tarball. Nyx supports both the python 2.x and 3.x series.
"""

MANIFEST = """
include LICENSE
include MANIFEST.in
include nyx.1
include run_nyx
include run_tests.py
graft test
graft web
global-exclude __pycache__
global-exclude *.orig
global-exclude *.pyc
global-exclude *.swp
global-exclude *.swo
global-exclude *~
""".strip()

# We cannot import our own modules since if they import stem it'll break
# installation. As such, just reading our file for the parameters we need.

ATTR = {}
ATTR_LINE = re.compile("^__(\S+)__ = '(.+)'")

with open('nyx/__init__.py') as init_file:
  for line in init_file:
    m = ATTR_LINE.match(line)

    if m:
      ATTR[m.group(1)] = m.group(2)

# installation requires us to be in our setup.py's directory

os.chdir(os.path.dirname(os.path.abspath(__file__)))

with open('MANIFEST.in', 'w') as manifest_file:
  manifest_file.write(MANIFEST)

try:
  setuptools.setup(
    name = 'nyx-dry-run' if DRY_RUN else 'nyx',
    version = ATTR['version'],
    description = DRY_RUN_SUMMARY if DRY_RUN else SUMMARY,
    long_description = DESCRIPTION,
    license = ATTR['license'],
    author = ATTR['author'],
    author_email = ATTR['contact'],
    url = ATTR['url'],
    packages = ['nyx', 'nyx.panel'],
    keywords = 'tor onion controller',
    install_requires = ['stem>=1.7.0'],
    package_data = {'nyx': ['settings/*']},
    entry_points = {'console_scripts': ['nyx = nyx.__init__:main']},
    classifiers = [
      'Development Status :: 5 - Production/Stable',
      'Environment :: Console :: Curses',
      'Intended Audience :: System Administrators',
      'License :: OSI Approved :: GNU General Public License v3 (GPLv3)',
      'Topic :: Security',
    ],
  )
finally:
  if os.path.exists('MANIFEST.in'):
    os.remove('MANIFEST.in')

  if os.path.exists('MANIFEST'):
    os.remove('MANIFEST')
