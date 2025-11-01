.. _plugin_hooks:

Plugin hooks
============

Datasette :ref:`plugins <plugins>` use *plugin hooks* to customize Datasette's behavior. These hooks are powered by the `pluggy <https://pluggy.readthedocs.io/>`__ plugin system.

Each plugin can implement one or more hooks using the ``@hookimpl`` decorator against a function named that matches one of the hooks documented on this page.

When you implement a plugin hook you can accept any or all of the parameters that are documented as being passed to that hook.

For example, you can implement the ``render_cell`` plugin hook like this even though the full documented hook signature is ``render_cell(row, value, column, table, database, datasette)``:

.. code-block:: python

    @hookimpl
    def render_cell(value, column):
        if column == "stars":
            return "*" * int(value)

.. contents:: List of plugin hooks
   :local:
   :class: this-will-duplicate-information-and-it-is-still-useful-here

.. _plugin_hook_prepare_connection:

prepare_connection(conn, database, datasette)
---------------------------------------------

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
        conn.create_function(
            "random_integer", 2, random.randint
        )

This registers a SQL function called ``random_integer`` which takes two
arguments and can be called like this::

    select random_integer(1, 10);

``prepare_connection()`` hooks are not called for Datasette's :ref:`internal database <internals_internal>`.

Examples: `datasette-jellyfish <https://datasette.io/plugins/datasette-jellyfish>`__, `datasette-jq <https://datasette.io/plugins/datasette-jq>`__, `datasette-haversine <https://datasette.io/plugins/datasette-haversine>`__, `datasette-rure <https://datasette.io/plugins/datasette-rure>`__

.. _plugin_hook_prepare_jinja2_environment:

prepare_jinja2_environment(env, datasette)
------------------------------------------

``env`` - jinja2 Environment
    The template environment that is being prepared

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``

This hook is called with the Jinja2 environment that is used to evaluate
Datasette HTML templates. You can use it to do things like `register custom
template filters <http://jinja.pocoo.org/docs/2.10/api/#custom-filters>`_, for
example:

.. code-block:: python

    from datasette import hookimpl


    @hookimpl
    def prepare_jinja2_environment(env):
        env.filters["uppercase"] = lambda u: u.upper()

You can now use this filter in your custom templates like so::

    Table name: {{ table|uppercase }}

This function can return an awaitable function if it needs to run any async code.

Examples: `datasette-edit-templates <https://datasette.io/plugins/datasette-edit-templates>`_

.. _plugin_page_extras:

Page extras
-----------

These plugin hooks can be used to affect the way HTML pages for different Datasette interfaces are rendered.

.. _plugin_hook_extra_template_vars:

extra_template_vars(template, database, table, columns, view_name, request, datasette)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Extra template variables that should be made available in the rendered template context.

``template`` - string
    The template that is being rendered, e.g. ``database.html``

``database`` - string or None
    The name of the database, or ``None`` if the page does not correspond to a database (e.g. the root page)

``table`` - string or None
    The name of the table, or ``None`` if the page does not correct to a table

``columns`` - list of strings or None
    The names of the database columns that will be displayed on this page. ``None`` if the page does not contain a table.

``view_name`` - string
    The name of the view being displayed. (``index``, ``database``, ``table``, and ``row`` are the most important ones.)

``request`` - :ref:`internals_request` or None
    The current HTTP request. This can be ``None`` if the request object is not available.

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

Here's an example plugin that adds a ``"user_agent"`` variable to the template context containing the current request's User-Agent header:

.. code-block:: python

    @hookimpl
    def extra_template_vars(request):
        return {"user_agent": request.headers.get("user-agent")}

This example returns an awaitable function which adds a list of ``hidden_table_names`` to the context:

.. code-block:: python

    @hookimpl
    def extra_template_vars(datasette, database):
        async def hidden_table_names():
            if database:
                db = datasette.databases[database]
                return {
                    "hidden_table_names": await db.hidden_table_names()
                }
            else:
                return {}

        return hidden_table_names

And here's an example which adds a ``sql_first(sql_query)`` function which executes a SQL statement and returns the first column of the first row of results:

.. code-block:: python

    @hookimpl
    def extra_template_vars(datasette, database):
        async def sql_first(sql, dbname=None):
            dbname = (
                dbname
                or database
                or next(iter(datasette.databases.keys()))
            )
            result = await datasette.execute(dbname, sql)
            return result.rows[0][0]

        return {"sql_first": sql_first}

You can then use the new function in a template like so::

    SQLite version: {{ sql_first("select sqlite_version()") }}

Examples: `datasette-search-all <https://datasette.io/plugins/datasette-search-all>`_, `datasette-template-sql <https://datasette.io/plugins/datasette-template-sql>`_

.. _plugin_hook_extra_css_urls:

extra_css_urls(template, database, table, columns, view_name, request, datasette)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This takes the same arguments as :ref:`extra_template_vars(...) <plugin_hook_extra_template_vars>`

Return a list of extra CSS URLs that should be included on the page. These can
take advantage of the CSS class hooks described in :ref:`customization`.

This can be a list of URLs:

.. code-block:: python

    from datasette import hookimpl


    @hookimpl
    def extra_css_urls():
        return [
            "https://stackpath.bootstrapcdn.com/bootstrap/4.1.0/css/bootstrap.min.css"
        ]

Or a list of dictionaries defining both a URL and an
`SRI hash <https://www.srihash.org/>`_:

.. code-block:: python

    @hookimpl
    def extra_css_urls():
        return [
            {
                "url": "https://stackpath.bootstrapcdn.com/bootstrap/4.1.0/css/bootstrap.min.css",
                "sri": "sha384-9gVQ4dYFwwWSjIDZnLEWnxCjeSWFphJiwGPXr1jddIhOegiu1FwO5qRGvFXOdJZ4",
            }
        ]

This function can also return an awaitable function, useful if it needs to run any async code:

.. code-block:: python

    @hookimpl
    def extra_css_urls(datasette):
        async def inner():
            db = datasette.get_database()
            results = await db.execute(
                "select url from css_files"
            )
            return [r[0] for r in results]

        return inner

Examples: `datasette-cluster-map <https://datasette.io/plugins/datasette-cluster-map>`_, `datasette-vega <https://datasette.io/plugins/datasette-vega>`_

.. _plugin_hook_extra_js_urls:

extra_js_urls(template, database, table, columns, view_name, request, datasette)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

This takes the same arguments as :ref:`extra_template_vars(...) <plugin_hook_extra_template_vars>`

This works in the same way as ``extra_css_urls()`` but for JavaScript. You can
return a list of URLs, a list of dictionaries or an awaitable function that returns those things:

.. code-block:: python

    from datasette import hookimpl


    @hookimpl
    def extra_js_urls():
        return [
            {
                "url": "https://code.jquery.com/jquery-3.3.1.slim.min.js",
                "sri": "sha384-q8i/X+965DzO0rT7abK41JStQIAqVgRVzpbzo5smXKp4YfRvH+8abtTE1Pi6jizo",
            }
        ]

You can also return URLs to files from your plugin's ``static/`` directory, if
you have one:

.. code-block:: python

    @hookimpl
    def extra_js_urls():
        return ["/-/static-plugins/your-plugin/app.js"]

Note that ``your-plugin`` here should be the hyphenated plugin name - the name that is displayed in the list on the ``/-/plugins`` debug page.

If your code uses `JavaScript modules <https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Modules>`__ you should include the ``"module": True`` key. See :ref:`configuration_reference_css_js` for more details.

.. code-block:: python

    @hookimpl
    def extra_js_urls():
        return [
            {
                "url": "/-/static-plugins/your-plugin/app.js",
                "module": True,
            }
        ]

Examples: `datasette-cluster-map <https://datasette.io/plugins/datasette-cluster-map>`_, `datasette-vega <https://datasette.io/plugins/datasette-vega>`_

.. _plugin_hook_extra_body_script:

extra_body_script(template, database, table, columns, view_name, request, datasette)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Extra JavaScript to be added to a ``<script>`` block at the end of the ``<body>`` element on the page.

