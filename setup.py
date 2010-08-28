#!/usr/bin/env python
import os
from distutils.core import setup

VERSION = '1.3.6_dev'

setup(name='arm',
      version=VERSION,
      description='Terminal tor status monitor',
      license='GPL v3',
      author='Damian Johnson',
      author_email='atagar@torproject.org',
      url='http://www.atagar.com/arm/',
      packages=['arm', 'arm.interface', 'arm.interface.graphing', 'arm.util', 'arm.TorCtl'],
      package_dir={'arm': 'src'},
      scripts=["arm"],
      data_files=[("/usr/share/man/man1", ["arm.1"])],
     )

# Removes the egg_info file. Apparently it is not optional during setup
# (hardcoded in distutils/command/install.py), nor are there any arguments to
# bypass its creation.
eggPath = '/usr/local/arm-%s.egg-info' % VERSION
if os.path.isfile(eggPath): os.remove(eggPath)

