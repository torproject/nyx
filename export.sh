#!/bin/sh
# Exports a copy of arm's release branch to the given location. This copy is
# stripped of git metadata and includes a bundled copy of TorCtl and cagraph.
# This accepts an optional argument for where to place the export.

if [ $# -lt 1 ]
  then exportDst="./arm"
  else exportDst=$1
fi

# if the destination already exists then abort
if [ -d $exportDst ]
then
  echo "unable to export, destination already exists: $exportDst"
  exit 1
fi

# exports arm's release branch
mkdir $exportDst
git archive --format=tar release | (cd $exportDst && tar xf -)

# fetches our torctl and cagraph dependency
wget --quiet http://www.atagar.com/arm/resources/deps/11-06-16/torctl.tar.gz
sha256sum torctl.tar.gz
echo "5460adb1394c368ba492cc33d6681618b3d3062b3f5f70b2a87520fc291701c3 <- expected"
tar -C $exportDst/src -xzf torctl.tar.gz
rm torctl.tar.gz

#wget --quiet http://www.atagar.com/arm/resources/deps/11-06-10/cagraph.tar.gz
#sha256sum cagraph.tar.gz
#echo "1439acd40ce016f4329deb216d86f36a749e4b8bf73a313a757396af6f95310d <- expected"
#tar -C $exportDst/src -xzf cagraph.tar.gz
#rm cagraph.tar.gz

# the following installed torctl from its master repo rather than our
# dependency mirror

# fetches torctl to /tmp
#torctlDir="$(mktemp -d)"
#git clone git://git.torproject.org/pytorctl.git $torctlDir > /dev/null

# exports torctl to the arm directory
#(cd $torctlDir && git archive --format=tar --prefix=TorCtl/ master) | (cd $exportDst/src && tar xf - 2> /dev/null)

# cleans up the temporary torctl repo
#rm -rf torctlDir

echo "arm exported to $exportDst"