This takes the same arguments as :ref:`extra_template_vars(...) <plugin_hook_extra_template_vars>`

The ``template``, ``database``, ``table`` and ``view_name`` options can be used to return different code depending on which template is being rendered and which database or table are being processed.

The ``datasette`` instance is provided primarily so that you can consult any plugin configuration options that may have been set, using the ``datasette.plugin_config(plugin_name)`` method documented above.

This function can return a string containing JavaScript, or a dictionary as described below, or a function or awaitable function that returns a string or dictionary.

Use a dictionary if you want to specify that the code should be placed in a ``<script type="module">...</script>`` element:

.. code-block:: python

    @hookimpl
    def extra_body_script():
        return {
            "module": True,
            "script": "console.log('Your JavaScript goes here...')",
        }

This will add the following to the end of your page:

.. code-block:: html

    <script type="module">console.log('Your JavaScript goes here...')</script>

Example: `datasette-cluster-map <https://datasette.io/plugins/datasette-cluster-map>`_

.. _plugin_hook_publish_subcommand:

publish_subcommand(publish)
---------------------------

``publish`` - Click publish command group
    The Click command group for the ``datasette publish`` subcommand

This hook allows you to create new providers for the ``datasette publish``
command. Datasette uses this hook internally to implement the default ``cloudrun``
and ``heroku`` subcommands, so you can read
`their source <https://github.com/simonw/datasette/tree/main/datasette/publish>`_
to see examples of this hook in action.

Let's say you want to build a plugin that adds a ``datasette publish my_hosting_provider --api_key=xxx mydatabase.db`` publish command. Your implementation would start like this:

.. code-block:: python

    from datasette import hookimpl
    from datasette.publish.common import (
        add_common_publish_arguments_and_options,
    )
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
            plugin_secret,
            version_note,
            secret,
            title,
            license,
            license_url,
            source,
            source_url,
            about,
            about_url,
            api_key,
        ): ...

Examples: `datasette-publish-fly <https://datasette.io/plugins/datasette-publish-fly>`_, `datasette-publish-vercel <https://datasette.io/plugins/datasette-publish-vercel>`_

.. _plugin_hook_render_cell:

render_cell(row, value, column, table, database, datasette, request)
--------------------------------------------------------------------

Lets you customize the display of values within table cells in the HTML table view.

``row`` - ``sqlite.Row``
    The SQLite row object that the value being rendered is part of

``value`` - string, integer, float, bytes or None
    The value that was loaded from the database

``column`` - string
    The name of the column being rendered

``table`` - string or None
    The name of the table - or ``None`` if this is a custom SQL query

``database`` - string
    The name of the database

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``, or to execute SQL queries.

``request`` - :ref:`internals_request`
    The current request object

If your hook returns ``None``, it will be ignored. Use this to indicate that your hook is not able to custom render this particular value.

If the hook returns a string, that string will be rendered in the table cell.

If you want to return HTML markup you can do so by returning a ``jinja2.Markup`` object.

You can also return an awaitable function which returns a value.

Datasette will loop through all available ``render_cell`` hooks and display the value returned by the first one that does not return ``None``.

Here is an example of a custom ``render_cell()`` plugin which looks for values that are a JSON string matching the following format::

    {"href": "https://www.example.com/", "label": "Name"}

If the value matches that pattern, the plugin returns an HTML link element:

.. code-block:: python

    from datasette import hookimpl
    import markupsafe
    import json


    @hookimpl
    def render_cell(value):
        # Render {"href": "...", "label": "..."} as link
        if not isinstance(value, str):
            return None
        stripped = value.strip()
        if not (
            stripped.startswith("{") and stripped.endswith("}")
        ):
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
            href.startswith("/")
            or href.startswith("http://")
            or href.startswith("https://")
        ):
            return None
        return markupsafe.Markup(
            '<a href="{href}">{label}</a>'.format(
                href=markupsafe.escape(data["href"]),
                label=markupsafe.escape(data["label"] or "")
                or "&nbsp;",
            )
        )

Examples: `datasette-render-binary <https://datasette.io/plugins/datasette-render-binary>`_, `datasette-render-markdown <https://datasette.io/plugins/datasette-render-markdown>`__, `datasette-json-html <https://datasette.io/plugins/datasette-json-html>`__

.. _plugin_register_output_renderer:

register_output_renderer(datasette)
-----------------------------------

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``

Registers a new output renderer, to output data in a custom format. The hook function should return a dictionary, or a list of dictionaries, of the following shape:

.. code-block:: python

    @hookimpl
    def register_output_renderer(datasette):
        return {
            "extension": "test",
            "render": render_demo,
            "can_render": can_render_demo,  # Optional
        }

This will register ``render_demo`` to be called when paths with the extension ``.test`` (for example ``/database.test``, ``/database/table.test``, or ``/database/table/row.test``) are requested.

``render_demo`` is a Python function. It can be a regular function or an ``async def render_demo()`` awaitable function, depending on if it needs to make any asynchronous calls.

``can_render_demo`` is a Python function (or ``async def`` function) which accepts the same arguments as ``render_demo`` but just returns ``True`` or ``False``. It lets Datasette know if the current SQL query can be represented by the plugin - and hence influence if a link to this output format is displayed in the user interface. If you omit the ``"can_render"`` key from the dictionary every query will be treated as being supported by the plugin.

When a request is received, the ``"render"`` callback function is called with zero or more of the following arguments. Datasette will inspect your callback function and pass arguments that match its function signature.

``datasette`` - :ref:`internals_datasette`
    For accessing plugin configuration and executing queries.

``columns`` - list of strings
    The names of the columns returned by this query.

``rows`` - list of ``sqlite3.Row`` objects
    The rows returned by the query.

``sql`` - string
    The SQL query that was executed.

``query_name`` - string or None
    If this was the execution of a :ref:`canned query <canned_queries>`, the name of that query.

``database`` - string
    The name of the database.

``table`` - string or None
    The table or view, if one is being rendered.

``request`` - :ref:`internals_request`
    The current HTTP request.

``error`` - string or None
    If an error occurred this string will contain the error message.

``truncated`` - bool or None
    If the query response was truncated - for example a SQL query returning more than 1,000 results where pagination is not available - this will be ``True``.

``view_name`` - string
    The name of the current view being called. ``index``, ``database``, ``table``, and ``row`` are the most important ones.

The callback function can return ``None``, if it is unable to render the data, or a :ref:`internals_response` that will be returned to the caller.

It can also return a dictionary with the following keys. This format is **deprecated** as-of Datasette 0.49 and will be removed by Datasette 1.0.

``body`` - string or bytes, optional
    The response body, default empty

``content_type`` - string, optional
    The Content-Type header, default ``text/plain``

``status_code`` - integer, optional
    The HTTP status code, default 200

``headers`` - dictionary, optional
    Extra HTTP headers to be returned in the response.

An example of an output renderer callback function:

.. code-block:: python

    def render_demo():
        return Response.text("Hello World")

Here is a more complex example:

.. code-block:: python

    async def render_demo(datasette, columns, rows):
        db = datasette.get_database()
        result = await db.execute("select sqlite_version()")
        first_row = " | ".join(columns)
        lines = [first_row]
        lines.append("=" * len(first_row))
        for row in rows:
            lines.append(" | ".join(row))
        return Response(
            "\n".join(lines),
            content_type="text/plain; charset=utf-8",
            headers={"x-sqlite-version": result.first()[0]},
        )

And here is an example ``can_render`` function which returns ``True`` only if the query results contain the columns ``atom_id``, ``atom_title`` and ``atom_updated``:

.. code-block:: python

    def can_render_demo(columns):
        return {
            "atom_id",
            "atom_title",
            "atom_updated",
        }.issubset(columns)

Examples: `datasette-atom <https://datasette.io/plugins/datasette-atom>`_, `datasette-ics <https://datasette.io/plugins/datasette-ics>`_, `datasette-geojson <https://datasette.io/plugins/datasette-geojson>`__, `datasette-copyable <https://datasette.io/plugins/datasette-copyable>`__

