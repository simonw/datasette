.. _spatialite:

SpatiaLite
==========

The `SpatiaLite module <https://www.gaia-gis.it/fossil/libspatialite/index>`_ for SQLite adds features for handling geographic and spatial data. For an example of what you can do with it, see the tutorial `Building a location to time zone API with SpatiaLite, OpenStreetMap and Datasette <https://simonwillison.net/2017/Dec/12/location-time-zone-api/>`_.

To use it with Datasette, you need to install the ``mod_spatialite`` dynamic library. This can then be loaded into Datasette using the ``--load-extension`` command-line option.

Installing SpatiaLite on OS X
-----------------------------

The easiest way to install SpatiaLite on OS X is to use `Homebrew <https://brew.sh/>`_.

::

    brew update
    brew install spatialite-tools

This will install the ``spatialite`` command-line tool and the ``mod_spatialite`` dynamic library.

You can now run Datasette like so::

    datasette --load-extension=/usr/local/lib/mod_spatialite.dylib

Installing SpatiaLite on Linux
------------------------------

SpatiaLite is packaged for most Linux distributions.

::

    apt install spatialite-bin libsqlite3-mod-spatialite

Depending on your distribution, you should be able to run Datasette something like this::

    datasette --load-extension=/usr/lib/x86_64-linux-gnu/mod_spatialite.so

If you are unsure of the location of the module, try running ``locate mod_spatialite`` and see what comes back.
