.. _plugins:

Plugins
=======

Datasette's plugin system is currently under active development. It allows
additional features to be implemented as Python code (or front-end JavaScript)
which can be wrapped up in a separate Python package. The underlying mechanism
uses `pluggy <https://pluggy.readthedocs.io/>`_.

You can follow the development of plugins in `issue #14 <https://github.com/simonw/datasette/issues/14>`_.

Using plugins
-------------

If a plugin has been packaged for distribution using setuptools you can use
the plugin by installing it alongside Datasette in the same virtual
environment or Docker container.

You can also define one-off per-project plugins by saving them as
``plugin_name.py`` functions in a ``plugins/`` folder and then passing that
folder to ``datasette serve``.

The ``datasette publish`` and ``datasette package`` commands both take an
optional ``--install`` argument. You can use this one or more times to tell
Datasette to ``pip install`` specific plugins as part of the process. You can
use the name of a package on PyPI or any of the other valid arguments to ``pip
install`` such as a URL to a ``.zip`` file::

    datasette publish now mydb.db \
        --install=datasette-plugin-demos \
        --install=https://url-to-my-package.zip

Writing plugins
---------------

The easiest way to write a plugin is to create a ``my_plugin.py`` file and
drop it into your ``plugins/`` directory. Here is an example plugin, which
adds a new custom SQL function called ``hello_world()`` which takes no
arguments and returns the string ``Hello world!``.

.. code-block:: python

    from datasette import hookimpl

    @hookimpl
    def prepare_connection(conn):
        conn.create_function('hello_world', 0, lambda: 'Hello world!')

If you save this in ``plugins/my_plugin.py`` you can then start Datasette like
this::

    datasette serve mydb.db --plugins-dir=plugins/

Now you can navigate to http://localhost:8001/mydb and run this SQL::

    select hello_world();

To see the output of your plugin.

Packaging a plugin
------------------

Plugins can be packaged using Python setuptools. You can see an example of a
packaged plugin at https://github.com/simonw/datasette-plugin-demos

The example consists of two files: a ``setup.py`` file that defines the plugin:

.. code-block:: python

    from setuptools import setup

    VERSION = '0.1'

    setup(
        name='datasette-plugin-demos',
        description='Examples of plugins for Datasette',
        author='Simon Willison',
        url='https://github.com/simonw/datasette-plugin-demos',
        license='Apache License, Version 2.0',
        version=VERSION,
        py_modules=['datasette_plugin_demos'],
        entry_points={
            'datasette': [
                'plugin_demos = datasette_plugin_demos'
            ]
        },
        install_requires=['datasette']
    )

And a Python module file, ``datasette_plugin_demos.py``, that implements the
plugin:

.. code-block:: python

    from datasette import hookimpl
    import random


    @hookimpl
    def prepare_jinja2_environment(env):
        env.filters['uppercase'] = lambda u: u.upper()


    @hookimpl
    def prepare_connection(conn):
        conn.create_function('random_integer', 2, random.randint)


Having built a plugin in this way you can turn it into an installable package
using the following command::

    python3 setup.py sdist

This will create a ``.tar.gz`` file in the ``dist/`` directory.

You can then install your new plugin into a Datasette virtual environment or
Docker container using ``pip``::

    pip install datasette-plugin-demos-0.1.tar.gz

To learn how to upload your plugin to `PyPI <https://pypi.org/>`_ for use by
other people, read the PyPA guide to `Packaging and distributing projects
<https://packaging.python.org/tutorials/distributing-packages/>`_.

Static assets
-------------

If your plugin has a ``static/`` directory, Datasette will automatically
configure itself to serve those static assets from the following path::

    /-/static-plugins/NAME_OF_PLUGIN_PACKAGE/yourfile.js

See `the datasette-plugin-demos repository <https://github.com/simonw/datasette-plugin-demos/tree/0ccf9e6189e923046047acd7878d1d19a2cccbb1>`_
for an example of how to create a package that includes a static folder.

Custom templates
----------------

If your plugin has a ``templates/`` directory, Datasette will attempt to load
templates from that directory before it uses its own default templates.

The priority order for template loading is:

* templates from the ``--template-dir`` argument, if specified
* templates from the ``templates/`` directory in any installed plugins
* default templates that ship with Datasette