.. _plugin_register_routes:

register_routes(datasette)
--------------------------

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``

Register additional view functions to execute for specified URL routes.

Return a list of ``(regex, view_function)`` pairs, something like this:

.. code-block:: python

    from datasette import hookimpl, Response
    import html


    async def hello_from(request):
        name = request.url_vars["name"]
        return Response.html(
            "Hello from {}".format(html.escape(name))
        )


    @hookimpl
    def register_routes():
        return [(r"^/hello-from/(?P<name>.*)$", hello_from)]

The view functions can take a number of different optional arguments. The corresponding argument will be passed to your function depending on its named parameters - a form of dependency injection.

The optional view function arguments are as follows:

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``, or to execute SQL queries.

``request`` - :ref:`internals_request`
    The current HTTP request.

``scope`` - dictionary
    The incoming ASGI scope dictionary.

``send`` - function
    The ASGI send function.

``receive`` - function
    The ASGI receive function.

The view function can be a regular function or an ``async def`` function, depending on if it needs to use any ``await`` APIs.

The function can either return a :ref:`internals_response` or it can return nothing and instead respond directly to the request using the ASGI ``send`` function (for advanced uses only).

It can also raise the ``datasette.NotFound`` exception to return a 404 not found error, or the ``datasette.Forbidden`` exception for a 403 forbidden.

See :ref:`writing_plugins_designing_urls` for tips on designing the URL routes used by your plugin.

Examples: `datasette-auth-github <https://datasette.io/plugins/datasette-auth-github>`__, `datasette-psutil <https://datasette.io/plugins/datasette-psutil>`__

.. _plugin_hook_register_commands:

register_commands(cli)
----------------------

``cli`` - the root Datasette `Click command group <https://click.palletsprojects.com/en/latest/commands/#callback-invocation>`__
    Use this to register additional CLI commands

Register additional CLI commands that can be run using ``datsette yourcommand ...``. This provides a mechanism by which plugins can add new CLI commands to Datasette.

This example registers a new ``datasette verify file1.db file2.db`` command that checks if the provided file paths are valid SQLite databases:

.. code-block:: python

    from datasette import hookimpl
    import click
    import sqlite3


    @hookimpl
    def register_commands(cli):
        @cli.command()
        @click.argument(
            "files", type=click.Path(exists=True), nargs=-1
        )
        def verify(files):
            "Verify that files can be opened by Datasette"
            for file in files:
                conn = sqlite3.connect(str(file))
                try:
                    conn.execute("select * from sqlite_master")
                except sqlite3.DatabaseError:
                    raise click.ClickException(
                        "Invalid database: {}".format(file)
                    )

The new command can then be executed like so::

    datasette verify fixtures.db

Help text (from the docstring for the function plus any defined Click arguments or options) will become available using::

    datasette verify --help

Plugins can register multiple commands by making multiple calls to the ``@cli.command()`` decorator. Consult the `Click documentation <https://click.palletsprojects.com/>`__ for full details on how to build a CLI command, including how to define arguments and options.

Note that ``register_commands()`` plugins cannot used with the :ref:`--plugins-dir mechanism <writing_plugins_one_off>` - they need to be installed into the same virtual environment as Datasette using ``pip install``. Provided it has a ``pyproject.toml`` file (see :ref:`writing_plugins_packaging`) you can run ``pip install`` directly against the directory in which you are developing your plugin like so::

    pip install -e path/to/my/datasette-plugin

Examples: `datasette-auth-passwords <https://datasette.io/plugins/datasette-auth-passwords>`__, `datasette-verify <https://datasette.io/plugins/datasette-verify>`__

.. _plugin_register_facet_classes:

register_facet_classes()
------------------------

Return a list of additional Facet subclasses to be registered.

.. warning::
    The design of this plugin hook is unstable and may change. See `issue 830 <https://github.com/simonw/datasette/issues/830>`__.

Each Facet subclass implements a new type of facet operation. The class should look like this:

.. code-block:: python

    class SpecialFacet(Facet):
        # This key must be unique across all facet classes:
        type = "special"

        async def suggest(self):
            # Use self.sql and self.params to suggest some facets
            suggested_facets = []
            suggested_facets.append(
                {
                    "name": column,  # Or other unique name
                    # Construct the URL that will enable this facet:
                    "toggle_url": self.ds.absolute_url(
                        self.request,
                        path_with_added_args(
                            self.request, {"_facet": column}
                        ),
                    ),
                }
            )
            return suggested_facets

        async def facet_results(self):
            # This should execute the facet operation and return results, again
            # using self.sql and self.params as the starting point
            facet_results = []
            facets_timed_out = []
            facet_size = self.get_facet_size()
            # Do some calculations here...
            for column in columns_selected_for_facet:
                try:
                    facet_results_values = []
                    # More calculations...
                    facet_results_values.append(
                        {
                            "value": value,
                            "label": label,
                            "count": count,
                            "toggle_url": self.ds.absolute_url(
                                self.request, toggle_path
                            ),
                            "selected": selected,
                        }
                    )
                    facet_results.append(
                        {
                            "name": column,
                            "results": facet_results_values,
                            "truncated": len(facet_rows_results)
                            > facet_size,
                        }
                    )
                except QueryInterrupted:
                    facets_timed_out.append(column)

            return facet_results, facets_timed_out

See `datasette/facets.py <https://github.com/simonw/datasette/blob/main/datasette/facets.py>`__ for examples of how these classes can work.

The plugin hook can then be used to register the new facet class like this:

.. code-block:: python

    @hookimpl
    def register_facet_classes():
        return [SpecialFacet]

.. _plugin_register_permissions:

register_permissions(datasette)
-------------------------------

.. note::
    This hook is deprecated. Use :ref:`plugin_register_actions` instead, which provides a more flexible resource-based permission system.

If your plugin needs to register additional permissions unique to that plugin - ``upload-csvs`` for example - you can return a list of those permissions from this hook.

.. code-block:: python

    from datasette import hookimpl, Permission


    @hookimpl
    def register_permissions(datasette):
        return [
            Permission(
                name="upload-csvs",
                abbr=None,
                description="Upload CSV files",
                takes_database=True,
                takes_resource=False,
                default=False,
            )
        ]

The fields of the ``Permission`` class are as follows:

``name`` - string
    The name of the permission, e.g. ``upload-csvs``. This should be unique across all plugins that the user might have installed, so choose carefully.

``abbr`` - string or None
    An abbreviation of the permission, e.g. ``uc``. This is optional - you can set it to ``None`` if you do not want to pick an abbreviation. Since this needs to be unique across all installed plugins it's best not to specify an abbreviation at all. If an abbreviation is provided it will be used when creating restricted signed API tokens.

``description`` - string or None
    A human-readable description of what the permission lets you do. Should make sense as the second part of a sentence that starts "A user with this permission can ...".

``takes_database`` - boolean
    ``True`` if this permission can be granted on a per-database basis, ``False`` if it is only valid at the overall Datasette instance level.

``takes_resource`` - boolean
    ``True`` if this permission can be granted on a per-resource basis. A resource is a database table, SQL view or :ref:`canned query <canned_queries>`.

``default`` - boolean
    The default value for this permission if it is not explicitly granted to a user. ``True`` means the permission is granted by default, ``False`` means it is not.

    This should only be ``True`` if you want anonymous users to be able to take this action.

.. _plugin_register_actions:

register_actions(datasette)
---------------------------

If your plugin needs to register actions that can be checked with Datasette's new resource-based permission system, return a list of those actions from this hook.

Actions define what operations can be performed on resources (like viewing a table, executing SQL, or custom plugin actions).

