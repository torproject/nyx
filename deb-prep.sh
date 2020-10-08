#!/bin/sh
# Creates a release directory that's ready to make a debian build. From here
# simply:
# update build/debian/changelog
# ./deb-prep.sh
# cd release_deb
# ./debian/make-deb
# 
# To try rebulding:
# dget http://www.atagar.com/transfer/tmp/armBuild_12-7-10/tor-arm_1.4.0.1-1.dsc
# dpkg-source -x tor-arm_1.4.0.1-1.dsc
# debuild -rfakeroot -uc -us -j3

# alternate (works, but not sure if it'll miss resources like gitignore):
# tar czf tor-arm_1.3.7.orig.tar.gz --exclude-vcs --exclude="*.pyc" -v release

if [ $# -lt 1 ]
  then
    echo "Usage: ./deb-prep.sh <nyx version>"
    exit 1
  else debVersion=$1
fi

mkdir release_deb
git archive --format=tar release | (cd ./release_deb && tar xf -)

# edits the man page path for the sample nyxconfig to reflect where it's located
# on debian:
# /usr/share/doc/nyx/config.sample -> /usr/share/doc/nyx/config.sample.gz
sed -i 's/\/usr\/share\/doc\/nyx\/config.sample/\/usr\/share\/doc\/nyx\/config.sample.gz/g' release_deb/src/resources/nyx.1

tar czf nyx_${debVersion}.orig.tar.gz release_deb

(cd build && git archive --format=tar packaging debian) | (cd ./release_deb && tar xf -)

