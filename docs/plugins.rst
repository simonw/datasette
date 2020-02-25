.. _plugins:

Plugins
=======

Datasette's plugin system allows additional features to be implemented as Python
code (or front-end JavaScript) which can be wrapped up in a separate Python
package. The underlying mechanism uses `pluggy <https://pluggy.readthedocs.io/>`_.

See :ref:`ecosystem_plugins` for a list of existing plugins, or take a look at the
`datasette-plugin <https://github.com/topics/datasette-plugin>`__ topic on GitHub.

Things you can do with plugins include:

* Add visualizations to Datasette, for example
  `datasette-cluster-map <https://github.com/simonw/datasette-cluster-map>`__ and
  `datasette-vega <https://github.com/simonw/datasette-vega>`__.
* Make new custom SQL functions available for use within Datasette, for example
  `datasette-haversine <https://github.com/simonw/datasette-haversine>`__ and
  `datasette-jellyfish <https://github.com/simonw/datasette-jellyfish>`__.
* Add template functions that can be called within your Jinja custom templates,
  for example `datasette-render-markdown <https://github.com/simonw/datasette-render-markdown#markdown-in-templates>`__.
* Customize how database values are rendered in the Datasette interface, for example
  `datasette-render-binary <https://github.com/simonw/datasette-render-binary>`__ and
  `datasette-pretty-json <https://github.com/simonw/datasette-pretty-json>`__.
* Wrap the entire Datasette application in custom ASGI middleware to add new pages
  or implement authentication, for example
  `datasette-auth-github <https://github.com/simonw/datasette-auth-github>`__.

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

    datasette publish cloudrun mydb.db \
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

.. _plugins_installed:

Seeing what plugins are installed
---------------------------------

You can see a list of installed plugins by navigating to the ``/-/plugins`` page of your Datasette instance - for example: https://fivethirtyeight.datasettes.com/-/plugins

You can also use the ``datasette plugins`` command::

    $ datasette plugins
    [
        {
            "name": "datasette_json_html",
            "static": false,
            "templates": false,
            "version": "0.4.0"
        }
    ]

If you run ``datasette plugins --all`` it will include default plugins that ship as part of Datasette::

    $ datasette plugins --all
    [
        {
            "name": "datasette_json_html",
            "static": false,
            "templates": false,
            "version": "0.4.0"
        },
        {
            "name": "datasette.publish.heroku",
            "static": false,
            "templates": false,
            "version": null
        },
        {
            "name": "datasette.publish.now",
            "static": false,
            "templates": false,
            "version": null
        }
    ]

You can add the ``--plugins-dir=`` option to include any plugins found in that directory.

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

.. _plugins_configuration:

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

.. _plugins_configuration_secret:

Secret configuration values
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Any values embedded in ``metadata.json`` will be visible to anyone who views the ``/-/metadata`` page of your Datasette instance. Some plugins may need configuration that should stay secret - API keys for example. There are two ways in which you can store secret configuration values.

**As environment variables**. If your secret lives in an environment variable that is available to the Datasette process, you can indicate that the configuration value should be read from that environment variable like so::

    {
        "plugins": {
            "datasette-auth-github": {
                "client_secret": {
                    "$env": "GITHUB_CLIENT_SECRET"
                }
            }
        }
    }

**As values in separate files**. Your secrets can also live in files on disk. To specify a secret should be read from a file, provide the full file path like this::

    {
        "plugins": {
            "datasette-auth-github": {
                "client_secret": {
                    "$file": "/secrets/client-secret"
                }
            }
        }
    }

If you are publishing your data using the :ref:`datasette publish <cli_publish>` family of commands, you can use the ``--plugin-secret`` option to set these secrets at publish time. For example, using Heroku you might run the following command::

    $ datasette publish heroku my_database.db \
        --name my-heroku-app-demo \
        --install=datasette-auth-github \
        --plugin-secret datasette-auth-github client_id your_client_id \
        --plugin-secret datasette-auth-github client_secret your_client_secret

.. _plugins_plugin_config:

Writing plugins that accept configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

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

.. _plugin_hooks:

Plugin hooks
------------

When you implement a plugin hook you can accept any or all of the parameters that are documented as being passed to that hook. For example, you can implement a ``render_cell`` plugin hook like this even though the hook definition defines more parameters than just ``value`` and ``column``:

.. code-block:: python

    @hookimpl
    def render_cell(value, column):
        if column == "stars":
            return "*" * int(value)

The full list of available plugin hooks is as follows.

.. _plugin_hook_prepare_connection:

prepare_connection(conn, database, datasette)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``conn`` - sqlite3 connection object
    The connection that is being opened