.. code-block:: python

    from datasette import hookimpl
    from datasette.permissions import Action, Resource


    class DocumentCollectionResource(Resource):
        """A collection of documents."""

        name = "document-collection"
        parent_name = None

        def __init__(self, collection: str):
            super().__init__(parent=collection, child=None)

        @classmethod
        def resources_sql(cls) -> str:
            return """
                SELECT collection_name AS parent, NULL AS child
                FROM document_collections
            """


    class DocumentResource(Resource):
        """A document in a collection."""

        name = "document"
        parent_name = "document-collection"

        def __init__(self, collection: str, document: str):
            super().__init__(parent=collection, child=document)

        @classmethod
        def resources_sql(cls) -> str:
            return """
                SELECT collection_name AS parent, document_id AS child
                FROM documents
            """


    @hookimpl
    def register_actions(datasette):
        return [
            Action(
                name="list-documents",
                abbr="ld",
                description="List documents in a collection",
                resource_class=DocumentCollectionResource,
            ),
            Action(
                name="view-document",
                abbr="vdoc",
                description="View document",
                resource_class=DocumentResource,
            ),
            Action(
                name="edit-document",
                abbr="edoc",
                description="Edit document",
                resource_class=DocumentResource,
            ),
        ]

The fields of the ``Action`` dataclass are as follows:

``name`` - string
    The name of the action, e.g. ``view-document``. This should be unique across all plugins.

``abbr`` - string or None
    An abbreviation of the action, e.g. ``vdoc``. This is optional. Since this needs to be unique across all installed plugins it's best to choose carefully or use ``None``.

``description`` - string or None
    A human-readable description of what the action allows you to do.

``resource_class`` - type[Resource] or None
    The Resource subclass that defines what kind of resource this action applies to. Omit this (or set to ``None``) for global actions that apply only at the instance level with no associated resources (like ``debug-menu`` or ``permissions-debug``). Your Resource subclass must:

    - Define a ``name`` class attribute (e.g., ``"document"``)
    - Define a ``parent_class`` class attribute (``None`` for top-level resources like databases, or the parent ``Resource`` subclass for child resources)
    - Implement a ``resources_sql()`` classmethod that returns SQL returning all resources as ``(parent, child)`` columns
    - Have an ``__init__`` method that accepts appropriate parameters and calls ``super().__init__(parent=..., child=...)``

The ``resources_sql()`` method
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``resources_sql()`` classmethod returns a SQL query that lists all resources of that type that exist in the system.

This query is used by Datasette to efficiently check permissions across multiple resources at once. When a user requests a list of resources (like tables, documents, or other entities), Datasette uses this SQL to:

1. Get all resources of this type from your data catalog
2. Combine it with permission rules from the ``permission_resources_sql`` hook
3. Use SQL joins and filtering to determine which resources the actor can access
4. Return only the permitted resources

The SQL query **must** return exactly two columns:

- ``parent`` - The parent identifier (e.g., database name, collection name), or ``NULL`` for top-level resources
- ``child`` - The child identifier (e.g., table name, document ID), or ``NULL`` for parent-only resources

For example, if you're building a document management plugin with collections and documents stored in a ``documents`` table, your ``resources_sql()`` might look like:

.. code-block:: python

    @classmethod
    def resources_sql(cls) -> str:
        return """
            SELECT collection_name AS parent, document_id AS child
            FROM documents
        """

This tells Datasette "here's how to find all documents in the system - look in the documents table and get the collection name and document ID for each one."

The permission system then uses this query along with rules from plugins to determine which documents each user can access, all efficiently in SQL rather than loading everything into Python.

.. _plugin_asgi_wrapper:

asgi_wrapper(datasette)
-----------------------

Return an `ASGI <https://asgi.readthedocs.io/>`__ middleware wrapper function that will be applied to the Datasette ASGI application.

This is a very powerful hook. You can use it to manipulate the entire Datasette response, or even to configure new URL routes that will be handled by your own custom code.

You can write your ASGI code directly against the low-level specification, or you can use the middleware utilities provided by an ASGI framework such as `Starlette <https://www.starlette.io/middleware/>`__.

This example plugin adds a ``x-databases`` HTTP header listing the currently attached databases:

.. code-block:: python

    from datasette import hookimpl
    from functools import wraps


    @hookimpl
    def asgi_wrapper(datasette):
        def wrap_with_databases_header(app):
            @wraps(app)
            async def add_x_databases_header(
                scope, receive, send
            ):
                async def wrapped_send(event):
                    if event["type"] == "http.response.start":
                        original_headers = (
                            event.get("headers") or []
                        )
                        event = {
                            "type": event["type"],
                            "status": event["status"],
                            "headers": original_headers
                            + [
                                [
                                    b"x-databases",
                                    ", ".join(
                                        datasette.databases.keys()
                                    ).encode("utf-8"),
                                ]
                            ],
                        }
                    await send(event)

                await app(scope, receive, wrapped_send)

            return add_x_databases_header

        return wrap_with_databases_header

Examples: `datasette-cors <https://datasette.io/plugins/datasette-cors>`__, `datasette-pyinstrument <https://datasette.io/plugins/datasette-pyinstrument>`__, `datasette-total-page-time <https://datasette.io/plugins/datasette-total-page-time>`__

.. _plugin_hook_startup:

startup(datasette)
------------------

This hook fires when the Datasette application server first starts up.

Here is an example that validates required plugin configuration. The server will fail to start and show an error if the validation check fails:

.. code-block:: python

    @hookimpl
    def startup(datasette):
        config = datasette.plugin_config("my-plugin") or {}
        assert (
            "required-setting" in config
        ), "my-plugin requires setting required-setting"

You can also return an async function, which will be awaited on startup. Use this option if you need to execute any database queries, for example this function which creates the ``my_table`` database table if it does not yet exist:

.. code-block:: python

    @hookimpl
    def startup(datasette):
        async def inner():
            db = datasette.get_database()
            if "my_table" not in await db.table_names():
                await db.execute_write(
                    """
                    create table my_table (mycol text)
                """
                )

        return inner

Potential use-cases:

* Run some initialization code for the plugin
* Create database tables that a plugin needs on startup
* Validate the configuration for a plugin on startup, and raise an error if it is invalid

.. note::

   If you are writing :ref:`unit tests <testing_plugins>` for a plugin that uses this hook and doesn't exercise Datasette by sending
   any simulated requests through it you will need to explicitly call ``await ds.invoke_startup()`` in your tests. An example:

   .. code-block:: python

        @pytest.mark.asyncio
        async def test_my_plugin():
            ds = Datasette()
            await ds.invoke_startup()
            # Rest of test goes here

Examples: `datasette-saved-queries <https://datasette.io/plugins/datasette-saved-queries>`__, `datasette-init <https://datasette.io/plugins/datasette-init>`__

.. _plugin_hook_canned_queries:

canned_queries(datasette, database, actor)
------------------------------------------

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``, or to execute SQL queries.

``database`` - string
    The name of the database.

``actor`` - dictionary or None
    The currently authenticated :ref:`actor <authentication_actor>`.

Use this hook to return a dictionary of additional :ref:`canned query <canned_queries>` definitions for the specified database. The return value should be the same shape as the JSON described in the :ref:`canned query <canned_queries>` documentation.

.. code-block:: python

    from datasette import hookimpl


    @hookimpl
    def canned_queries(datasette, database):
        if database == "mydb":
            return {
                "my_query": {
                    "sql": "select * from my_table where id > :min_id"
                }
            }

The hook can alternatively return an awaitable function that returns a list. Here's an example that returns queries that have been stored in the ``saved_queries`` database table, if one exists:

.. code-block:: python

    from datasette import hookimpl


    @hookimpl
    def canned_queries(datasette, database):
        async def inner():
            db = datasette.get_database(database)
            if await db.table_exists("saved_queries"):
                results = await db.execute(
                    "select name, sql from saved_queries"
                )
                return {
                    result["name"]: {"sql": result["sql"]}
                    for result in results
                }

        return inner

The actor parameter can be used to include the currently authenticated actor in your decision. Here's an example that returns saved queries that were saved by that actor:

