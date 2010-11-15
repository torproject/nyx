#!/usr/bin/env python
import os
import sys
import gzip
from src.version import VERSION
from distutils.core import setup

# Compresses the man page. This is a temporary file that we'll install. If
# something goes wrong then we'll print the issue and use the uncompressed man
# page instead. Build resources are cleaned up by the installion script later.
try:
  manInputFile = open('arm.1', 'r')
  manContents = manInputFile.read()
  manInputFile.close()
  
  if not os.path.exists('./build'): os.makedirs('./build')
  manOutputFile = gzip.open('build/arm.1.gz', 'wb')
  manOutputFile.write(manContents)
  manOutputFile.close()
  
  manFilename = "build/arm.1.gz"
except IOError, exc:
  print "Unable to compress man page: %s" % exc
  manFilename = "arm.1"

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
                  ("/usr/share/man/man1", [manFilename]),
                  ("/usr/lib/arm", ["src/settings.cfg"])],
     )

# Removes the egg_info file. Apparently it is not optional during setup
# (hardcoded in distutils/command/install.py), nor are there any arguments to
# bypass its creation.
# TODO: not sure how to remove this from the deb build too...
eggPath = '/usr/lib/arm-%s.egg-info' % VERSION
if os.path.isfile(eggPath):
  if "-q" not in sys.argv: print "Removing %s" % eggPath
  os.remove(eggPath)

