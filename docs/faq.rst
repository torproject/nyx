Frequently Asked Questions
==========================

* **General Information**

 * :ref:`what_is_nyx`
 * :ref:`does_nyx_have_any_dependencies`
 * :ref:`what_python_versions_is_nyx_compatible_with`
 * :ref:`are_there_any_other_tor_uis`
 * :ref:`what_license_is_nyx_under`

* **Development**

 * :ref:`how_do_i_get_started`
 * :ref:`how_do_i_run_the_tests`
 * :ref:`how_do_i_build_the_site`
 * :ref:`what_is_the_copyright_for_patches`

General Information
===================

.. _what_is_nyx:

What is Nyx?
------------

Nyx is a command-line application for monitoring real time `Tor
<https://www.torproject.org/>`_ status information. This includes bandwidth
usage, logs, connections, configuration, `and more <screenshots.html>`_.

.. image:: /_static/section/screenshots/main.png
   :target: _static/section/screenshots/main_full.png

As a curses interface Nyx is particularly well suited for ssh connections, tty
terminals, and command-line aficionados.

.. _does_nyx_have_any_dependencies:

Does Nyx have any dependencies?
-------------------------------

**Yes**, Nyx requires `Stem 1.4.5 or later <https://stem.torproject.org/>`_.

.. _what_python_versions_is_nyx_compatible_with:

What Python versions is Nyx compatible with?
--------------------------------------------

Nyx works with **Python 2.6 and greater**, including the Python 3.x series.

.. _are_there_any_other_tor_uis:

Are there any other user interfaces for tor?
--------------------------------------------

.. image:: /_static/section/screenshots/vidalia.png
   :align: right

Yes, though sadly this isn't a space that gets much attention.

For years `Vidalia <https://en.wikipedia.org/wiki/Vidalia_%28software%29>`_ was
the default interface of Tor until it was replaced in 2013 by `Tor Browser
<https://www.torproject.org/projects/torbrowser.html.en>`_. Vidalia includes a
launcher, settings editor, map, and more. `TorK
<https://sourceforge.net/projects/tork/>`_ is similar, providing connection
information as well but never reached the same level of prominence. Both
interfaces are now unmaintained.

Smaller widgits include...

* `Syboa <https://gitorious.org/syboa/syboa>`_ - General interface
* `OnionLauncher <https://github.com/neelchauhan/OnionLauncher>`_ - Tor launcher
* `TorNova <https://github.com/neelchauhan/TorNova>`_ - Tor launcher
* `OnionView <https://github.com/skyguy/onionview>`_ - Circuit information
* `OnionCircuits <https://git-tails.immerda.ch/onioncircuits/>`_ - Circuit information
* `or-applet <https://github.com/Yawning/or-applet>`_ - Circuit information

If I missed any then please `let me know <https://www.atagar.com/contact/>`_!

.. _what_license_is_nyx_under:

What license is Nyx under?
--------------------------

Nyx is under the `GPLv3 <https://www.gnu.org/licenses/gpl>`_.

.. _where_can_i_get_help:

Development
===========

.. _how_do_i_get_started:

How do I get started?
---------------------

The best way of getting involved with any project is to jump right in! Our `bug
tracker <https://trac.torproject.org/projects/tor/wiki/doc/nyx/bugs>`_ lists
several development tasks. In particular look for the 'easy' keyword when
getting started. If you have any questions then I'm always more than happy to
help! I'm **atagar** on `oftc <http://www.oftc.net/>`_ and also available
`via email <https://www.atagar.com/contact/>`_.

To start hacking on Nyx please do the following and don't hesitate to let me
know if you get stuck or would like to discuss anything!

#. Clone our `git <http://git-scm.com/>`_ repository: **git clone https://git.torproject.org/nyx.git**
#. Install the development version of `Stem <https://stem.torproject.org/>`_: **git clone https://git.torproject.org/stem.git; cd stem; sudo python setup.py install**.
#. Get our test dependencies: **sudo pip install mock pep8 pyflakes**.
#. Find a `bug or feature <https://trac.torproject.org/projects/tor/wiki/doc/nyx/bugs>`_ that sounds interesting.
#. When you have something that you would like to contribute back do the following...

 * If you don't already have a publicly accessible Nyx repository then set one up. `GitHub <https://github.com/>`_ in particular is great for this.
 * File a `trac ticket <https://trac.torproject.org/projects/tor/newticket>`_, the only fields you'll need are...

  * Summary: short description of your change
  * Description: longer description and a link to your repository with either the git commits or branch that has your change
  * Type: 'defect' if this is a bug fix and 'enhancement' otherwise
  * Priority: rough guess at the priority of your change
  * Component: Core Tor / Nyx

 * I'll review the change and give suggestions. When we're both happy with it I'll push your change to the official repository.

.. _how_do_i_run_the_tests:

How do I run the tests?
-----------------------

Nyx has unit tests, including tests that exercise our curses functionality.
When you run the tests you may notice your console flicker as these are
exercised.

If you have them installed we run `pyflakes <https://launchpad.net/pyflakes>`_
to do static error checking and `pycodestyle
<http://pycodestyle.readthedocs.org/en/latest/>`_ for style checking as part of
our tests.

Tests are run with...

::

  % run_tests.py

.. _how_do_i_build_the_site:

How do I build the site?
------------------------

If you have `Sphinx <http://sphinx-doc.org/>`_ version 1.1 or later installed
then building our site is as easy as...

::

  ~$ cd nyx/docs
  ~/nyx/docs$ make html

When it's finished you can direct your browser to the *_build* directory with a
URI similar to...

::

  file:///home/atagar/nyx/docs/_build/html/index.html

.. _what_is_the_copyright_for_patches:

What is the copyright for patches?
----------------------------------

Nyx is under the GPLv3 which is a fine license, but poses a bit of a problem
for sharing code with our other projects (which are mostly BSD). To share code
without needing to hunt down prior contributors we need Tor to have the
copyright for the whole Nyx codebase. Presently the copyright of Nyx is
jointly held by its main author (`Damian <https://www.atagar.com/>`_) and the
`Tor Project <https://www.torproject.org/>`_.

If you submit a substantial patch I'll ask if you're fine with it being in the
public domain. This would mean that there are no legal restrictions for using
your contribution, and hence won't pose a problem if we reuse Nyx code in
other projects.