.. code-block:: python

    from datasette import hookimpl


    @hookimpl
    def canned_queries(datasette, database, actor):
        async def inner():
            db = datasette.get_database(database)
            if actor is not None and await db.table_exists(
                "saved_queries"
            ):
                results = await db.execute(
                    "select name, sql from saved_queries where actor_id = :id",
                    {"id": actor["id"]},
                )
                return {
                    result["name"]: {"sql": result["sql"]}
                    for result in results
                }

        return inner

Example: `datasette-saved-queries <https://datasette.io/plugins/datasette-saved-queries>`__

.. _plugin_hook_actor_from_request:

actor_from_request(datasette, request)
--------------------------------------

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``, or to execute SQL queries.

``request`` - :ref:`internals_request`
    The current HTTP request.

This is part of Datasette's :ref:`authentication and permissions system <authentication>`. The function should attempt to authenticate an actor (either a user or an API actor of some sort) based on information in the request.

If it cannot authenticate an actor, it should return ``None``, otherwise it should return a dictionary representing that actor. Once a plugin has returned an actor from this hook other plugins will be ignored.

Here's an example that authenticates the actor based on an incoming API key:

.. code-block:: python

    from datasette import hookimpl
    import secrets

    SECRET_KEY = "this-is-a-secret"


    @hookimpl
    def actor_from_request(datasette, request):
        authorization = (
            request.headers.get("authorization") or ""
        )
        expected = "Bearer {}".format(SECRET_KEY)

        if secrets.compare_digest(authorization, expected):
            return {"id": "bot"}

If you install this in your plugins directory you can test it like this::

    curl -H 'Authorization: Bearer this-is-a-secret' http://localhost:8003/-/actor.json

Instead of returning a dictionary, this function can return an awaitable function which itself returns either ``None`` or a dictionary. This is useful for authentication functions that need to make a database query - for example:

.. code-block:: python

    from datasette import hookimpl


    @hookimpl
    def actor_from_request(datasette, request):
        async def inner():
            token = request.args.get("_token")
            if not token:
                return None
            # Look up ?_token=xxx in sessions table
            result = await datasette.get_database().execute(
                "select count(*) from sessions where token = ?",
                [token],
            )
            if result.first()[0]:
                return {"token": token}
            else:
                return None

        return inner

Examples: `datasette-auth-tokens <https://datasette.io/plugins/datasette-auth-tokens>`_, `datasette-auth-passwords <https://datasette.io/plugins/datasette-auth-passwords>`_

.. _plugin_hook_actors_from_ids:

actors_from_ids(datasette, actor_ids)
-------------------------------------

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``, or to execute SQL queries.

``actor_ids`` - list of strings or integers
    The actor IDs to look up.

The hook must return a dictionary that maps the incoming actor IDs to their full dictionary representation.

Some plugins that implement social features may store the ID of the :ref:`actor <authentication_actor>` that performed an action - added a comment, bookmarked a table or similar - and then need a way to resolve those IDs into display-friendly actor dictionaries later on.

The :ref:`await datasette.actors_from_ids(actor_ids) <datasette_actors_from_ids>` internal method can be used to look up actors from their IDs. It will dispatch to the first plugin that implements this hook.

Unlike other plugin hooks, this only uses the first implementation of the hook to return a result. You can expect users to only have a single plugin installed that implements this hook.

If no plugin is installed, Datasette defaults to returning actors that are just ``{"id": actor_id}``.

The hook can return a dictionary or an awaitable function that then returns a dictionary.

This example implementation returns actors from a database table:

.. code-block:: python

    from datasette import hookimpl


    @hookimpl
    def actors_from_ids(datasette, actor_ids):
        db = datasette.get_database("actors")

        async def inner():
            sql = "select id, name from actors where id in ({})".format(
                ", ".join("?" for _ in actor_ids)
            )
            actors = {}
            for row in (await db.execute(sql, actor_ids)).rows:
                actor = dict(row)
                actors[actor["id"]] = actor
            return actors

        return inner

The returned dictionary from this example looks like this:

.. code-block:: json

    {
        "1": {"id": "1", "name": "Tony"},
        "2": {"id": "2", "name": "Tina"},
    }

These IDs could be integers or strings, depending on how the actors used by the Datasette instance are configured.

Example: `datasette-remote-actors <https://github.com/datasette/datasette-remote-actors>`_

.. _plugin_hook_jinja2_environment_from_request:

jinja2_environment_from_request(datasette, request, env)
--------------------------------------------------------

``datasette`` - :ref:`internals_datasette`
    A Datasette instance.

``request`` - :ref:`internals_request` or ``None``
    The current HTTP request, if one is available.

``env`` - ``Environment``
    The Jinja2 environment that will be used to render the current page.

This hook can be used to return a customized `Jinja environment <https://jinja.palletsprojects.com/en/3.0.x/api/#jinja2.Environment>`__ based on the incoming request.

If you want to run a single Datasette instance that serves different content for different domains, you can do so like this:

.. code-block:: python

    from datasette import hookimpl
    from jinja2 import ChoiceLoader, FileSystemLoader


    @hookimpl
    def jinja2_environment_from_request(request, env):
        if request and request.host == "www.niche-museums.com":
            return env.overlay(
                loader=ChoiceLoader(
                    [
                        FileSystemLoader(
                            "/mnt/niche-museums/templates"
                        ),
                        env.loader,
                    ]
                ),
                enable_async=True,
            )
        return env

This uses the Jinja `overlay() method <https://jinja.palletsprojects.com/en/3.0.x/api/#jinja2.Environment.overlay>`__ to create a new environment identical to the default environment except for having a different template loader, which first looks in the ``/mnt/niche-museums/templates`` directory before falling back on the default loader.

.. _plugin_hook_filters_from_request:

filters_from_request(request, database, table, datasette)
---------------------------------------------------------

``request`` - :ref:`internals_request`
    The current HTTP request.

``database`` - string
    The name of the database.

``table`` - string
    The name of the table.

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``, or to execute SQL queries.

This hook runs on the :ref:`table <TableView>` page, and can influence the ``where`` clause of the SQL query used to populate that page, based on query string arguments on the incoming request.

The hook should return an instance of ``datasette.filters.FilterArguments`` which has one required and three optional arguments:

.. code-block:: python

    return FilterArguments(
        where_clauses=["id > :max_id"],
        params={"max_id": 5},
        human_descriptions=["max_id is greater than 5"],
        extra_context={},
    )

The arguments to the ``FilterArguments`` class constructor are as follows:

``where_clauses`` - list of strings, required
    A list of SQL fragments that will be inserted into the SQL query, joined by the ``and`` operator. These can include ``:named`` parameters which will be populated using data in ``params``.
``params`` - dictionary, optional
    Additional keyword arguments to be used when the query is executed. These should match any ``:arguments`` in the where clauses.
``human_descriptions`` - list of strings, optional
    These strings will be included in the human-readable description at the top of the page and the page ``<title>``.
``extra_context`` - dictionary, optional
    Additional context variables that should be made available to the ``table.html`` template when it is rendered.

This example plugin causes 0 results to be returned if ``?_nothing=1`` is added to the URL:

.. code-block:: python

    from datasette import hookimpl
    from datasette.filters import FilterArguments


    @hookimpl
    def filters_from_request(self, request):
        if request.args.get("_nothing"):
            return FilterArguments(
                ["1 = 0"], human_descriptions=["NOTHING"]
            )

Example: `datasette-leaflet-freedraw <https://datasette.io/plugins/datasette-leaflet-freedraw>`_

.. _plugin_hook_permission_allowed:

permission_allowed(datasette, actor, action, resource)
------------------------------------------------------

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``, or to execute SQL queries.

``actor`` - dictionary
    The current actor, as decided by :ref:`plugin_hook_actor_from_request`.

``action`` - string
    The action to be performed, e.g. ``"edit-table"``.

``resource`` - string or None
    An identifier for the individual resource, e.g. the name of the table.

Called to check that an actor has permission to perform an action on a resource. Can return ``True`` if the action is allowed, ``False`` if the action is not allowed or ``None`` if the plugin does not have an opinion one way or the other.

