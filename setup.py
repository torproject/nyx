#!/usr/bin/env python
# Copyright 2015, Damian Johnson and The Tor Project
# See LICENSE for licensing information

import gzip
import os
import shutil
import stat
import sysconfig

import nyx

from distutils import log
from distutils.core import setup
from distutils.command.install import install

DEFAULT_MAN_PAGE_PATH = '/usr/share/man/man1/nyx.1.gz'
DEFAULT_SAMPLE_PATH = '/usr/share/doc/nyx/nyxrc.sample'


class NyxInstaller(install):
  """
  Nyx installer. This adds the following additional options...

    --man-page [path]
    --sample-path [path]

  If the man page path ends in '.gz' it will be compressed. Empty paths such
  as...

    % python setup.py install --man-page ''

  ... will cause that resource to be omitted.
  """

  user_options = install.user_options + [
    ('man-page=', None, 'man page location (default: %s)' % DEFAULT_MAN_PAGE_PATH),
    ('sample-path=', None, 'example nyxrc location (default: %s)' % DEFAULT_SAMPLE_PATH),
  ]

  def initialize_options(self):
    install.initialize_options(self)
    self.man_page = DEFAULT_MAN_PAGE_PATH
    self.sample_path = DEFAULT_SAMPLE_PATH

  def run(self):
    install.run(self)

    self.install_bin_script('run_nyx', os.path.join(self.install_scripts, 'nyx'))

    if self.man_page:
      self.install_man_page(os.path.join('nyx', 'resources', 'nyx.1'), self.man_page)

    if self.sample_path:
      self.install_sample('nyxrc.sample', self.sample_path)

  def install_bin_script(self, source, dest):
    # Install our bin script. We do this ourselves rather than with the setup()
    # method's scripts argument because we want to call the script 'nyx' rather
    # than 'run_nyx'.
    #
    # If using setuptools this would be better replaced with its entry_points.

    self.mkpath(os.path.dirname(dest))

    with open(source, 'rb') as source_file:
      with open(dest, 'wb') as dest_file:
        orig_shebang = source_file.readline()

        python_cmd = 'python%s%s' % (sysconfig.get_config_var('VERSION'), sysconfig.get_config_var('EXE'))
        new_shebang = '#!%s\n' % os.path.join(sysconfig.get_config_var('BINDIR'), python_cmd)

        log.info("adjusting bin script's shebang line '%s' -> '%s'" % (orig_shebang.strip(), new_shebang.strip()))
        dest_file.write(new_shebang)
        dest_file.write(source_file.read())

    mode = ((os.stat(dest)[stat.ST_MODE]) | 0o555) & 0o7777
    os.chmod(dest, mode)
    log.info("installed bin script to '%s'" % dest)

  def install_man_page(self, source, dest):
    if not os.path.exists(source):
      raise OSError(None, "man page doesn't exist at '%s'" % source)

    self.mkpath(os.path.dirname(dest))
    open_func = gzip.open if dest.endswith('.gz') else open

    with open(source, 'rb') as source_file:
      with open_func(dest, 'wb') as dest_file:
        dest_file.write(source_file.read())
        log.info("installed man page to '%s'" % dest)

  def install_sample(self, source, dest):
    if not os.path.exists(source):
      raise OSError(None, "nyxrc sample doesn't exist at '%s'" % source)

    self.mkpath(os.path.dirname(dest))
    shutil.copyfile(source, dest)
    log.info("installed sample nyxrc to '%s'" % dest)


# installation requires us to be in our setup.py's directory

setup_dir = os.path.dirname(os.path.join(os.getcwd(), __file__))
os.chdir(setup_dir)

setup(
  name = 'nyx',
  version = nyx.__version__,
  description = 'Terminal status monitor for Tor <https://www.torproject.org/>',
  license = nyx.__license__,
  author = nyx.__author__,
  author_email = nyx.__contact__,
  url = nyx.__url__,
  packages = ['nyx', 'nyx.connections', 'nyx.menu', 'nyx.util'],
  keywords = 'tor onion controller',
  install_requires = ['stem>=1.4.1'],
  package_data = {'nyx': ['config/*', 'resources/*']},
  cmdclass = {'install': NyxInstaller},
)