``database`` - string
    The name of the database

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``

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

.. _plugin_hook_prepare_jinja2_environment:

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

.. _plugin_hook_extra_css_urls:

extra_css_urls(template, database, table, datasette)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``template`` - string
    The template that is being rendered, e.g. ``database.html``

``database`` - string or None
    The name of the database

``table`` - string or None
    The name of the table

``datasette`` - :ref:`internals_datasette`
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

.. _plugin_hook_extra_js_urls:

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
            '/-/static-plugins/your-plugin/app.js'
        ]

.. _plugin_hook_publish_subcommand:

publish_subcommand(publish)
~~~~~~~~~~~~~~~~~~~~~~~~~~~

``publish`` - Click publish command group
    The Click command group for the ``datasette publish`` subcommand

This hook allows you to create new providers for the ``datasette publish``
command. Datasette uses this hook internally to implement the default ``now``
and ``heroku`` subcommands, so you can read
`their source <https://github.com/simonw/datasette/tree/master/datasette/publish>`_
to see examples of this hook in action.

Let's say you want to build a plugin that adds a ``datasette publish my_hosting_provider --api_key=xxx mydatabase.db`` publish command. Your implementation would start like this:

.. code-block:: python

    from datasette import hookimpl
    from datasette.publish.common import add_common_publish_arguments_and_options
    import click


    @hookimpl
    def publish_subcommand(publish):
        @publish.command()
        @add_common_publish_arguments_and_options
        @click.option(
            "-k",
            "--api_key",
            help="API key for talking to my hosting provider",
        )
        def my_hosting_provider(
            files,
            metadata,
            extra_options,
            branch,
            template_dir,
            plugins_dir,
            static,
            install,
            version_note,
            title,
            license,
            license_url,
            source,
            source_url,
            api_key,
        ):
            # Your implementation goes here

.. _plugin_hook_render_cell:

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

``datasette`` - :ref:`internals_datasette`
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

.. _plugin_hook_extra_body_script:

extra_body_script(template, database, table, view_name, datasette)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Extra JavaScript to be added to a ``<script>`` block at the end of the ``<body>`` element on the page.

``template`` - string
    The template that is being rendered, e.g. ``database.html``

``database`` - string or None
    The name of the database, or ``None`` if the page does not correspond to a database (e.g. the root page)

``table`` - string or None
    The name of the table, or ``None`` if the page does not correct to a table

``view_name`` - string
    The name of the view being displayed. (`index`, `database`, `table`, and `row` are the most important ones.)

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``

The ``template``, ``database`` and ``table`` options can be used to return different code depending on which template is being rendered and which database or table are being processed.

The ``datasette`` instance is provided primarily so that you can consult any plugin configuration options that may have been set, using the ``datasette.plugin_config(plugin_name)`` method documented above.

The string that you return from this function will be treated as "safe" for inclusion in a ``<script>`` block directly in the page, so it is up to you to apply any necessary escaping.


.. _plugin_hook_extra_template_vars:

extra_template_vars(template, database, table, view_name, request, datasette)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Extra template variables that should be made available in the rendered template context.

``template`` - string
    The template that is being rendered, e.g. ``database.html``

``database`` - string or None
    The name of the database, or ``None`` if the page does not correspond to a database (e.g. the root page)

``table`` - string or None
    The name of the table, or ``None`` if the page does not correct to a table

``view_name`` - string
    The name of the view being displayed. (`index`, `database`, `table`, and `row` are the most important ones.)

``request`` - object
    The current HTTP request object. ``request.scope`` provides access to the ASGI scope.

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``

This hook can return one of three different types:

Dictionary
    If you return a dictionary its keys and values will be merged into the template context.

Function that returns a dictionary
    If you return a function it will be executed. If it returns a dictionary those values will will be merged into the template context.

Function that returns an awaitable function that returns a dictionary
    You can also return a function which returns an awaitable function which returns a dictionary.

Datasette runs Jinja2 in `async mode <https://jinja.palletsprojects.com/en/2.10.x/api/#async-support>`__, which means you can add awaitable functions to the template scope and they will be automatically awaited when they are rendered by the template.

Here's an example plugin that returns an authentication object from the ASGI scope:

.. code-block:: python

    @hookimpl
    def extra_template_vars(request):
        return {
            "auth": request.scope.get("auth")
        }

And here's an example which adds a ``sql_first(sql_query)`` function which executes a SQL statement and returns the first column of the first row of results:

.. code-block:: python

    @hookimpl
    def extra_template_vars(datasette, database):
        async def sql_first(sql, dbname=None):
            dbname = dbname or database or next(iter(datasette.databases.keys()))
            return (await datasette.execute(dbname, sql)).rows[0][0]