Here's an example plugin which randomly selects if a permission should be allowed or denied, except for ``view-instance`` which always uses the default permission scheme instead.

.. code-block:: python

    from datasette import hookimpl
    import random


    @hookimpl
    def permission_allowed(action):
        if action != "view-instance":
            # Return True or False at random
            return random.random() > 0.5
        # Returning None falls back to default permissions

This function can alternatively return an awaitable function which itself returns ``True``, ``False`` or ``None``. You can use this option if you need to execute additional database queries using ``await datasette.execute(...)``.

Here's an example that allows users to view the ``admin_log`` table only if their actor ``id`` is present in the ``admin_users`` table. It aso disallows arbitrary SQL queries for the ``staff.db`` database for all users.

.. code-block:: python

    @hookimpl
    def permission_allowed(datasette, actor, action, resource):
        async def inner():
            if action == "execute-sql" and resource == "staff":
                return False
            if action == "view-table" and resource == (
                "staff",
                "admin_log",
            ):
                if not actor:
                    return False
                user_id = actor["id"]
                result = await datasette.get_database(
                    "staff"
                ).execute(
                    "select count(*) from admin_users where user_id = :user_id",
                    {"user_id": user_id},
                )
                return result.first()[0] > 0

        return inner

See :ref:`built-in permissions <authentication_permissions>` for a full list of permissions that are included in Datasette core.

Example: `datasette-permissions-sql <https://datasette.io/plugins/datasette-permissions-sql>`_

.. _plugin_hook_permission_resources_sql:

permission_resources_sql(datasette, actor, action)
--------------------------------------------------

``datasette`` - :ref:`internals_datasette`
    Access to the Datasette instance.

``actor`` - dictionary or None
    The current actor dictionary. ``None`` for anonymous requests.

``action`` - string
    The permission action being evaluated. Examples include ``"view-table"`` or ``"insert-row"``.

Return value
    A :class:`datasette.permissions.PermissionSQL` object, ``None`` or an iterable of ``PermissionSQL`` objects.

Datasette's action-based permission resolver calls this hook to gather SQL rows describing which
resources an actor may access (``allow = 1``) or should be denied (``allow = 0``) for a specific action.
Each SQL snippet should return ``parent``, ``child``, ``allow`` and ``reason`` columns.

**Parameter naming convention:** Plugin parameters in ``PermissionSQL.params`` should use unique names
to avoid conflicts with other plugins. The recommended convention is to prefix parameters with your
plugin's source name (e.g., ``myplugin_user_id``). The system reserves these parameter names:
``:actor``, ``:actor_id``, ``:action``, and ``:filter_parent``.

You can also use return ``PermissionSQL.allow(reason="reason goes here")`` or ``PermissionSQL.deny(reason="reason goes here")`` as shortcuts for simple root-level allow or deny rules. These will create SQL snippets that look like this:

.. code-block:: sql

    SELECT
        NULL AS parent,
        NULL AS child,
        1 AS allow,
        'reason goes here' AS reason

Or ``0 AS allow`` for denies.

Permission plugin examples
~~~~~~~~~~~~~~~~~~~~~~~~~~

These snippets show how to use the new ``permission_resources_sql`` hook to
contribute rows to the action-based permission resolver. Each hook receives the
current actor dictionary (or ``None``) and must return ``None`` or an instance or list of
``datasette.permissions.PermissionSQL`` (or a coroutine that resolves to that).

Allow Alice to view a specific table
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

This plugin grants the actor with ``id == "alice"`` permission to perform the
``view-table`` action against the ``sales`` table inside the ``accounting`` database.

.. code-block:: python

    from datasette import hookimpl
    from datasette.permissions import PermissionSQL


    @hookimpl
    def permission_resources_sql(datasette, actor, action):
        if action != "view-table":
            return None
        if not actor or actor.get("id") != "alice":
            return None

        return PermissionSQL(
            sql="""
                SELECT
                    'accounting' AS parent,
                    'sales' AS child,
                    1 AS allow,
                    'alice can view accounting/sales' AS reason
            """,
        )

Restrict execute-sql to a database prefix
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Only allow ``execute-sql`` against databases whose name begins with
``analytics_``. This shows how to use parameters that the permission resolver
will pass through to the SQL snippet.

.. code-block:: python

    from datasette import hookimpl
    from datasette.permissions import PermissionSQL


    @hookimpl
    def permission_resources_sql(datasette, actor, action):
        if action != "execute-sql":
            return None

        return PermissionSQL(
            sql="""
                SELECT
                    parent,
                    NULL AS child,
                    1 AS allow,
                    'execute-sql allowed for analytics_*' AS reason
                FROM catalog_databases
                WHERE database_name LIKE :analytics_prefix
            """,
            params={
                "analytics_prefix": "analytics_%",
            },
        )

Read permissions from a custom table
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

This example stores grants in an internal table called ``permission_grants``
with columns ``(actor_id, action, parent, child, allow, reason)``.

.. code-block:: python

    from datasette import hookimpl
    from datasette.permissions import PermissionSQL


    @hookimpl
    def permission_resources_sql(datasette, actor, action):
        if not actor:
            return None

        return PermissionSQL(
            sql="""
                SELECT
                    parent,
                    child,
                    allow,
                    COALESCE(reason, 'permission_grants table') AS reason
                FROM permission_grants
                WHERE actor_id = :grants_actor_id
                  AND action = :grants_action
            """,
            params={
                "grants_actor_id": actor.get("id"),
                "grants_action": action,
            },
        )

Default deny with an exception
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Combine a root-level deny with a specific table allow for trusted users.
The resolver will automatically apply the most specific rule.

.. code-block:: python

    from datasette import hookimpl
    from datasette.permissions import PermissionSQL


    TRUSTED = {"alice", "bob"}


    @hookimpl
    def permission_resources_sql(datasette, actor, action):
        if action != "view-table":
            return None

        actor_id = (actor or {}).get("id")

        if actor_id not in TRUSTED:
            return PermissionSQL(
                sql="""
                    SELECT NULL AS parent, NULL AS child, 0 AS allow,
                           'default deny view-table' AS reason
                """,
            )

        return PermissionSQL(
            sql="""
                SELECT NULL AS parent, NULL AS child, 0 AS allow,
                       'default deny view-table' AS reason
                UNION ALL
                SELECT 'reports' AS parent, 'daily_metrics' AS child, 1 AS allow,
                       'trusted user access' AS reason
            """,
            params={"actor_id": actor_id},
        )

The ``UNION ALL`` ensures the deny rule is always present, while the second row
adds the exception for trusted users.

.. _plugin_hook_register_magic_parameters:

register_magic_parameters(datasette)
------------------------------------

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``.

:ref:`canned_queries_magic_parameters` can be used to add automatic parameters to :ref:`canned queries <canned_queries>`. This plugin hook allows additional magic parameters to be defined by plugins.

Magic parameters all take this format: ``_prefix_rest_of_parameter``. The prefix indicates which magic parameter function should be called - the rest of the parameter is passed as an argument to that function.

To register a new function, return it as a tuple of ``(string prefix, function)`` from this hook. The function you register should take two arguments: ``key`` and ``request``, where ``key`` is the ``rest_of_parameter`` portion of the parameter and ``request`` is the current :ref:`internals_request`.

This example registers two new magic parameters: ``:_request_http_version`` returning the HTTP version of the current request, and ``:_uuid_new`` which returns a new UUID. It also registers an ``:_asynclookup_key`` parameter, demonstrating that these functions can be asynchronous:

.. code-block:: python

    from datasette import hookimpl
    from uuid import uuid4


    def uuid(key, request):
        if key == "new":
            return str(uuid4())
        else:
            raise KeyError


    def request(key, request):
        if key == "http_version":
            return request.scope["http_version"]
        else:
            raise KeyError


    async def asynclookup(key, request):
        return await do_something_async(key)


    @hookimpl
    def register_magic_parameters(datasette):
        return [
            ("request", request),
            ("uuid", uuid),
            ("asynclookup", asynclookup),
        ]

