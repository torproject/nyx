#!/bin/sh
# Creates a release directory that's ready to make a red hat build. From here
# simply run:
# cd release_rpm
# ./redhat/make-rpm

./export.sh release_rpm
(cd build && git archive --format=tar packaging redhat) | (cd ./release_rpm && tar xf -)

