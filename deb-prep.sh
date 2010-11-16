#!/bin/sh
# Creates a release directory that's ready to make a debian build. From here
# simply run:
# cd release_build
# ./debian/make-deb

# alternate (works, but svn export is simpler):
# tar czf tor-arm_1.3.7.orig.tar.gz --exclude-vcs --exclude="*.pyc" -v release

svn export release release_build
tar czf tor-arm_1.3.7.orig.tar.gz release_build
svn export resources/build/debian release_build/debian