.. _plugin_hook_forbidden:

forbidden(datasette, request, message)
--------------------------------------

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``, or to render templates or execute SQL queries.

``request`` - :ref:`internals_request`
    The current HTTP request.

``message`` - string
    A message hinting at why the request was forbidden.

Plugins can use this to customize how Datasette responds when a 403 Forbidden error occurs - usually because a page failed a permission check, see :ref:`authentication_permissions`.

If a plugin hook wishes to react to the error, it should return a :ref:`Response object <internals_response>`.

This example returns a redirect to a ``/-/login`` page:

.. code-block:: python

    from datasette import hookimpl
    from urllib.parse import urlencode


    @hookimpl
    def forbidden(request, message):
        return Response.redirect(
            "/-/login?=" + urlencode({"message": message})
        )

The function can alternatively return an awaitable function if it needs to make any asynchronous method calls. This example renders a template:

.. code-block:: python

    from datasette import hookimpl, Response


    @hookimpl
    def forbidden(datasette):
        async def inner():
            return Response.html(
                await datasette.render_template(
                    "render_message.html", request=request
                )
            )

        return inner

.. _plugin_hook_handle_exception:

handle_exception(datasette, request, exception)
-----------------------------------------------

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``, or to render templates or execute SQL queries.

``request`` - :ref:`internals_request`
    The current HTTP request.

``exception`` - ``Exception``
    The exception that was raised.

This hook is called any time an unexpected exception is raised. You can use it to record the exception.

If your handler returns a ``Response`` object it will be returned to the client in place of the default Datasette error page.

The handler can return a response directly, or it can return return an awaitable function that returns a response.

This example logs an error to `Sentry <https://sentry.io/>`__ and then renders a custom error page:

.. code-block:: python

    from datasette import hookimpl, Response
    import sentry_sdk


    @hookimpl
    def handle_exception(datasette, exception):
        sentry_sdk.capture_exception(exception)

        async def inner():
            return Response.html(
                await datasette.render_template(
                    "custom_error.html", request=request
                )
            )

        return inner

Example: `datasette-sentry <https://datasette.io/plugins/datasette-sentry>`_

.. _plugin_hook_skip_csrf:

skip_csrf(datasette, scope)
---------------------------

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``, or to execute SQL queries.

``scope`` - dictionary
    The `ASGI scope <https://asgi.readthedocs.io/en/latest/specs/www.html#http-connection-scope>`__ for the incoming HTTP request.

This hook can be used to skip :ref:`internals_csrf` for a specific incoming request. For example, you might have a custom path at ``/submit-comment`` which is designed to accept comments from anywhere, whether or not the incoming request originated on the site and has an accompanying CSRF token.

This example will disable CSRF protection for that specific URL path:

.. code-block:: python

    from datasette import hookimpl


    @hookimpl
    def skip_csrf(scope):
        return scope["path"] == "/submit-comment"

If any of the currently active ``skip_csrf()`` plugin hooks return ``True``, CSRF protection will be skipped for the request.

.. _plugin_hook_menu_links:

menu_links(datasette, actor, request)
-------------------------------------

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``, or to execute SQL queries.

``actor`` - dictionary or None
    The currently authenticated :ref:`actor <authentication_actor>`.

``request`` - :ref:`internals_request` or None
    The current HTTP request. This can be ``None`` if the request object is not available.

This hook allows additional items to be included in the menu displayed by Datasette's top right menu icon.

The hook should return a list of ``{"href": "...", "label": "..."}`` menu items. These will be added to the menu.

It can alternatively return an ``async def`` awaitable function which returns a list of menu items.

This example adds a new menu item but only if the signed in user is ``"root"``:

.. code-block:: python

    from datasette import hookimpl


    @hookimpl
    def menu_links(datasette, actor):
        if actor and actor.get("id") == "root":
            return [
                {
                    "href": datasette.urls.path(
                        "/-/edit-schema"
                    ),
                    "label": "Edit schema",
                },
            ]

Using :ref:`internals_datasette_urls` here ensures that links in the menu will take the :ref:`setting_base_url` setting into account.

Examples: `datasette-search-all <https://datasette.io/plugins/datasette-search-all>`_, `datasette-graphql <https://datasette.io/plugins/datasette-graphql>`_

.. _plugin_actions:

Action hooks
------------

Action hooks can be used to add items to the action menus that appear at the top of different pages within Datasette. Unlike :ref:`menu_links() <plugin_hook_menu_links>`, actions which are displayed on every page, actions should only be relevant to the page the user is currently viewing.

Each of these hooks should return return a list of ``{"href": "...", "label": "..."}`` menu items, with optional ``"description": "..."`` keys describing each action in more detail.

They can alternatively return an ``async def`` awaitable function which, when called, returns a list of those menu items.

.. _plugin_hook_table_actions:

table_actions(datasette, actor, database, table, request)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``, or to execute SQL queries.

``actor`` - dictionary or None
    The currently authenticated :ref:`actor <authentication_actor>`.

``database`` - string
    The name of the database.

``table`` - string
    The name of the table.

``request`` - :ref:`internals_request` or None
    The current HTTP request. This can be ``None`` if the request object is not available.

This example adds a new table action if the signed in user is ``"root"``:

.. code-block:: python

    from datasette import hookimpl


    @hookimpl
    def table_actions(datasette, actor, database, table):
        if actor and actor.get("id") == "root":
            return [
                {
                    "href": datasette.urls.path(
                        "/-/edit-schema/{}/{}".format(
                            database, table
                        )
                    ),
                    "label": "Edit schema for this table",
                    "description": "Add, remove, rename or alter columns for this table.",
                }
            ]

Example: `datasette-graphql <https://datasette.io/plugins/datasette-graphql>`_

.. _plugin_hook_view_actions:

view_actions(datasette, actor, database, view, request)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``, or to execute SQL queries.

``actor`` - dictionary or None
    The currently authenticated :ref:`actor <authentication_actor>`.

``database`` - string
    The name of the database.

``view`` - string
    The name of the SQL view.

``request`` - :ref:`internals_request` or None
    The current HTTP request. This can be ``None`` if the request object is not available.

Like :ref:`plugin_hook_table_actions` but for SQL views.

.. _plugin_hook_query_actions:

query_actions(datasette, actor, database, query_name, request, sql, params)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``, or to execute SQL queries.

``actor`` - dictionary or None
    The currently authenticated :ref:`actor <authentication_actor>`.

``database`` - string
    The name of the database.

``query_name`` - string or None
    The name of the canned query, or ``None`` if this is an arbitrary SQL query.

``request`` - :ref:`internals_request`
    The current HTTP request.

``sql`` - string
    The SQL query being executed

``params`` - dictionary
    The parameters passed to the SQL query, if any.

Populates a "Query actions" menu on the canned query and arbitrary SQL query pages.

This example adds a new query action linking to a page for explaining a query:

.. code-block:: python

    from datasette import hookimpl
    import urllib


    @hookimpl
    def query_actions(datasette, database, query_name, sql):
        # Don't explain an explain
        if sql.lower().startswith("explain"):
            return
        return [
            {
                "href": datasette.urls.database(database)
                + "?"
                + urllib.parse.urlencode(
                    {
                        "sql": "explain " + sql,
                    }
                ),
                "label": "Explain this query",
                "description": "Get a summary of how SQLite executes the query",
            },
        ]

Example: `datasette-create-view <https://datasette.io/plugins/datasette-create-view>`_

.. _plugin_hook_row_actions:

row_actions(datasette, actor, request, database, table, row)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``, or to execute SQL queries.

``actor`` - dictionary or None
    The currently authenticated :ref:`actor <authentication_actor>`.

``request`` - :ref:`internals_request` or None
    The current HTTP request.

``database`` - string
    The name of the database.

``table`` - string
    The name of the table.

``row`` - ``sqlite.Row``
    The SQLite row object being displayed on the page.

Return links for the "Row actions" menu shown at the top of the row page.

This example displays the row in JSON plus some additional debug information if the user is signed in:

