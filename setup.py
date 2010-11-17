#!/usr/bin/env python
import os
import sys
import gzip
from src.version import VERSION
from distutils.core import setup

# Compresses the man page. This is a temporary file that we'll install. If
# something goes wrong then we'll print the issue and use the uncompressed man
# page instead.

try:
  manInputFile = open('arm.1', 'r')
  manContents = manInputFile.read()
  manInputFile.close()
  
  manOutputFile = gzip.open('/tmp/arm.1.gz', 'wb')
  manOutputFile.write(manContents)
  manOutputFile.close()
  
  manFilename = "/tmp/arm.1.gz"
except IOError, exc:
  print "Unable to compress man page: %s" % exc
  manFilename = "arm.1"

# if this is placing resources for debian then the sample armrc should go in
# tor-arm instead of arm
docPath = "/usr/share/doc/"
if "--install-layout=deb" in sys.argv: docPath += "tor-arm"
else: docPath += "arm"

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
                  ("/usr/share/pyshared/arm", ["src/settings.cfg"])],
     )

# Cleans up the temporary compressed man page.
if manFilename == '/tmp/arm.1.gz' and os.path.isfile(manFilename):
  if "-q" not in sys.argv: print "Removing %s" % manFilename
  os.remove(manFilename)