See :ref:`customization` for more details on how to write custom templates,
including which filenames to use to customize which parts of the Datasette UI.

Plugin configuration
--------------------

Plugins can have their own configuration, embedded in a :ref:`metadata` file. Configuration options for plugins live within a ``"plugins"`` key in that file, which can be included at the root, database or table level.

Here is an example of some plugin configuration for a specific table::

    {
        "databases: {
            "sf-trees": {
                "tables": {
                    "Street_Tree_List": {
                        "plugins": {
                            "datasette-cluster-map": {
                                "latitude_column": "lat",
                                "longitude_column": "lng"
                            }
                        }
                    }
                }
            }
        }
    }

This tells the ``datasette-cluster-map`` column which latitude and longitude columns should be used for a table called ``Street_Tree_List`` inside a database file called ``sf-trees.db``.

When you are writing plugins, you can access plugin configuration like this using the ``datasette.plugin_config()`` method. If you know you need plugin configuration for a specific table, you can access it like this::

    plugin_config = datasette.plugin_config(
        "datasette-cluster-map", database="sf-trees", table="Street_Tree_List"
    )

This will return the ``{"latitude_column": "lat", "longitude_column": "lng"}`` in the above example.

If it cannot find the requested configuration at the table layer, it will fall back to the database layer and then the root layer. For example, a user may have set the plugin configuration option like so::

    {
        "databases: {
            "sf-trees": {
                "plugins": {
                    "datasette-cluster-map": {
                        "latitude_column": "xlat",
                        "longitude_column": "xlng"
                    }
                }
            }
        }
    }

In this case, the above code would return that configuration for ANY table within the ``sf-trees`` database.

The plugin configuration could also be set at the top level of ``metadata.json``::

    {
        "title": "This is the top-level title in metadata.json",
        "plugins": {
            "datasette-cluster-map": {
                "latitude_column": "xlat",
                "longitude_column": "xlng"
            }
        }
    }

Now that ``datasette-cluster-map`` plugin configuration will apply to every table in every database.

Plugin hooks
------------

When you implement a plugin hook you can accept any or all of the parameters that are documented as being passed to that hook. For example, you can implement a ``render_cell`` plugin hook like this even though the hook definition defines more parameters than just ``value`` and ``column``:

.. code-block:: python

    @hookimpl
    def render_cell(value, column):
        if column == "stars":
            return "*" * int(value)

The full list of available plugin hooks is as follows.

prepare_connection(conn)
~~~~~~~~~~~~~~~~~~~~~~~~

``conn`` - sqlite3 connection object
    The connection that is being opened

This hook is called when a new SQLite database connection is created. You can
use it to `register custom SQL functions <https://docs.python.org/2/library/sqlite3.html#sqlite3.Connection.create_function>`_,
aggregates and collations. For example:

.. code-block:: python

    from datasette import hookimpl
    import random

    @hookimpl
    def prepare_connection(conn):
        conn.create_function('random_integer', 2, random.randint)

This registers a SQL function called ``random_integer`` which takes two
arguments and can be called like this::

    select random_integer(1, 10);

prepare_jinja2_environment(env)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``env`` - jinja2 Environment
    The template environment that is being prepared

This hook is called with the Jinja2 environment that is used to evaluate
Datasette HTML templates. You can use it to do things like `register custom
template filters <http://jinja.pocoo.org/docs/2.10/api/#custom-filters>`_, for
example:

.. code-block:: python

    from datasette import hookimpl

    @hookimpl
    def prepare_jinja2_environment(env):
        env.filters['uppercase'] = lambda u: u.upper()

You can now use this filter in your custom templates like so::

    Table name: {{ table|uppercase }}

extra_css_urls(template, database, table, datasette)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``template`` - string
    The template that is being rendered, e.g. ``database.html``

``database`` - string or None
    The name of the database

``table`` - string or None
    The name of the table

``datasette`` - Datasette instance
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``

Return a list of extra CSS URLs that should be included on the page. These can
take advantage of the CSS class hooks described in :ref:`customization`.

This can be a list of URLs:

.. code-block:: python

    from datasette import hookimpl

    @hookimpl
    def extra_css_urls():
        return [
            'https://stackpath.bootstrapcdn.com/bootstrap/4.1.0/css/bootstrap.min.css'
        ]

Or a list of dictionaries defining both a URL and an
`SRI hash <https://www.srihash.org/>`_:

