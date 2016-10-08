Change Log
==========

The following is a log of all user-facing changes to Nyx, both released and
unreleased. For a monthly report on work being done see my `development log
<http://blog.atagar.com/>`_.

* :ref:`versioning`
* :ref:`unreleased`
* `Version 1.x <change_log_legacy.html>`_

.. _versioning:

Versioning
----------

As of the 2.x release Nyx uses `semantic versioning <http://semver.org/>`_,
which means that **versions consist of three numbers** (such as '**1.2.4**').
These are used to convey the kind of backward compatibility a release has...

 * The first value is the **major version**. This changes infrequently, and
   indicates that backward incompatible changes have been made (such as the
   removal of deprecated functions).

 * The second value is the **minor version**. This is the most common kind of
   release, and denotes that the improvements are backward compatible.

 * The third value is the **patch version**. When a Nyx release has a major
   issue another release is made which fixes just that problem. These do not
   contain substantial improvements or new features. This value is sometimes
   left off to indicate all releases with a given major/minor version.

Prior to version 2.x nyx did not follow any particular versioning scheme.

.. _unreleased:

Unreleased
----------

The following are only available within Nyx's `git repository
<download.html>`_.

From a user perspective little has changed, but this release is nothing less
than a complete rewrite of our codebase. This adds long overdue **support for
python 3.x**, test coverage, and migrate from TorCtl to `Stem
<https://stem.torproject.org/>`_.

Python 2.5 is no longer supported, but hopefully by now nobody will miss it. ;)

 * **Startup**

  * Startup is several seconds faster when ran for the first time

 * **Graph**

  * Graph prepopulation no longer requires shifting to 15 minute intervals

 * **Connections**

  * Connections are now shown despite DisableDebuggerAttachment
  * Support for showing IPv6 connections

 * **Logging**

  * Order of magnitude faster log deduplication

 * **Curses**

  * Interface continues to update while awaiting user input

