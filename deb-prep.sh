#!/bin/sh
# Creates a release directory that's ready to make a debian build. From here
# simply run:
# cd release_deb
# ./debian/make-deb
# 
# To try rebulding:
# dget http://www.atagar.com/transfer/tmp/armBuild_12-7-10/tor-arm_1.4.0.1-1.dsc
# dpkg-source -x tor-arm_1.4.0.1-1.dsc
# debuild -rfakeroot -uc -us -j3

# alternate (works, but svn export is simpler):
# tar czf tor-arm_1.3.7.orig.tar.gz --exclude-vcs --exclude="*.pyc" -v release

export DEB_VERSION="1.2.3.4"

mkdir release_deb
git archive --format=tar release | (cd ./release_deb && tar xf -)

# edits the man page path for the sample armrc to reflect where it's located
# on debian:
# /usr/share/doc/arm/armrc.sample -> /usr/share/doc/tor-arm/armrc.sample.gz
sed -i 's/\/usr\/share\/doc\/arm\/armrc.sample/\/usr\/share\/doc\/tor-arm\/armrc.sample.gz/g' release_deb/arm.1

tar czf tor-arm_${DEB_VERSION}.orig.tar.gz release_deb

(cd build && git archive --format=tar packaging debian) | (cd ./release_deb && tar xf -)

