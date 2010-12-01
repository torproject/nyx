#!/bin/sh
# Creates a release directory that's ready to make a red hat build. From here
# simply run:
# cd release_rpm
# ./redhat/make-rpm

svn export release release_rpm
svn export resources/build/redhat release_rpm/redhat