.. code-block:: python

    from datasette import hookimpl

    @hookimpl
    def extra_css_urls():
        return [{
            'url': 'https://stackpath.bootstrapcdn.com/bootstrap/4.1.0/css/bootstrap.min.css',
            'sri': 'sha384-9gVQ4dYFwwWSjIDZnLEWnxCjeSWFphJiwGPXr1jddIhOegiu1FwO5qRGvFXOdJZ4',
        }]

extra_js_urls(template, database, table, datasette)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Same arguments as ``extra_css_urls``.

This works in the same way as ``extra_css_urls()`` but for JavaScript. You can
return either a list of URLs or a list of dictionaries:

.. code-block:: python

    from datasette import hookimpl

    @hookimpl
    def extra_js_urls():
        return [{
            'url': 'https://code.jquery.com/jquery-3.3.1.slim.min.js',
            'sri': 'sha384-q8i/X+965DzO0rT7abK41JStQIAqVgRVzpbzo5smXKp4YfRvH+8abtTE1Pi6jizo',
        }]

You can also return URLs to files from your plugin's ``static/`` directory, if
you have one:

.. code-block:: python

    from datasette import hookimpl

    @hookimpl
    def extra_js_urls():
        return [
            '/-/static-plugins/your_plugin/app.js'
        ]

publish_subcommand(publish)
~~~~~~~~~~~~~~~~~~~~~~~~~~~

``publish`` - Click publish command group
    The Click command group for the ``datasette publish`` subcommand

This hook allows you to create new providers for the ``datasette publish``
command. Datasette uses this hook internally to implement the default ``now``
and ``heroku`` subcommands, so you can read
`their source <https://github.com/simonw/datasette/tree/master/datasette/publish>`_
to see examples of this hook in action.

render_cell(value, column, table, database, datasette)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Lets you customize the display of values within table cells in the HTML table view.

``value`` - string, integer or None
    The value that was loaded from the database

``column`` - string
    The name of the column being rendered

``table`` - string or None
    The name of the table - or ``None`` if this is a custom SQL query

``database`` - string
    The name of the database

``datasette`` - Datasette instance
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``

If your hook returns ``None``, it will be ignored. Use this to indicate that your hook is not able to custom render this particular value.

If the hook returns a string, that string will be rendered in the table cell.

If you want to return HTML markup you can do so by returning a ``jinja2.Markup`` object.

Datasette will loop through all available ``render_cell`` hooks and display the value returned by the first one that does not return ``None``.

Here is an example of a custom ``render_cell()`` plugin which looks for values that are a JSON string matching the following format::

    {"href": "https://www.example.com/", "label": "Name"}

If the value matches that pattern, the plugin returns an HTML link element:

.. code-block:: python

    from datasette import hookimpl
    import jinja2
    import json


    @hookimpl
    def render_cell(value):
        # Render {"href": "...", "label": "..."} as link
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        if not stripped.startswith("{") and stripped.endswith("}"):
            return None
        try:
            data = json.loads(value)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        if set(data.keys()) != {"href", "label"}:
            return None
        href = data["href"]
        if not (
            href.startswith("/") or href.startswith("http://")
            or href.startswith("https://")
        ):
            return None
        return jinja2.Markup('<a href="{href}">{label}</a>'.format(
            href=jinja2.escape(data["href"]),
            label=jinja2.escape(data["label"] or "") or "&nbsp;"
        ))

extra_body_script(template, database, table, datasette)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``template`` - string
    The template that is being rendered, e.g. ``database.html``

``database`` - string or None
    The name of the database, or ``None`` if the page does not correspond to a database (e.g. the root page)

``table`` - string or None
    The name of the table, or ``None`` if the page does not correct to a table

``datasette`` - Datasette instance
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``

Extra JavaScript to be added to a ``<script>`` block at the end of the ``<body>`` element on the page.

The ``template``, ``database`` and ``table`` options can be used to return different code depending on which template is being rendered and which database or table are being processed.

The ``datasette`` instance is provided primarily so that you can consult any plugin configuration options that may have been set, using the ``datasette.plugin_config(plugin_name)`` method documented above.

The string that you return from this function will be treated as "safe" for inclusion in a ``<script>`` block directly in the page, so it is up to you to apply any necessary escaping.
