.. _contributing:

Contributing
============

Datasette is an open source project. We welcome contributions!

This document describes how to contribute to Datasette core. You can also contribute to the wider Datasette ecosystem by creating new :ref:`plugins`.

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
    # Create a virtual environment in venv/
    python3 -mvenv venv
    # Install Datasette and its testing dependencies
    pip install -e .[test]

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