.. code-block:: python

    from datasette import hookimpl


    @hookimpl
    def row_actions(datasette, database, table, actor, row):
        if actor:
            return [
                {
                    "href": datasette.urls.instance(),
                    "label": f"Row details for {actor['id']}",
                    "description": json.dumps(
                        dict(row), default=repr
                    ),
                },
            ]

Example: `datasette-enrichments <https://datasette.io/plugins/datasette-enrichments>`_

.. _plugin_hook_database_actions:

database_actions(datasette, actor, database, request)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``, or to execute SQL queries.

``actor`` - dictionary or None
    The currently authenticated :ref:`actor <authentication_actor>`.

``database`` - string
    The name of the database.

``request`` - :ref:`internals_request`
    The current HTTP request.

Populates an actions menu on the database page.

This example adds a new database action for creating a table, if the user has the ``edit-schema`` permission:

.. code-block:: python

    from datasette import hookimpl


    @hookimpl
    def database_actions(datasette, actor, database):
        async def inner():
            if not await datasette.permission_allowed(
                actor,
                "edit-schema",
                resource=database,
                default=False,
            ):
                return []
            return [
                {
                    "href": datasette.urls.path(
                        "/-/edit-schema/{}/-/create".format(
                            database
                        )
                    ),
                    "label": "Create a table",
                }
            ]

        return inner

Example: `datasette-graphql <https://datasette.io/plugins/datasette-graphql>`_, `datasette-edit-schema <https://datasette.io/plugins/datasette-edit-schema>`_

.. _plugin_hook_homepage_actions:

homepage_actions(datasette, actor, request)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``, or to execute SQL queries.

``actor`` - dictionary or None
    The currently authenticated :ref:`actor <authentication_actor>`.

``request`` - :ref:`internals_request`
    The current HTTP request.

Populates an actions menu on the top-level index homepage of the Datasette instance.

This example adds a link an imagined tool for editing the homepage, only for signed in users:

.. code-block:: python

    from datasette import hookimpl


    @hookimpl
    def homepage_actions(datasette, actor):
        if actor:
            return [
                {
                    "href": datasette.urls.path(
                        "/-/customize-homepage"
                    ),
                    "label": "Customize homepage",
                }
            ]

.. _plugin_hook_slots:

Template slots
--------------

The following set of plugin hooks can be used to return extra HTML content that will be inserted into the corresponding page, directly below the ``<h1>`` heading.

Multiple plugins can contribute content here. The order in which it is displayed can be controlled using Pluggy's `call time order options <https://pluggy.readthedocs.io/en/stable/#call-time-order>`__.

Each of these plugin hooks can return either a string or an awaitable function that returns a string.

.. _plugin_hook_top_homepage:

top_homepage(datasette, request)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``.

``request`` - :ref:`internals_request`
    The current HTTP request.

Returns HTML to be displayed at the top of the Datasette homepage.

.. _plugin_hook_top_database:

top_database(datasette, request, database)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``.

``request`` - :ref:`internals_request`
    The current HTTP request.

``database`` - string
    The name of the database.

Returns HTML to be displayed at the top of the database page.

.. _plugin_hook_top_table:

top_table(datasette, request, database, table)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``.

``request`` - :ref:`internals_request`
    The current HTTP request.

``database`` - string
    The name of the database.

``table`` - string
    The name of the table.

Returns HTML to be displayed at the top of the table page.

.. _plugin_hook_top_row:

top_row(datasette, request, database, table, row)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``.

``request`` - :ref:`internals_request`
    The current HTTP request.

``database`` - string
    The name of the database.

``table`` - string
    The name of the table.

``row`` - ``sqlite.Row``
    The SQLite row object being displayed.

Returns HTML to be displayed at the top of the row page.

.. _plugin_hook_top_query:

top_query(datasette, request, database, sql)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``.

``request`` - :ref:`internals_request`
    The current HTTP request.

``database`` - string
    The name of the database.

``sql`` - string
    The SQL query.

Returns HTML to be displayed at the top of the query results page.

.. _plugin_hook_top_canned_query:

top_canned_query(datasette, request, database, query_name)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``.

``request`` - :ref:`internals_request`
    The current HTTP request.

``database`` - string
    The name of the database.

``query_name`` - string
    The name of the canned query.

Returns HTML to be displayed at the top of the canned query page.

.. _plugin_event_tracking:

Event tracking
--------------

Datasette includes an internal mechanism for tracking notable events. This can be used for analytics, but can also be used by plugins that want to listen out for when key events occur (such as a table being created) and take action in response.

Plugins can register to receive events using the ``track_event`` plugin hook.

They can also define their own events for other plugins to receive using the :ref:`register_events() plugin hook <plugin_hook_register_events>`, combined with calls to the :ref:`datasette.track_event() internal method <datasette_track_event>`.

.. _plugin_hook_track_event:

track_event(datasette, event)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``.

``event`` - ``Event``
    Information about the event, represented as an instance of a subclass of the ``Event`` base class.

This hook will be called any time an event is tracked by code that calls the :ref:`datasette.track_event(...) <datasette_track_event>` internal method.

The ``event`` object will always have the following properties:

- ``name``: a string representing the name of the event, for example ``logout`` or ``create-table``.
- ``actor``: a dictionary representing the actor that triggered the event, or ``None`` if the event was not triggered by an actor.
- ``created``: a ``datatime.datetime`` object in the ``timezone.utc`` timezone representing the time the event object was created.

Other properties on the event will be available depending on the type of event. You can also access those as a dictionary using ``event.properties()``.

The events fired by Datasette core are :ref:`documented here <events>`.

This example plugin logs details of all events to standard error:

.. code-block:: python

    from datasette import hookimpl
    import json
    import sys


    @hookimpl
    def track_event(event):
        name = event.name
        actor = event.actor
        properties = event.properties()
        msg = json.dumps(
            {
                "name": name,
                "actor": actor,
                "properties": properties,
            }
        )
        print(msg, file=sys.stderr, flush=True)

The function can also return an async function which will be awaited. This is useful for writing to a database.

This example logs events to a ``datasette_events`` table in a database called ``events``. It uses the :ref:`plugin_hook_startup` hook to create that table if it does not exist.

.. code-block:: python

    from datasette import hookimpl
    import json


    @hookimpl
    def startup(datasette):
        async def inner():
            db = datasette.get_database("events")
            await db.execute_write(
                """
                create table if not exists datasette_events (
                    id integer primary key,
                    event_type text,
                    created text,
                    actor text,
                    properties text
                )
            """
            )

        return inner


    @hookimpl
    def track_event(datasette, event):
        async def inner():
            db = datasette.get_database("events")
            properties = event.properties()
            await db.execute_write(
                """
                insert into datasette_events (event_type, created, actor, properties)
                values (?, strftime('%Y-%m-%d %H:%M:%S', 'now'), ?, ?)
            """,
                (
                    event.name,
                    json.dumps(event.actor),
                    json.dumps(properties),
                ),
            )

        return inner

Example: `datasette-events-db <https://datasette.io/plugins/datasette-events-db>`_

.. _plugin_hook_register_events:

register_events(datasette)
~~~~~~~~~~~~~~~~~~~~~~~~~~

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``.

This hook should return a list of ``Event`` subclasses that represent custom events that the plugin might send to the :ref:`datasette.track_event() <datasette_track_event>` method.

This example registers event subclasses for ``ban-user`` and ``unban-user`` events:

.. code-block:: python

    from dataclasses import dataclass
    from datasette import hookimpl, Event


    @dataclass
    class BanUserEvent(Event):
        name = "ban-user"
        user: dict


    @dataclass
    class UnbanUserEvent(Event):
        name = "unban-user"
        user: dict


    @hookimpl
    def register_events():
        return [BanUserEvent, UnbanUserEvent]

The plugin can then call ``datasette.track_event(...)`` to send a ``ban-user`` event:

.. code-block:: python

    await datasette.track_event(
        BanUserEvent(user={"id": 1, "username": "cleverbot"})
    )
