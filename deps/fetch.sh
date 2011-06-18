#!/bin/sh
# This fetches copies of arm's library dependencies. They're relatively static
# and provided with the tarball to avoid complicating the install process.
# 
# TorCtl (https://gitweb.torproject.org/pytorctl.git)
#   6/18/11 - 5460adb1394c368ba492cc33d6681618b3d3062b3f5f70b2a87520fc291701c3
#   6/10/11 - be583e53b2bccf09a7126c5271f9af5682447903b6ac92cf1cf78ca5b35273ed
# 
# cagraph (https://code.google.com/p/cagraph/)
#   6/10/11 - 1439acd40ce016f4329deb216d86f36a749e4b8bf73a313a757396af6f95310d

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
tar --strip-components=1 -xzf cagraph-1.2.tar.gz cagraph-1.2/cagraph
tar -czf cagraph.tar.gz cagraph
rm -rf cagraph/
rm cagraph-1.2.tar.gz

echo "Sha256 Checksums:"
sha256sum torctl.tar.gz
sha256sum cagraph.tar.gz

