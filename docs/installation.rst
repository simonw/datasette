.. _installation:

==============
 Installation
==============

.. note::
    If you just want to try Datasette out you don't need to install anything: see :ref:`getting_started_glitch`

There are two main options for installing Datasette. You can install it directly on to your machine, or you can install it using Docker.

If you want to start making contributions to the Datasette project by installing a copy that lets you directly modify the code, take a look at our guide to :ref:`devenvironment`.

.. contents::
   :local:
   :class: this-will-duplicate-information-and-it-is-still-useful-here

.. _installation_basic:

Basic installation
==================

.. _installation_datasette_desktop:

Datasette Desktop for Mac
-------------------------

`Datasette Desktop <https://datasette.io/desktop>`__ is a packaged Mac application which bundles Datasette together with Python and allows you to install and run Datasette directly on your laptop. This is the best option for local installation if you are not comfortable using the command line.

.. _installation_homebrew:

Using Homebrew
--------------

If you have a Mac and use `Homebrew <https://brew.sh/>`__, you can install Datasette by running this command in your terminal::

    brew install datasette

This should install the latest version. You can confirm by running::

    datasette --version

You can upgrade to the latest Homebrew packaged version using::

    brew upgrade datasette

Once you have installed Datasette you can install plugins using the following::

    datasette install datasette-vega

If the latest packaged release of Datasette has not yet been made available through Homebrew, you can upgrade your Homebrew installation in-place using::

    datasette install -U datasette

.. _installation_pip:

Using pip
---------

Datasette requires Python 3.7 or higher. The `Python.org Python For Beginners <https://www.python.org/about/gettingstarted/>`__ page has instructions for getting started.

You can install Datasette and its dependencies using ``pip``::

    pip install datasette

You can now run Datasette like so::

    datasette

.. _installation_advanced:

Advanced installation options
=============================

.. _installation_pipx:

Using pipx
----------

`pipx <https://pipxproject.github.io/pipx/>`__ is a tool for installing Python software with all of its dependencies in an isolated environment, to ensure that they will not conflict with any other installed Python software.

If you use `Homebrew <https://brew.sh/>`__ on macOS you can install pipx like this::

    brew install pipx
    pipx ensurepath

Without Homebrew you can install it like so::

    python3 -m pip install --user pipx
    python3 -m pipx ensurepath

The ``pipx ensurepath`` command configures your shell to ensure it can find commands that have been installed by pipx - generally by making sure ``~/.local/bin`` has been added to your ``PATH``.

Once pipx is installed you can use it to install Datasette like this::

    pipx install datasette

Then run ``datasette --version`` to confirm that it has been successfully installed.

Installing plugins using pipx
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You can install additional datasette plugins with ``pipx inject`` like so::

    $ pipx inject datasette datasette-json-html
    injected package datasette-json-html into venv datasette
    done! ✨ 🌟 ✨

    $ datasette plugins
    [
        {
            "name": "datasette-json-html",
            "static": false,
            "templates": false,
            "version": "0.6"
        }
    ]

Upgrading packages using pipx
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You can upgrade your pipx installation to the latest release of Datasette using ``pipx upgrade datasette``::

    $ pipx upgrade datasette    
    upgraded package datasette from 0.39 to 0.40 (location: /Users/simon/.local/pipx/venvs/datasette)

To upgrade a plugin within the pipx environment use ``pipx runpip datasette install -U name-of-plugin`` - like this::

    % datasette plugins
    [
        {
            "name": "datasette-vega",
            "static": true,
            "templates": false,
            "version": "0.6"
        }
    ]

    $ pipx runpip datasette install -U datasette-vega     
    Collecting datasette-vega
    Downloading datasette_vega-0.6.2-py3-none-any.whl (1.8 MB)
        |████████████████████████████████| 1.8 MB 2.0 MB/s 
    ...
    Installing collected packages: datasette-vega
    Attempting uninstall: datasette-vega
        Found existing installation: datasette-vega 0.6
        Uninstalling datasette-vega-0.6:
        Successfully uninstalled datasette-vega-0.6
    Successfully installed datasette-vega-0.6.2

    $ datasette plugins                              
    [
        {
            "name": "datasette-vega",
            "static": true,
            "templates": false,
            "version": "0.6.2"
        }
    ]