You can then use the new function in a template like so::

    SQLite version: {{ sql_first("select sqlite_version()") }}

.. _plugin_register_output_renderer:

register_output_renderer(datasette)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``

Allows the plugin to register a new output renderer, to output data in a custom format. The hook function should return a dictionary, or a list of dictionaries, which contain the file extension you want to handle and a callback function:

.. code-block:: python

    @hookimpl
    def register_output_renderer(datasette):
        return {
            'extension': 'test',
            'callback': render_test
        }

This will register `render_test` to be called when paths with the extension `.test` (for example `/database.test`, `/database/table.test`, or `/database/table/row.test`) are requested. When a request is received, the callback function is called with three positional arguments:

``args`` - dictionary
    The GET parameters of the request

``data`` - dictionary
    The data to be rendered

``view_name`` - string
    The name of the view where the renderer is being called. (`index`, `database`, `table`, and `row` are the most important ones.)

The callback function can return `None`, if it is unable to render the data, or a dictionary with the following keys:

``body`` - string or bytes, optional
    The response body, default empty

``content_type`` - string, optional
    The Content-Type header, default `text/plain`

``status_code`` - integer, optional
    The HTTP status code, default 200

A simple example of an output renderer callback function:

.. code-block:: python

    def render_test(args, data, view_name):
        return {
            'body': 'Hello World'
        }

.. _plugin_register_facet_classes:

register_facet_classes()
~~~~~~~~~~~~~~~~~~~~~~~~

Return a list of additional Facet subclasses to be registered.

Each Facet subclass implements a new type of facet operation. The class should look like this:

.. code-block:: python

    class SpecialFacet(Facet):
        # This key must be unique across all facet classes:
        type = "special"

        async def suggest(self):
            # Use self.sql and self.params to suggest some facets
            suggested_facets = []
            suggested_facets.append({
                "name": column, # Or other unique name
                # Construct the URL that will enable this facet:
                "toggle_url": self.ds.absolute_url(
                    self.request, path_with_added_args(
                        self.request, {"_facet": column}
                    )
                ),
            })
            return suggested_facets

        async def facet_results(self):
            # This should execute the facet operation and return results, again
            # using self.sql and self.params as the starting point
            facet_results = {}
            facets_timed_out = []
            # Do some calculations here...
            for column in columns_selected_for_facet:
                try:
                    facet_results_values = []
                    # More calculations...
                    facet_results_values.append({
                        "value": value,
                        "label": label,
                        "count": count,
                        "toggle_url": self.ds.absolute_url(self.request, toggle_path),
                        "selected": selected,
                    })
                    facet_results[column] = {
                        "name": column,
                        "results": facet_results_values,
                        "truncated": len(facet_rows_results) > facet_size,
                    }
                except QueryInterrupted:
                    facets_timed_out.append(column)

            return facet_results, facets_timed_out

See `datasette/facets.py <https://github.com/simonw/datasette/blob/master/datasette/facets.py>`__ for examples of how these classes can work.

The plugin hook can then be used to register the new facet class like this:

.. code-block:: python

    @hookimpl
    def register_facet_classes():
        return [SpecialFacet]


.. _plugin_asgi_wrapper:

asgi_wrapper(datasette)
~~~~~~~~~~~~~~~~~~~~~~~

Return an `ASGI <https://asgi.readthedocs.io/>`__ middleware wrapper function that will be applied to the Datasette ASGI application.

This is a very powerful hook. You can use it to manipulate the entire Datasette response, or even to configure new URL routes that will be handled by your own custom code.

You can write your ASGI code directly against the low-level specification, or you can use the middleware utilites provided by an ASGI framework such as `Starlette <https://www.starlette.io/middleware/>`__.

This example plugin adds a ``x-databases`` HTTP header listing the currently attached databases:

.. code-block:: python

    from datasette import hookimpl
    from functools import wraps


    @hookimpl
    def asgi_wrapper(datasette):
        def wrap_with_databases_header(app):
            @wraps(app)
            async def add_x_databases_header(scope, recieve, send):
                async def wrapped_send(event):
                    if event["type"] == "http.response.start":
                        original_headers = event.get("headers") or []
                        event = {
                            "type": event["type"],
                            "status": event["status"],
                            "headers": original_headers + [
                                [b"x-databases",
                                ", ".join(datasette.databases.keys()).encode("utf-8")]
                            ],
                        }
                    await send(event)
                await app(scope, recieve, wrapped_send)
            return add_x_databases_header
        return wrap_with_databases_header
