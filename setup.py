#!/usr/bin/env python
import os
import sys
import gzip
import tempfile
from src.version import VERSION
from distutils.core import setup

# Use 'tor-arm' instead of 'arm' in the path for the sample armrc if we're
# building for debian.

isDebInstall = False
for arg in sys.argv:
  if "tor-arm" in arg:
    isDebInstall = True
    break

docPath = "/usr/share/doc/%s" % ("tor-arm" if isDebInstall else "arm")

# Provides the configuration option to install to "/usr/share" rather than as a
# python module. Alternatives are to either provide this as an input argument
# (not an option for deb/rpm builds) or add a setup.cfg with:
#   [install]
#   install-purelib=/usr/share
# which would mean a bit more unnecessary clutter.

manFilename = "arm.1"
if "install" in sys.argv:
  sys.argv += ["--install-purelib", "/usr/share"]
  
  # Compresses the man page. This is a temporary file that we'll install. If
  # something goes wrong then we'll print the issue and use the uncompressed man
  # page instead.
  
  try:
    manInputFile = open('arm.1', 'r')
    manContents = manInputFile.read()
    manInputFile.close()
    
    # temporary destination for the man page guarenteed to be unoccupied (to
    # avoid conflicting with files that are already there)
    manOutputFile = gzip.open(tempfile.mktemp("/arm.1.gz"), 'wb')
    manOutputFile.write(manContents)
    manOutputFile.close()
    
    # places in tmp rather than a relative path to avoid having this copy appear
    # in the deb and rpm builds
    manFilename = manOutputFile.name
  except IOError, exc:
    print "Unable to compress man page: %s" % exc

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
                  (docPath, ["armrc.sample"]),
                  ("/usr/share/arm", ["src/settings.cfg"])],
     )

# Cleans up the temporary compressed man page.
if manFilename != 'arm.1' and os.path.isfile(manFilename):
  if "-q" not in sys.argv: print "Removing %s" % manFilename
  os.remove(manFilename)

# Removes the egg_info file. Apparently it is not optional during setup
# (hardcoded in distutils/command/install.py), nor are there any arguments to
# bypass its creation.
# TODO: not sure how to remove this from the deb build too...
eggPath = '/usr/share/arm-%s.egg-info' % VERSION

if not isDebInstall and os.path.isfile(eggPath):
  if "-q" not in sys.argv: print "Removing %s" % eggPath
  os.remove(eggPath)

