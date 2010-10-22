#!/usr/bin/env python
import os
import sys
from src.version import VERSION
from distutils.core import setup

setup(name='arm',
      version=VERSION,
      description='Terminal tor status monitor',
      license='GPL v3',
      author='Damian Johnson',
      author_email='atagar@torproject.org',
      url='http://www.atagar.com/arm/',
      packages=['arm', 'arm.interface', 'arm.interface.graphing', 'arm.util', 'arm.TorCtl'],
      package_dir={'arm': 'src'},
      data_files=[("/usr/bin", ["arm"]),
                  ("/usr/lib/arm", ["src/settings.cfg"]),
                  ("/usr/share/man/man1", ["debian/arm.1.gz"])],
     )

# Removes the egg_info file. Apparently it is not optional during setup
# (hardcoded in distutils/command/install.py), nor are there any arguments to
# bypass its creation.
# TODO: not sure how to remove this from the deb build too...
eggPath = '/usr/lib/arm-%s.egg-info' % VERSION
if os.path.isfile(eggPath):
  if "-q" not in sys.argv: print "Removing %s" % eggPath
  os.remove(eggPath)

