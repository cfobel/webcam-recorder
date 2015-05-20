try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup

import sys
sys.path.insert(0, '.')
import version


setup(name='webcam-recorder',
      version=version.getVersion(),
      author='Christian Fobel',
      author_email='christian@fobel.net',
      url='https://github.com/cfobel/webcam-recorder',
      license='LGPL-3.0',
      packages=['webcam_recorder'])
