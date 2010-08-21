#!/usr/bin/env python

from distutils.core import setup

setup(name='arm',
      version='1.3.6_dev',
      description='Terminal tor status monitor',
      license='GPL v3',
      author='Damian Johnson',
      author_email='atagar@torproject.org',
      url='http://www.atagar.com/arm/',
      packages=['arm', 'arm.interface', 'arm.util', 'arm.TorCtl'],
      package_dir={'arm': 'src'},
     )

