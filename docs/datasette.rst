.. _datasette:

Datasette class
===============

Many of Datasette's :ref:`plugin_hooks` pass a ``datasette`` object to the plugin as an argument.

This object is an instance of the ``Datasette`` class. That class currently has a large number of methods on it, but it should not be considered stable (at least until Datasette 1.0) with the exception of the methods that are documented on this page.

.add_database(name, db)
-----------------------

The ``datasette.add_database(name, db)`` method lets you add a new database to the current Datasette instance. This database will then be served at URL path that matches the ``name`` parameter, e.g. ``/mynewdb/``.

The ``db`` parameter should be an instance of the ``datasette.database.Database`` class. For example:

.. code-block:: python

    from datasette.database import Database

    datasette.add_database("my-new-database", Database(
        datasette,
        path="path/to/my-new-database.db",
        is_mutable=True
    ))

This will add a mutable database from the provided file path.

The ``Database()`` constructor takes four arguments: the first is the ``datasette`` instance you are attaching to, the second is a ``path=``, then ``is_mutable`` and ``is_memory`` are both optional arguments.

Use ``is_mutable`` if it is possible that updates will be made to that database - otherwise Datasette will open it in immutable mode and any changes could cause undesired behavior.

Use ``is_memory`` if the connection is to an in-memory SQLite database.

.remove_database(name)
----------------------

This removes a database that has been previously added. ``name=`` is the unique name of that database, also used in the URL for it.

.plugin_config(plugin_name, database=None, table=None)
------------------------------------------------------

This method lets you read plugin configuration values that were set in ``metadata.json``. See :ref:`plugins_plugin_config` for full details.
