.. _spatialite:

============
 SpatiaLite
============

The `SpatiaLite module <https://www.gaia-gis.it/fossil/libspatialite/index>`_ for SQLite adds features for handling geographic and spatial data. For an example of what you can do with it, see the tutorial `Building a location to time zone API with SpatiaLite, OpenStreetMap and Datasette <https://simonwillison.net/2017/Dec/12/location-time-zone-api/>`_.

To use it with Datasette, you need to install the ``mod_spatialite`` dynamic library. This can then be loaded into Datasette using the ``--load-extension`` command-line option.

Installation
============

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

Building SpatiaLite from source
-------------------------------

The packaged versions of SpatiaLite usually provide SpatiaLite 4.3.0a. For an example of how to build the most recent unstable version, 4.4.0-RC0 (which includes the powerful `VirtualKNN module <https://www.gaia-gis.it/fossil/libspatialite/wiki?name=KNN>`_), take a look at the `Datasette Dockerfile <https://github.com/simonw/datasette/blob/master/Dockerfile>`_.

Importing shapefiles into SpatiaLite
====================================

The `shapefile format <https://en.wikipedia.org/wiki/Shapefile>`_ is a common format for distributing geospatial data. You can use the ``spatialite`` command-line tool to create a new database table from a shapefile.

Try it now with the North America shapefile available from the University of North Carolina `Global River Database <http://gaia.geosci.unc.edu/rivers/>`_ project. Download the file and unzip it (this will create files called ``narivs.dbf``, ``narivs.prj``, ``narivs.shp`` and ``narivs.shx`` in the current directory), then run the following::

    $ spatialite rivers-database.db
    SpatiaLite version ..: 4.3.0a	Supported Extensions:
    ...
    spatialite> .loadshp narivs rivers CP1252 23032
    ========
    Loading shapefile at 'narivs' into SQLite table 'rivers'
    ...
    Inserted 467973 rows into 'rivers' from SHAPEFILE

This will load the data from the ``narivs`` shapefile into a new database table called ``rivers``.

Exit out of ``spatialite`` (using ``Ctrl+D``) and run Datasette against your new database like this::

    datasette rivers-database.db \
        --load-extension=/usr/local/lib/mod_spatialite.dylib

If you browse to ``http://localhost:8001/rivers-database/rivers`` you will see the new table... but the ``Geometry`` column will contain unreadable binary data (SpatiaLite uses `a custom format based on WKB <https://www.gaia-gis.it/gaia-sins/BLOB-Geometry.html>`_).

The easiest way to turn this into semi-readable data is to use the SpatiaLite ``AsGeoJSON`` function. Try the following using the SQL query interface at ``http://localhost:8001/rivers-database``::

    select *, AsGeoJSON(Geometry) from rivers limit 10;

This will give you back an additional column of GeoJSON. You can copy and paste GeoJSON from this column into the debugging tool at `geojson.io <https://geojson.io/>`_ to visualize it on a map.

To see a more interesting example, try ordering the records with the longest geometry first. Since there are 467,000 rows in the table you will first need to increase the SQL time limit imposed by Datasette::

    datasette rivers-database.db \
        --load-extension=/usr/local/lib/mod_spatialite.dylib \
        --config sql_time_limit_ms:10000

Now try the following query::

    select *, AsGeoJSON(Geometry) from rivers
    order by length(Geometry) desc limit 10;
