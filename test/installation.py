import glob
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

    try:
      os.chdir(base_directory)
      stem.util.system.call(sys.executable + ' setup.py install --prefix /tmp/nyx_test --man-page /tmp/nyx_test/nyx.1.gz --sample-path /tmp/nyx_test/nyxrc.sample')
      stem.util.system.call(sys.executable + ' setup.py clean --all')  # tidy up the build directory
      site_packages_paths = glob.glob('/tmp/nyx_test/lib*/*/site-packages')

      if len(site_packages_paths) != 1:
        self.fail('We should only have a single site-packages directory, but instead had: %s' % site_packages_paths)

      self.assertEqual(nyx.__version__, stem.util.system.call([sys.executable, '-c', "import sys;sys.path.insert(0, '%s');import nyx;print(nyx.__version__)" % site_packages_paths[0]])[0])

      process_path = [site_packages_paths[0]] + sys.path
      process = subprocess.Popen(['/tmp/nyx_test/bin/nyx', '--help'], stdout = subprocess.PIPE, env = {'PYTHONPATH': ':'.join(process_path)})
      stdout = process.communicate()[0]

      self.assertTrue(stdout.startswith(b'Usage nyx [OPTION]'))
    finally:
      shutil.rmtree('/tmp/nyx_test')
      os.chdir(original_cwd)
