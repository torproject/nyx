"""
Provides a warning and error code if python version isn't compatible.
"""

import os
import sys
import shutil
import urllib
import hashlib
import tarfile
import tempfile

# Library dependencies can be fetched on request. By default this is via
# the following mirrors with their sha256 signatures checked.
TORCTL_ARCHIVE = "http://www.atagar.com/arm/resources/deps/11-06-16/torctl.tar.gz"
TORCTL_SIG = "5460adb1394c368ba492cc33d6681618b3d3062b3f5f70b2a87520fc291701c3"

# optionally we can do an unverified fetch from the library's sources
STEM_REPO = "git://git.torproject.org/stem.git"

def isTorCtlAvailable():
  """
  True if TorCtl is already available on the platform, false otherwise.
  """
  
  try:
    import TorCtl
    return True
  except ImportError:
    return False

def isStemAvailable():
  """
  True if stem is already available on the platform, false otherwise.
  """
  
  try:
    import stem
    return True
  except ImportError:
    return False

def promptTorCtlInstall():
  """
  Asks the user to install TorCtl. This returns True if it was installed and
  False otherwise (if it was either declined or failed to be fetched).
  """
  
  userInput = raw_input("Arm requires TorCtl to run, but it's unavailable. Would you like to install it? (y/n): ")
  
  # if user says no then terminate
  if not userInput.lower() in ("y", "yes"): return False
  
  # attempt to install TorCtl, printing the issue if unsuccessful
  try:
    fetchLibrary(TORCTL_ARCHIVE, TORCTL_SIG)
    
    if not isTorCtlAvailable():
      raise IOError("Unable to install TorCtl, sorry")
    
    print "TorCtl successfully installed"
    return True
  except IOError, exc:
    print exc
    return False

def promptStemInstall():
  """
  Asks the user to install stem. This returns True if it was installed and
  False otherwise (if it was either declined or failed to be fetched).
  """
  
  userInput = raw_input("Arm requires stem to run, but it's unavailable. Would you like to install it? (y/n): ")
  
  # if user says no then terminate
  if not userInput.lower() in ("y", "yes"): return False
  
  # attempt to install stem, printing the issue if unsuccessful
  try:
    installStem()
    
    if not isStemAvailable():
      raise IOError("Unable to install stem, sorry")
    
    print "Stem successfully installed"
    return True
  except IOError, exc:
    print exc
    return False

def fetchLibrary(url, sig):
  """
  Downloads the given archive, verifies its signature, then installs the
  library. This raises an IOError if any of these steps fail.
  
  Arguments:
    url - url from which to fetch the gzipped tarball
    sig - sha256 signature for the archive
  """
  
  tmpDir = tempfile.mkdtemp()
  destination = tmpDir + "/" + url.split("/")[-1]
  urllib.urlretrieve(url, destination)
  
  # checks the signature, reading the archive in 256-byte chunks
  m = hashlib.sha256()
  fd = open(destination, "rb")
  
  while True:
    data = fd.read(256)
    if not data: break
    m.update(data)
  
  fd.close()
  actualSig = m.hexdigest()
  
  if sig != actualSig:
    raise IOError("Signature of the library is incorrect (got '%s' rather than '%s')" % (actualSig, sig))
  
  # extracts the tarball
  tarFd = tarfile.open(destination, 'r:gz')
  tarFd.extractall("src/")
  tarFd.close()
  
  # clean up the temporary contents (fails quietly if unsuccessful)
  shutil.rmtree(destination, ignore_errors=True)

def installStem():
  """
  Checks out the current git head release for stem and bundles it with arm.
  This raises an IOError if unsuccessful.
  """
  
  if isStemAvailable(): return
  
  # temporary destination for stem's git clone, guarenteed to be unoccupied
  # (to avoid conflicting with files that are already there)
  tmpFilename = tempfile.mktemp("/stem")
  
  # fetches stem
  exitStatus = os.system("git clone --quiet %s %s > /dev/null" % (STEM_REPO, tmpFilename))
  if exitStatus: raise IOError("Unable to get stem from %s. Is git installed?" % STEM_REPO)
  
  # the destination for stem will be our directory
  ourDir = os.path.dirname(os.path.realpath(__file__))
  
  # exports stem to our location
  exitStatus = os.system("(cd %s && git archive --format=tar master stem) | (cd %s && tar xf - 2> /dev/null)" % (tmpFilename, ourDir))
  if exitStatus: raise IOError("Unable to install stem to %s" % ourDir)
  
  # Clean up the temporary contents. This isn't vital so quietly fails in case
  # of errors.
  shutil.rmtree(tmpFilename, ignore_errors=True)

if __name__ == '__main__':
  majorVersion = sys.version_info[0]
  minorVersion = sys.version_info[1]
  
  if majorVersion > 2:
    print("arm isn't compatible beyond the python 2.x series\n")
    sys.exit(1)
  elif majorVersion < 2 or minorVersion < 5:
    print("arm requires python version 2.5 or greater\n")
    sys.exit(1)
  
  if not isTorCtlAvailable():
    isInstalled = promptTorCtlInstall()
    if not isInstalled: sys.exit(1)
  
  if not isStemAvailable():
    isInstalled = promptStemInstall()
    if not isInstalled: sys.exit(1)
  
  try:
    import curses
  except ImportError:
    print("arm requires curses - try installing the python-curses package\n")
    sys.exit(1)

