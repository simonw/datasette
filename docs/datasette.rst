.. _datasette:

Datasette class
===============

Many of Datasette's :ref:`plugin_hooks` pass a ``datasette`` object to the plugin as an argument.

This object is an instance of the ``Datasette`` class. That class currently has a large number of methods on it, but it should not be considered stable (at least until Datasette 1.0) with the exception of the methods that are documented on this page.

.. _datasette_add_database:

.add_database(name, db)
-----------------------

``name`` - string
    The unique name to use for this database. Also used in the URL.

``db`` - datasette.database.Database instance
    The database to be attached.

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

.. _datasette_remove_database:

.remove_database(name)
----------------------

``name`` - string
    The name of the database to be removed.

This removes a database that has been previously added. ``name=`` is the unique name of that database, also used in the URL for it.

.. _datasette_plugin_config:

.plugin_config(plugin_name, database=None, table=None)
------------------------------------------------------

``plugin_name`` - string
    The name of the plugin to look up configuration for. Usually this is something similar to ``datasette-cluster-map``.

``database`` - None or string
    The database the user is interacting with.

``table`` - None or string
    The table the user is interacting with.

This method lets you read plugin configuration values that were set in ``metadata.json``. See :ref:`plugins_plugin_config` for full details of how this method should be used.

.. _datasette_render_template:

.render_template(template, context=None, request=None)
------------------------------------------------------

``template`` - string
    The template file to be rendered, e.g. ``my_plugin.html``. Datasette will search for this file first in the ``--template-dir=`` location, if it was specified - then in the plugin's bundled templates and finally in Datasette's set of default templates.

``conttext`` - None or a Python dictionary
    The context variables to pass to the template.

``request`` - request object or None
    If you pass a Datasette request object here it will be made available to the template.

Renders a `Jinja template <https://jinja.palletsprojects.com/en/2.11.x/>`__ using Datasette's preconfigured instance of Jinja and returns the resulting string. The template will have access to Datasette's default template functions and any functions that have been made available by other plugins.
