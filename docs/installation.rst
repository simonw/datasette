.. _installation:

Installation
============

.. note::
    If you just want to try Datasette out you don't need to install anything: see :ref:`glitch`

There are two main options for installing Datasette. You can install it directly
on to your machine, or you can install it using Docker.

.. contents::

.. _installation_pip:

Install using pip
-----------------

To run Datasette without Docker you will need Python 3.6 or higher.

You can install Datasette and its dependencies using ``pip``::

    pip install datasette

The last version to support Python 3.5 was 0.30.2 - you can install that version like so::

    pip install datasette==0.30.2

If you want to install Datasette in its own virtual environment, use this::

    python -mvenv datasette-venv
    source datasette-venv/bin/activate
    pip install datasette

You can now run Datasette like so::

    datasette fixtures.db

If you want to start making contributions to the Datasette project by installing a copy that lets you directly modify the code, take a look at our guide to :ref:`devenvironment`.

.. _installation_pipx:

Install using pipx
------------------

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

Datasette plugins need to be installed into the same environment as Datasette itself. You can do this using ``pipx inject datasette name-of-plugin`` - and then confirm that the plugin has been installed using the ``datasette plugins`` command::

    $ datasette plugins
    []

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

Loading Spatialite
~~~~~~~~~~~~~~~~~~

The ``datasetteproject/datasette`` image includes a recent version of the
:ref:`SpatiaLite extension <spatialite>` for SQLite. To load and enable that
module, use the following command::

    docker run -p 8001:8001 -v `pwd`:/mnt \
        datasetteproject/datasette \
        datasette -p 8001 -h 0.0.0.0 /mnt/fixtures.db \
        --load-extension=/usr/local/lib/mod_spatialite.so

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
