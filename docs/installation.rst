.. _installation:

Installation
============

There are two main options for installing Datasette. You can install it directly
on to your machine, or you can install it using Docker.

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


Install using pip
-----------------

To run Datasette without Docker you will need Python 3.5 or higher.

You can install Datasette and its dependencies using ``pip``::

    pip install datasette

If you want to install Datasette in its own virtual environment, use this::

    python -mvenv datasette-venv
    source datasette-venv/bin/activate
    pip install datasette

You can now run Datasette like so::

    datasette fixtures.db

If you want to start making contributions to the Datasette project by installing a copy that lets you directly modify the code, take a look at our guide to :ref:`devenvironment`.
