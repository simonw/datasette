.. _contributing:

Contributing
============

Datasette is an open source project. We welcome contributions!

This document describes how to contribute to Datasette core. You can also contribute to the wider Datasette ecosystem by creating new :ref:`plugins`.

General guidelines
------------------

* **master should always be releasable**. Incomplete features should live in branches. This ensures that any small bug fixes can be quickly released.
* **The ideal commit** should bundle together the implementation, unit tests and associated documentation updates. The commit message should link to an associated issue.

.. _devenvironment:

Setting up a development environment
------------------------------------

If you have Python 3.5 or higher installed on your computer (on OS X the easiest way to do this `is using homebrew <https://docs.python-guide.org/starting/install3/osx/>`__) you can install an editable copy of Datasette using the following steps.

If you want to use GitHub to publish your changes, first `create a fork of datasette <https://github.com/simonw/datasette/fork>`__ under your own GitHub account.

Now clone that repository somewhere on your computer::

    git clone git@github.com:YOURNAME/datasette

If you just want to get started without creating your own fork, you can do this instead::

    git clone git@github.com:simonw/datasette

The next step is to create a virtual environment for your project and use it to install Datasette's dependencies::

    cd datasette
    # Create a virtual environment in ./venv
    python3 -m venv ./venv
    # Now activate the virtual environment, so pip can install into it
    source venv/bin/activate
    # Install Datasette and its testing dependencies
    python3 -m pip install -e .[test]

That last line does most of the work: ``pip install -e`` means "install this package in a way that allows me to edit the source code in place". The ``.[test]`` option means "use the setup.py in this directory and install the optional testing dependencies as well".

Once you have done this, you can run the Datasette unit tests from inside your ``datasette/`` directory using `pytest <https://docs.pytest.org/en/latest/>`__ like so::

    pytest

To run Datasette itself, just type ``datasette``.

You're going to need at least one SQLite database. An easy way to get started is to use the fixtures database that Datasette uses for its own tests.

You can create a copy of that database by running this command::

    python tests/fixtures.py fixtures.db

Now you can run Datasette against the new fixtures database like so::

    datasette fixtures.db

This will start a server at ``http://127.0.0.1:8001/``.

Any changes you make in the ``datasette/templates`` or ``datasette/static`` folder will be picked up immediately (though you may need to do a force-refresh in your browser to see changes to CSS or JavaScript).

If you want to change Datasette's Python code you can use the ``--reload`` option to cause Datasette to automatically reload any time the underlying code changes::

    datasette --reload fixtures.db

You can also use the ``fixtures.py`` script to recreate the testing version of ``metadata.json`` used by the unit tests. To do that::

    python tests/fixtures.py fixtures.db fixtures-metadata.json

(You may need to delete ``fixtures.db`` before running this command.)

Then run Datasette like this::

    datasette fixtures.db -m fixtures-metadata.json

.. _contributing_documentation:

Editing and building the documentation
--------------------------------------

Datasette's documentation lives in the ``docs/`` directory and is deployed automatically using `Read The Docs <https://readthedocs.org/>`__.

The documentation is written using reStructuredText. You may find this article on `The subset of reStructuredText worth committing to memory <https://simonwillison.net/2018/Aug/25/restructuredtext/>`__ useful.

You can build it locally by installing ``sphinx`` and ``sphinx_rtd_theme`` in your Datasette development environment and then running ``make html`` directly in the ``docs/`` directory::

    # You may first need to activate your virtual environment:
    source venv/bin/activate

    # Install the dependencies needed to build the docs
    pip install sphinx sphinx_rtd_theme

    # Now build the docs
    cd docs/
    make html

This will create the HTML version of the documentation in ``docs/_build/html``. You can open it in your browser like so::

    open _build/html/index.html

Any time you make changes to a ``.rst`` file you can re-run ``make html`` to update the built documents, then refresh them in your browser.

For added productivity, you can run Sphinx in auto-build mode. This will run a local webserver serving the docs that automatically rebuilds them and refreshes the page any time you hit save in your editor.

To enable auto-build mode, first install `sphinx-autobuild <https://pypi.org/project/sphinx-autobuild/>`__::

    pip install sphinx-autobuild

Now start the server by running::

    make livehtml

.. _contributing_release:

Release process
---------------

Datasette releases are performed using tags. When a new version tag is pushed to GitHub, a `Travis CI task <https://github.com/simonw/datasette/blob/master/.travis.yml>`__ will perform the following:

* Run the unit tests against all supported Python versions. If the tests pass...
* Set up https://v0-25-1.datasette.io/ (but with the new tag) to point to a live demo of this release
* Build a Docker image of the release and push a tag to https://hub.docker.com/r/datasetteproject/datasette
* Re-point the "latest" tag on Docker Hub to the new image
* Build a wheel bundle of the underlying Python source code
* Push that new wheel up to PyPI: https://pypi.org/project/datasette/

To deploy new releases you will need to have push access to the main Datasette GitHub repository.

Datasette follows `Semantic Versioning <https://semver.org/>`__::

    major.minor.patch

We increment ``major`` for backwards-incompatible releases. Datasette is currently pre-1.0 so the major version is always ``0``.

We increment ``minor`` for new features.

We increment ``patch`` for bugfix releass.

To release a new version, first create a commit that updates :ref:`the changelog <changelog>` with highlights of the new version. An example `commit can be seen here <https://github.com/simonw/datasette/commit/28872a1fa789f314b0342f4e6182f1c78d6e2bca>`__::

    # Update changelog
    git commit -m "Release 0.25.2" -a
    git push

For non-bugfix releases you may want to update the news section of ``README.md`` as part of the same commit.

Wait long enough for Travis to build and deploy the demo version of that commit (otherwise the tag deployment may fail to alias to it properly). Then run the following::

    git tag 0.25.2
    git push --tags

Once the release is out, you can manually update https://github.com/simonw/datasette/releases
