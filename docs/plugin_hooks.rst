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

.. _plugin_hook_extra_template_vars:

extra_template_vars(template, database, table, columns, view_name, request, datasette)
--------------------------------------------------------------------------------------

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
---------------------------------------------------------------------------------

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
--------------------------------------------------------------------------------

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

If your code uses `JavaScript modules <https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Modules>`__ you should include the ``"module": True`` key. See :ref:`customization_css_and_javascript` for more details.

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
------------------------------------------------------------------------------------

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

render_cell(row, value, column, table, database, datasette)
-----------------------------------------------------------

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

``can_render_demo`` is a Python function (or ``async def`` function) which accepts the same arguments as ``render_demo`` but just returns ``True`` or ``False``. It lets Datasette know if the current SQL query can be represented by the plugin - and hence influnce if a link to this output format is displayed in the user interface. If you omit the ``"can_render"`` key from the dictionary every query will be treated as being supported by the plugin.

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

Note that ``register_commands()`` plugins cannot used with the :ref:`--plugins-dir mechanism <writing_plugins_one_off>` - they need to be installed into the same virtual environment as Datasette using ``pip install``. Provided it has a ``setup.py`` file (see :ref:`writing_plugins_packaging`) you can run ``pip install`` directly against the directory in which you are developing your plugin like so::

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

This hook fires when the Datasette application server first starts up. You can implement a regular function, for example to validate required plugin configuration:

.. code-block:: python

    @hookimpl
    def startup(datasette):
        config = datasette.plugin_config("my-plugin") or {}
        assert (
            "required-setting" in config
        ), "my-plugin requires setting required-setting"

Or you can return an async function which will be awaited on startup. Use this option if you need to make any database queries:

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
* Validate the metadata configuration for a plugin on startup, and raise an error if it is invalid

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

If it cannot authenticate an actor, it should return ``None``. Otherwise it should return a dictionary representing that actor.

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

    $ curl -H 'Authorization: Bearer this-is-a-secret' http://localhost:8003/-/actor.json

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

Example: `datasette-auth-tokens <https://datasette.io/plugins/datasette-auth-tokens>`_

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
                return await datasette.get_database(
                    "staff"
                ).execute(
                    "select count(*) from admin_users where user_id = :user_id",
                    {"user_id": user_id},
                )

        return inner

See :ref:`built-in permissions <permissions>` for a full list of permissions that are included in Datasette core.

Example: `datasette-permissions-sql <https://datasette.io/plugins/datasette-permissions-sql>`_

.. _plugin_hook_register_magic_parameters:

register_magic_parameters(datasette)
------------------------------------

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``.

:ref:`canned_queries_magic_parameters` can be used to add automatic parameters to :ref:`canned queries <canned_queries>`. This plugin hook allows additional magic parameters to be defined by plugins.

Magic parameters all take this format: ``_prefix_rest_of_parameter``. The prefix indicates which magic parameter function should be called - the rest of the parameter is passed as an argument to that function.

To register a new function, return it as a tuple of ``(string prefix, function)`` from this hook. The function you register should take two arguments: ``key`` and ``request``, where ``key`` is the ``rest_of_parameter`` portion of the parameter and ``request`` is the current :ref:`internals_request`.

This example registers two new magic parameters: ``:_request_http_version`` returning the HTTP version of the current request, and ``:_uuid_new`` which returns a new UUID:

.. code-block:: python

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


    @hookimpl
    def register_magic_parameters(datasette):
        return [
            ("request", request),
            ("uuid", uuid),
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

.. _plugin_hook_table_actions:

table_actions(datasette, actor, database, table, request)
---------------------------------------------------------

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

This hook allows table actions to be displayed in a menu accessed via an action icon at the top of the table page. It should return a list of ``{"href": "...", "label": "..."}`` menu items.

It can alternatively return an ``async def`` awaitable function which returns a list of menu items.

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
                }
            ]

Example: `datasette-graphql <https://datasette.io/plugins/datasette-graphql>`_

.. _plugin_hook_database_actions:

database_actions(datasette, actor, database, request)
-----------------------------------------------------

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``, or to execute SQL queries.

``actor`` - dictionary or None
    The currently authenticated :ref:`actor <authentication_actor>`.

``database`` - string
    The name of the database.

``request`` - :ref:`internals_request`
    The current HTTP request.

This hook is similar to :ref:`plugin_hook_table_actions` but populates an actions menu on the database page.

Example: `datasette-graphql <https://datasette.io/plugins/datasette-graphql>`_

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

.. _plugin_hook_get_metadata:

get_metadata(datasette, key, database, table)
---------------------------------------------

``datasette`` - :ref:`internals_datasette`
    You can use this to access plugin configuration options via ``datasette.plugin_config(your_plugin_name)``.

``actor`` - dictionary or None
    The currently authenticated :ref:`actor <authentication_actor>`.

``database`` - string or None
    The name of the database metadata is being asked for.

``table`` - string or None
    The name of the table.

``key`` - string or None
    The name of the key for which data is being asked for.

This hook is responsible for returning a dictionary corresponding to Datasette :ref:`metadata`. This function is passed the ``database``, ``table`` and ``key`` which were passed to the upstream internal request for metadata. Regardless, it is important to return a global metadata object, where ``"databases": []`` would be a top-level key. The dictionary returned here, will be merged with, and overwritten by, the contents of the physical ``metadata.yaml`` if one is present.

.. warning::
    The design of this plugin hook does not currently provide a mechanism for interacting with async code, and may change in the future. See `issue 1384 <https://github.com/simonw/datasette/issues/1384>`__.

.. code-block:: python

    @hookimpl
    def get_metadata(datasette, key, database, table):
        metadata = {
            "title": "This will be the Datasette landing page title!",
            "description": get_instance_description(datasette),
            "databases": [],
        }
        for db_name, db_data_dict in get_my_database_meta(
            datasette, database, table, key
        ):
            metadata["databases"][db_name] = db_data_dict
        # whatever we return here will be merged with any other plugins using this hook and
        # will be overwritten by a local metadata.yaml if one exists!
        return metadata

Example: `datasette-remote-metadata plugin <https://datasette.io/plugins/datasette-remote-metadata>`__
