#!/bin/sh
# Exports a copy of arm's release branch to the given location. This copy is
# stripped of git metadata and includes a bundled copy of TorCtl. This accepts
# an optional argument for where to place the export.

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

# fetches torctl to /tmp
torctlDir="$(mktemp -d)"
git clone git://git.torproject.org/pytorctl.git $torctlDir > /dev/null

# exports torctl to the arm directory
(cd $torctlDir && git archive --format=tar --prefix=TorCtl/ master) | (cd $exportDst/src && tar xf - 2> /dev/null)

# cleans up the temporary torctl repo
rm -rf torctlDir

echo "arm exported to $exportDst"

