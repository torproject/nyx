#!/bin/sh
# This fetches copies of arm's library dependencies. They're relatively static
# and provided with the tarball to avoid complicating the install process.
# 
# TorCtl (https://gitweb.torproject.org/pytorctl.git)
#   6/10/11 - be583e53b2bccf09a7126c5271f9af5682447903b6ac92cf1cf78ca5b35273ed
# 
# cagraph (https://code.google.com/p/cagraph/)
#   6/10/11 - a6928f07adb8f8d4b0076e01c0ec264e1acaaa6db21376c854fa827c9b04e3f3

# removes old archives if they exist
[ -f "torctl.tar.gz" ] && rm -f "torctl.tar.gz"
[ -f "cagraph-1.2.tar.gz" ] && rm -f "cagraph-1.2.tar.gz"

# retrieves torctl
# note: This checksum changes with each fetch (maybe a timestamp's included?)
git clone --quiet git://git.torproject.org/pytorctl.git
cd pytorctl
git archive --format=tar --prefix=TorCtl/ master | gzip > ../torctl.tar.gz
cd ..
rm -rf pytorctl

# retrieves cagraph
wget --quiet http://cagraph.googlecode.com/files/cagraph-1.2.tar.gz

echo "Sha256 Checksums:"
sha256sum torctl.tar.gz
sha256sum cagraph-1.2.tar.gz

