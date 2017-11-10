import os
import shutil
import subprocess
import sys
import unittest

import nyx
import stem.util.system


class TestInstallation(unittest.TestCase):
  def test_installing_stem(self):
    base_directory = os.path.sep.join(__file__.split(os.path.sep)[:-2])

    if not os.path.exists(os.path.sep.join([base_directory, 'setup.py'])):
      self.skipTest('(only for git checkout)')

    original_cwd = os.getcwd()
    site_packages = '/tmp/nyx_test/lib/python%i.%i/site-packages/' % sys.version_info[:2]

    try:
      os.chdir(base_directory)
      os.makedirs(site_packages)
      stem.util.system.call(sys.executable + ' setup.py install --prefix /tmp/nyx_test', env = {'PYTHONPATH': site_packages})
      stem.util.system.call(sys.executable + ' setup.py clean --all')  # tidy up the build directory

      if not os.path.exists(site_packages):
        self.fail('We should have a site-packages located at: %s' % site_packages)

      self.assertEqual(nyx.__version__, stem.util.system.call([sys.executable, '-c', "import sys;sys.path.insert(0, '%s');import nyx;print(nyx.__version__)" % site_packages])[0])

      process_path = [site_packages] + sys.path
      process = subprocess.Popen(['/tmp/nyx_test/bin/nyx', '--help'], stdout = subprocess.PIPE, env = {'PYTHONPATH': ':'.join(process_path)})
      stdout = process.communicate()[0]

      self.assertTrue(stdout.startswith(b'Usage nyx [OPTION]'))
    finally:
      if os.path.exists('/tmp/nyx_test'):
        shutil.rmtree('/tmp/nyx_test')

      os.chdir(original_cwd)