.. _installation_docker:

Using Docker
------------

A Docker image containing the latest release of Datasette is published to Docker
Hub here: https://hub.docker.com/r/datasetteproject/datasette/

If you have Docker installed (for example with `Docker for Mac
<https://www.docker.com/docker-mac>`_ on OS X) you can download and run this
image like so::

    docker run -p 8001:8001 -v `pwd`:/mnt \
        datasetteproject/datasette \
        datasette -p 8001 -h 0.0.0.0 /mnt/fixtures.db

This will start an instance of Datasette running on your machine's port 8001,
serving the ``fixtures.db`` file in your current directory.

Now visit http://127.0.0.1:8001/ to access Datasette.

(You can download a copy of ``fixtures.db`` from
https://latest.datasette.io/fixtures.db )

To upgrade to the most recent release of Datasette, run the following::

    docker pull datasetteproject/datasette

Loading SpatiaLite
~~~~~~~~~~~~~~~~~~

The ``datasetteproject/datasette`` image includes a recent version of the
:ref:`SpatiaLite extension <spatialite>` for SQLite. To load and enable that
module, use the following command::

    docker run -p 8001:8001 -v `pwd`:/mnt \
        datasetteproject/datasette \
        datasette -p 8001 -h 0.0.0.0 /mnt/fixtures.db \
        --load-extension=spatialite

You can confirm that SpatiaLite is successfully loaded by visiting
http://127.0.0.1:8001/-/versions

Installing plugins
~~~~~~~~~~~~~~~~~~

If you want to install plugins into your local Datasette Docker image you can do
so using the following recipe. This will install the plugins and then save a
brand new local image called ``datasette-with-plugins``::

    docker run datasetteproject/datasette \
        pip install datasette-vega

    docker commit $(docker ps -lq) datasette-with-plugins

You can now run the new custom image like so::

    docker run -p 8001:8001 -v `pwd`:/mnt \
        datasette-with-plugins \
        datasette -p 8001 -h 0.0.0.0 /mnt/fixtures.db

You can confirm that the plugins are installed by visiting
http://127.0.0.1:8001/-/plugins

Some plugins such as `datasette-ripgrep <https://datasette.io/plugins/datasette-ripgrep>`__ may need additional system packages. You can install these by running `apt-get install` inside the container::

    docker run datasette-057a0 bash -c '
        apt-get update && 
        apt-get install ripgrep &&
        pip install datasette-ripgrep'

    docker commit $(docker ps -lq) datasette-with-ripgrep

.. _installation_extensions:

A note about extensions
=======================

SQLite supports extensions, such as :ref:`spatialite` for geospatial operations.

These can be loaded using the ``--load-extension`` argument, like so::

    datasette --load-extension=/usr/local/lib/mod_spatialite.dylib

Some Python installations do not include support for SQLite extensions. If this is the case you will see the following error when you attempt to load an extension:

    Your Python installation does not have the ability to load SQLite extensions.

In some cases you may see the following error message instead::

    AttributeError: 'sqlite3.Connection' object has no attribute 'enable_load_extension'

On macOS the easiest fix for this is to install Datasette using Homebrew::

    brew install datasette

Use ``which datasette`` to confirm that ``datasette`` will run that version. The output should look something like this::

    /usr/local/opt/datasette/bin/datasette

If you get a different location here such as ``/Library/Frameworks/Python.framework/Versions/3.10/bin/datasette`` you can run the following command to cause ``datasette`` to execute the Homebrew version instead::

    alias datasette=$(echo $(brew --prefix datasette)/bin/datasette)

You can undo this operation using::

    unalias datasette

If you need to run SQLite with extension support for other Python code, you can do so by install Python itself using Homebrew::

    brew install python

Then executing Python using::

    /usr/local/opt/python@3/libexec/bin/python

A more convenient way to work with this version of Python may be to use it to create a virtual environment::

    /usr/local/opt/python@3/libexec/bin/python -m venv datasette-venv

Then activate it like this::

    source datasette-venv/bin/activate

Now running ``python`` and ``pip`` will work against a version of Python 3 that includes support for SQLite extensions::

    pip install datasette
    which datasette
    datasette --version
