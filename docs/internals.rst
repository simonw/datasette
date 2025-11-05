.. _internals:

=======================
 Internals for plugins
=======================

Many :ref:`plugin_hooks` are passed objects that provide access to internal Datasette functionality. The interface to these objects should not be considered stable with the exception of methods that are documented here.

.. _internals_request:

Request object
==============

The request object is passed to various plugin hooks. It represents an incoming HTTP request. It has the following properties:

``.scope`` - dictionary
    The ASGI scope that was used to construct this request, described in the `ASGI HTTP connection scope <https://asgi.readthedocs.io/en/latest/specs/www.html#connection-scope>`__ specification.

``.method`` - string
    The HTTP method for this request, usually ``GET`` or ``POST``.

``.url`` - string
    The full URL for this request, e.g. ``https://latest.datasette.io/fixtures``.

``.scheme`` - string
    The request scheme - usually ``https`` or ``http``.

``.headers`` - dictionary (str -> str)
    A dictionary of incoming HTTP request headers. Header names have been converted to lowercase.

``.cookies`` - dictionary (str -> str)
    A dictionary of incoming cookies

``.host`` - string
    The host header from the incoming request, e.g. ``latest.datasette.io`` or ``localhost``.

``.path`` - string
    The path of the request excluding the query string, e.g. ``/fixtures``.

``.full_path`` - string
    The path of the request including the query string if one is present, e.g. ``/fixtures?sql=select+sqlite_version()``.

``.query_string`` - string
    The query string component of the request, without the ``?`` - e.g. ``name__contains=sam&age__gt=10``.

``.args`` - MultiParams
    An object representing the parsed query string parameters, see below.

``.url_vars`` - dictionary (str -> str)
    Variables extracted from the URL path, if that path was defined using a regular expression. See :ref:`plugin_register_routes`.

``.actor`` - dictionary (str -> Any) or None
    The currently authenticated actor (see :ref:`actors <authentication_actor>`), or ``None`` if the request is unauthenticated.

The object also has two awaitable methods:

``await request.post_vars()`` - dictionary
    Returns a dictionary of form variables that were submitted in the request body via ``POST``. Don't forget to read about :ref:`internals_csrf`!

``await request.post_body()`` - bytes
    Returns the un-parsed body of a request submitted by ``POST`` - useful for things like incoming JSON data.

And a class method that can be used to create fake request objects for use in tests:

``fake(path_with_query_string, method="GET", scheme="http", url_vars=None)``
    Returns a ``Request`` instance for the specified path and method. For example:

    .. code-block:: python

        from datasette import Request
        from pprint import pprint

        request = Request.fake(
            "/fixtures/facetable/",
            url_vars={"database": "fixtures", "table": "facetable"},
        )
        pprint(request.scope)

    This outputs::

        {'http_version': '1.1',
         'method': 'GET',
         'path': '/fixtures/facetable/',
         'query_string': b'',
         'raw_path': b'/fixtures/facetable/',
         'scheme': 'http',
         'type': 'http',
         'url_route': {'kwargs': {'database': 'fixtures', 'table': 'facetable'}}}

.. _internals_multiparams:

The MultiParams class
=====================

``request.args`` is a ``MultiParams`` object - a dictionary-like object which provides access to query string parameters that may have multiple values.

Consider the query string ``?foo=1&foo=2&bar=3`` - with two values for ``foo`` and one value for ``bar``.

``request.args[key]`` - string
    Returns the first value for that key, or raises a ``KeyError`` if the key is missing. For the above example ``request.args["foo"]`` would return ``"1"``.

``request.args.get(key)`` - string or None
    Returns the first value for that key, or ``None`` if the key is missing. Pass a second argument to specify a different default, e.g. ``q = request.args.get("q", "")``.

``request.args.getlist(key)`` - list of strings
    Returns the list of strings for that key. ``request.args.getlist("foo")`` would return ``["1", "2"]`` in the above example. ``request.args.getlist("bar")`` would return ``["3"]``. If the key is missing an empty list will be returned.

``request.args.keys()`` - list of strings
    Returns the list of available keys - for the example this would be ``["foo", "bar"]``.

``key in request.args`` - True or False
    You can use ``if key in request.args`` to check if a key is present.

``for key in request.args`` - iterator
    This lets you loop through every available key.

``len(request.args)`` - integer
    Returns the number of keys.

.. _internals_response:

Response class
==============

The ``Response`` class can be returned from view functions that have been registered using the :ref:`plugin_register_routes` hook.

The ``Response()`` constructor takes the following arguments:

``body`` - string
    The body of the response.

``status`` - integer (optional)
    The HTTP status - defaults to 200.

``headers`` - dictionary (optional)
    A dictionary of extra HTTP headers, e.g. ``{"x-hello": "world"}``.

``content_type`` - string (optional)
    The content-type for the response. Defaults to ``text/plain``.

For example:

.. code-block:: python

    from datasette.utils.asgi import Response

    response = Response(
        "<xml>This is XML</xml>",
        content_type="application/xml; charset=utf-8",
    )

The quickest way to create responses is using the ``Response.text(...)``, ``Response.html(...)``, ``Response.json(...)`` or ``Response.redirect(...)`` helper methods:

.. code-block:: python

    from datasette.utils.asgi import Response

    html_response = Response.html("This is HTML")
    json_response = Response.json({"this_is": "json"})
    text_response = Response.text(
        "This will become utf-8 encoded text"
    )
    # Redirects are served as 302, unless you pass status=301:
    redirect_response = Response.redirect(
        "https://latest.datasette.io/"
    )

Each of these responses will use the correct corresponding content-type - ``text/html; charset=utf-8``, ``application/json; charset=utf-8`` or ``text/plain; charset=utf-8`` respectively.

Each of the helper methods take optional ``status=`` and ``headers=`` arguments, documented above.

.. _internals_response_asgi_send:

Returning a response with .asgi_send(send)
------------------------------------------

In most cases you will return ``Response`` objects from your own view functions. You can also use a ``Response`` instance to respond at a lower level via ASGI, for example if you are writing code that uses the :ref:`plugin_asgi_wrapper` hook.

Create a ``Response`` object and then use ``await response.asgi_send(send)``, passing the ASGI ``send`` function. For example:

.. code-block:: python

    async def require_authorization(scope, receive, send):
        response = Response.text(
            "401 Authorization Required",
            headers={
                "www-authenticate": 'Basic realm="Datasette", charset="UTF-8"'
            },
            status=401,
        )
        await response.asgi_send(send)

.. _internals_response_set_cookie:

Setting cookies with response.set_cookie()
------------------------------------------

To set cookies on the response, use the ``response.set_cookie(...)`` method. The method signature looks like this:

.. code-block:: python

    def set_cookie(
        self,
        key,
        value="",
        max_age=None,
        expires=None,
        path="/",
        domain=None,
        secure=False,
        httponly=False,
        samesite="lax",
    ): ...

You can use this with :ref:`datasette.sign() <datasette_sign>` to set signed cookies. Here's how you would set the :ref:`ds_actor cookie <authentication_ds_actor>` for use with Datasette :ref:`authentication <authentication>`:

.. code-block:: python

    response = Response.redirect("/")
    response.set_cookie(
        "ds_actor",
        datasette.sign({"a": {"id": "cleopaws"}}, "actor"),
    )
    return response

.. _internals_datasette:

Datasette class
===============

This object is an instance of the ``Datasette`` class, passed to many plugin hooks as an argument called ``datasette``.

You can create your own instance of this - for example to help write tests for a plugin - like so:

.. code-block:: python

    from datasette.app import Datasette

    # With no arguments a single in-memory database will be attached
    datasette = Datasette()

    # The files= argument can load files from disk
    datasette = Datasette(files=["/path/to/my-database.db"])

    # Pass metadata as a JSON dictionary like this
    datasette = Datasette(
        files=["/path/to/my-database.db"],
        metadata={
            "databases": {
                "my-database": {
                    "description": "This is my database"
                }
            }
        },
    )

Constructor parameters include:

- ``files=[...]`` - a list of database files to open
- ``immutables=[...]`` - a list of database files to open in immutable mode
- ``metadata={...}`` - a dictionary of :ref:`metadata`
- ``config_dir=...`` - the :ref:`configuration directory <config_dir>` to use, stored in ``datasette.config_dir``

.. _datasette_databases:

.databases
----------

Property exposing a ``collections.OrderedDict`` of databases currently connected to Datasette.

The dictionary keys are the name of the database that is used in the URL - e.g. ``/fixtures`` would have a key of ``"fixtures"``. The values are :ref:`internals_database` instances.

All databases are listed, irrespective of user permissions. This means that the ``_internal`` database will always be listed here.

.. _datasette_plugin_config:

.plugin_config(plugin_name, database=None, table=None)
------------------------------------------------------

``plugin_name`` - string
    The name of the plugin to look up configuration for. Usually this is something similar to ``datasette-cluster-map``.

``database`` - None or string
    The database the user is interacting with.

``table`` - None or string
    The table the user is interacting with.

This method lets you read plugin configuration values that were set in ``metadata.json``. See :ref:`writing_plugins_configuration` for full details of how this method should be used.

The return value will be the value from the configuration file - usually a dictionary.

If the plugin is not configured the return value will be ``None``.

.. _datasette_render_template:

await .render_template(template, context=None, request=None)
------------------------------------------------------------

``template`` - string, list of strings or jinja2.Template
    The template file to be rendered, e.g. ``my_plugin.html``. Datasette will search for this file first in the ``--template-dir=`` location, if it was specified - then in the plugin's bundled templates and finally in Datasette's set of default templates.

    If this is a list of template file names then the first one that exists will be loaded and rendered.

    If this is a Jinja `Template object <https://jinja.palletsprojects.com/en/2.11.x/api/#jinja2.Template>`__ it will be used directly.

``context`` - None or a Python dictionary
    The context variables to pass to the template.

``request`` - request object or None
    If you pass a Datasette request object here it will be made available to the template.

Renders a `Jinja template <https://jinja.palletsprojects.com/en/2.11.x/>`__ using Datasette's preconfigured instance of Jinja and returns the resulting string. The template will have access to Datasette's default template functions and any functions that have been made available by other plugins.

.. _datasette_permission_allowed:

await .permission_allowed(actor, action, resource=None, default=False)
----------------------------------------------------------------------

``actor`` - dictionary
    The authenticated actor. This is usually ``request.actor``.

``action`` - string
    The name of the action that is being permission checked.

``resource`` - string or tuple, optional
    The resource, e.g. the name of the database, or a tuple of two strings containing the name of the database and the name of the table. Only some permissions apply to a resource.

``default`` - optional, True or False
    Should this permission check be default allow or default deny.

Check if the given actor has :ref:`permission <authentication_permissions>` to perform the given action on the given resource.

Some permission checks are carried out against :ref:`rules defined in metadata.json <authentication_permissions_metadata>`, while other custom permissions may be decided by plugins that implement the :ref:`plugin_hook_permission_allowed` plugin hook.

If neither ``metadata.json`` nor any of the plugins provide an answer to the permission query the ``default`` argument will be returned.

See :ref:`permissions` for a full list of permission actions included in Datasette core.

.. _datasette_ensure_permissions:

await .ensure_permissions(actor, permissions)
---------------------------------------------

``actor`` - dictionary
    The authenticated actor. This is usually ``request.actor``.

``permissions`` - list
    A list of permissions to check. Each permission in that list can be a string ``action`` name or a 2-tuple of ``(action, resource)``.

This method allows multiple permissions to be checked at once. It raises a ``datasette.Forbidden`` exception if any of the checks are denied before one of them is explicitly granted.

This is useful when you need to check multiple permissions at once. For example, an actor should be able to view a table if either one of the following checks returns ``True`` or not a single one of them returns ``False``:

.. code-block:: python

    await self.ds.ensure_permissions(
        request.actor,
        [
            ("view-table", (database, table)),
            ("view-database", database),
            "view-instance",
        ],
    )

.. _datasette_check_visibility:

await .check_visibility(actor, action=None, resource=None, permissions=None)
----------------------------------------------------------------------------

``actor`` - dictionary
    The authenticated actor. This is usually ``request.actor``.

``action`` - string, optional
    The name of the action that is being permission checked.

``resource`` - string or tuple, optional
    The resource, e.g. the name of the database, or a tuple of two strings containing the name of the database and the name of the table. Only some permissions apply to a resource.

``permissions`` - list of ``action`` strings or ``(action, resource)`` tuples, optional
    Provide this instead of ``action`` and ``resource`` to check multiple permissions at once.

This convenience method can be used to answer the question "should this item be considered private, in that it is visible to me but it is not visible to anonymous users?"

It returns a tuple of two booleans, ``(visible, private)``. ``visible`` indicates if the actor can see this resource. ``private`` will be ``True`` if an anonymous user would not be able to view the resource.

This example checks if the user can access a specific table, and sets ``private`` so that a padlock icon can later be displayed:

.. code-block:: python

    visible, private = await self.ds.check_visibility(
        request.actor,
        action="view-table",
        resource=(database, table),
    )

The following example runs three checks in a row, similar to :ref:`datasette_ensure_permissions`. If any of the checks are denied before one of them is explicitly granted then ``visible`` will be ``False``. ``private`` will be ``True`` if an anonymous user would not be able to view the resource.

.. code-block:: python

    visible, private = await self.ds.check_visibility(
        request.actor,
        permissions=[
            ("view-table", (database, table)),
            ("view-database", database),
            "view-instance",
        ],
    )

.. _datasette_get_database:

.get_database(name)
-------------------

``name`` - string, optional
    The name of the database - optional.

Returns the specified database object. Raises a ``KeyError`` if the database does not exist. Call this method without an argument to return the first connected database.

.. _datasette_add_database:

.add_database(db, name=None, route=None)
----------------------------------------

``db`` - datasette.database.Database instance
    The database to be attached.

``name`` - string, optional
    The name to be used for this database . If not specified Datasette will pick one based on the filename or memory name.

``route`` - string, optional
    This will be used in the URL path. If not specified, it will default to the same thing as the ``name``.

The ``datasette.add_database(db)`` method lets you add a new database to the current Datasette instance.

The ``db`` parameter should be an instance of the ``datasette.database.Database`` class. For example:

.. code-block:: python

    from datasette.database import Database

    datasette.add_database(
        Database(
            datasette,
            path="path/to/my-new-database.db",
        )
    )

This will add a mutable database and serve it at ``/my-new-database``.

Use ``is_mutable=False`` to add an immutable database.

``.add_database()`` returns the Database instance, with its name set as the ``database.name`` attribute. Any time you are working with a newly added database you should use the return value of ``.add_database()``, for example:

.. code-block:: python

    db = datasette.add_database(
        Database(datasette, memory_name="statistics")
    )
    await db.execute_write(
        "CREATE TABLE foo(id integer primary key)"
    )

.. _datasette_add_memory_database:

.add_memory_database(name)
--------------------------

Adds a shared in-memory database with the specified name:

.. code-block:: python

    datasette.add_memory_database("statistics")

This is a shortcut for the following:

.. code-block:: python

    from datasette.database import Database

    datasette.add_database(
        Database(datasette, memory_name="statistics")
    )

Using either of these pattern will result in the in-memory database being served at ``/statistics``.

.. _datasette_remove_database:

.remove_database(name)
----------------------

``name`` - string
    The name of the database to be removed.

This removes a database that has been previously added. ``name=`` is the unique name of that database.

.. _datasette_sign:

.sign(value, namespace="default")
---------------------------------

``value`` - any serializable type
    The value to be signed.

``namespace`` - string, optional
    An alternative namespace, see the `itsdangerous salt documentation <https://itsdangerous.palletsprojects.com/en/1.1.x/serializer/#the-salt>`__.

Utility method for signing values, such that you can safely pass data to and from an untrusted environment. This is a wrapper around the `itsdangerous <https://itsdangerous.palletsprojects.com/>`__ library.

This method returns a signed string, which can be decoded and verified using :ref:`datasette_unsign`.

.. _datasette_unsign:

.unsign(value, namespace="default")
-----------------------------------

``signed`` - any serializable type
    The signed string that was created using :ref:`datasette_sign`.

``namespace`` - string, optional
    The alternative namespace, if one was used.

Returns the original, decoded object that was passed to :ref:`datasette_sign`. If the signature is not valid this raises a ``itsdangerous.BadSignature`` exception.

.. _datasette_add_message:

.add_message(request, message, type=datasette.INFO)
---------------------------------------------------

``request`` - Request
    The current Request object

``message`` - string
    The message string

``type`` - constant, optional
    The message type - ``datasette.INFO``, ``datasette.WARNING`` or ``datasette.ERROR``

Datasette's flash messaging mechanism allows you to add a message that will be displayed to the user on the next page that they visit. Messages are persisted in a ``ds_messages`` cookie. This method adds a message to that cookie.

You can try out these messages (including the different visual styling of the three message types) using the ``/-/messages`` debugging tool.

.. _datasette_absolute_url:

.absolute_url(request, path)
----------------------------

``request`` - Request
    The current Request object

``path`` - string
    A path, for example ``/dbname/table.json``

Returns the absolute URL for the given path, including the protocol and host. For example:

.. code-block:: python

    absolute_url = datasette.absolute_url(
        request, "/dbname/table.json"
    )
    # Would return "http://localhost:8001/dbname/table.json"

The current request object is used to determine the hostname and protocol that should be used for the returned URL. The :ref:`setting_force_https_urls` configuration setting is taken into account.

.. _datasette_setting:

.setting(key)
-------------

``key`` - string
    The name of the setting, e.g. ``base_url``.

Returns the configured value for the specified :ref:`setting <settings>`. This can be a string, boolean or integer depending on the requested setting.

For example:

.. code-block:: python

    downloads_are_allowed = datasette.setting("allow_download")

.. _internals_datasette_client:

datasette.client
----------------

Plugins can make internal simulated HTTP requests to the Datasette instance within which they are running. This ensures that all of Datasette's external JSON APIs are also available to plugins, while avoiding the overhead of making an external HTTP call to access those APIs.

The ``datasette.client`` object is a wrapper around the `HTTPX Python library <https://www.python-httpx.org/>`__, providing an async-friendly API that is similar to the widely used `Requests library <https://requests.readthedocs.io/>`__.

It offers the following methods:

``await datasette.client.get(path, **kwargs)`` - returns HTTPX Response
    Execute an internal GET request against that path.

``await datasette.client.post(path, **kwargs)`` - returns HTTPX Response
    Execute an internal POST request. Use ``data={"name": "value"}`` to pass form parameters.

``await datasette.client.options(path, **kwargs)`` - returns HTTPX Response
    Execute an internal OPTIONS request.

``await datasette.client.head(path, **kwargs)`` - returns HTTPX Response
    Execute an internal HEAD request.

``await datasette.client.put(path, **kwargs)`` - returns HTTPX Response
    Execute an internal PUT request.

``await datasette.client.patch(path, **kwargs)`` - returns HTTPX Response
    Execute an internal PATCH request.

``await datasette.client.delete(path, **kwargs)`` - returns HTTPX Response
    Execute an internal DELETE request.

``await datasette.client.request(method, path, **kwargs)`` - returns HTTPX Response
    Execute an internal request with the given HTTP method against that path.

These methods can be used with :ref:`internals_datasette_urls` - for example:

.. code-block:: python

    table_json = (
        await datasette.client.get(
            datasette.urls.table(
                "fixtures", "facetable", format="json"
            )
        )
    ).json()

``datasette.client`` methods automatically take the current :ref:`setting_base_url` setting into account, whether or not you use the ``datasette.urls`` family of methods to construct the path.

For documentation on available ``**kwargs`` options and the shape of the HTTPX Response object refer to the `HTTPX Async documentation <https://www.python-httpx.org/async/>`__.

.. _internals_datasette_urls:

datasette.urls
--------------

The ``datasette.urls`` object contains methods for building URLs to pages within Datasette. Plugins should use this to link to pages, since these methods take into account any :ref:`setting_base_url` configuration setting that might be in effect.

``datasette.urls.instance(format=None)``
    Returns the URL to the Datasette instance root page. This is usually ``"/"``.

``datasette.urls.path(path, format=None)``
    Takes a path and returns the full path, taking ``base_url`` into account.

    For example, ``datasette.urls.path("-/logout")`` will return the path to the logout page, which will be ``"/-/logout"`` by default or ``/prefix-path/-/logout`` if ``base_url`` is set to ``/prefix-path/``

``datasette.urls.logout()``
    Returns the URL to the logout page, usually ``"/-/logout"``

``datasette.urls.static(path)``
    Returns the URL of one of Datasette's default static assets, for example ``"/-/static/app.css"``

``datasette.urls.static_plugins(plugin_name, path)``
    Returns the URL of one of the static assets belonging to a plugin.

    ``datasette.urls.static_plugins("datasette_cluster_map", "datasette-cluster-map.js")`` would return ``"/-/static-plugins/datasette_cluster_map/datasette-cluster-map.js"``

``datasette.urls.static(path)``
    Returns the URL of one of Datasette's default static assets, for example ``"/-/static/app.css"``

``datasette.urls.database(database_name, format=None)``
    Returns the URL to a database page, for example ``"/fixtures"``

``datasette.urls.table(database_name, table_name, format=None)``
    Returns the URL to a table page, for example ``"/fixtures/facetable"``

``datasette.urls.query(database_name, query_name, format=None)``
    Returns the URL to a query page, for example ``"/fixtures/pragma_cache_size"``

These functions can be accessed via the ``{{ urls }}`` object in Datasette templates, for example:

.. code-block:: jinja

    <a href="{{ urls.instance() }}">Homepage</a>
    <a href="{{ urls.database("fixtures") }}">Fixtures database</a>
    <a href="{{ urls.table("fixtures", "facetable") }}">facetable table</a>
    <a href="{{ urls.query("fixtures", "pragma_cache_size") }}">pragma_cache_size query</a>

Use the ``format="json"`` (or ``"csv"`` or other formats supported by plugins) arguments to get back URLs to the JSON representation. This is the path with ``.json`` added on the end.

These methods each return a ``datasette.utils.PrefixedUrlString`` object, which is a subclass of the Python ``str`` type. This allows the logic that considers the ``base_url`` setting to detect if that prefix has already been applied to the path.

.. _internals_database:

Database class
==============

Instances of the ``Database`` class can be used to execute queries against attached SQLite databases, and to run introspection against their schemas.

.. _database_constructor:

Database(ds, path=None, is_mutable=True, is_memory=False, memory_name=None)
---------------------------------------------------------------------------

The ``Database()`` constructor can be used by plugins, in conjunction with :ref:`datasette_add_database`, to create and register new databases.

The arguments are as follows:

``ds`` - :ref:`internals_datasette` (required)
    The Datasette instance you are attaching this database to.

``path`` - string
    Path to a SQLite database file on disk.

``is_mutable`` - boolean
    Set this to ``False`` to cause Datasette to open the file in immutable mode.

``is_memory`` - boolean
    Use this to create non-shared memory connections.

``memory_name`` - string or ``None``
    Use this to create a named in-memory database. Unlike regular memory databases these can be accessed by multiple threads and will persist an changes made to them for the lifetime of the Datasette server process.

The first argument is the ``datasette`` instance you are attaching to, the second is a ``path=``, then ``is_mutable`` and ``is_memory`` are both optional arguments.

.. _database_hash:

db.hash
-------

If the database was opened in immutable mode, this property returns the 64 character SHA-256 hash of the database contents as a string. Otherwise it returns ``None``.

.. _database_execute:

await db.execute(sql, ...)
--------------------------

Executes a SQL query against the database and returns the resulting rows (see :ref:`database_results`).

``sql`` - string (required)
    The SQL query to execute. This can include ``?`` or ``:named`` parameters.

``params`` - list or dict
    A list or dictionary of values to use for the parameters. List for ``?``, dictionary for ``:named``.

``truncate`` - boolean
    Should the rows returned by the query be truncated at the maximum page size? Defaults to ``True``, set this to ``False`` to disable truncation.

``custom_time_limit`` - integer ms
    A custom time limit for this query. This can be set to a lower value than the Datasette configured default. If a query takes longer than this it will be terminated early and raise a ``dataette.database.QueryInterrupted`` exception.

``page_size`` - integer
    Set a custom page size for truncation, over-riding the configured Datasette default.

``log_sql_errors`` - boolean
    Should any SQL errors be logged to the console in addition to being raised as an error? Defaults to ``True``.

.. _database_results:

Results
-------

The ``db.execute()`` method returns a single ``Results`` object. This can be used to access the rows returned by the query.

Iterating over a ``Results`` object will yield SQLite `Row objects <https://docs.python.org/3/library/sqlite3.html#row-objects>`__. Each of these can be treated as a tuple or can be accessed using ``row["column"]`` syntax:

.. code-block:: python

    info = []
    results = await db.execute("select name from sqlite_master")
    for row in results:
        info.append(row["name"])

The ``Results`` object also has the following properties and methods:

``.truncated`` - boolean
    Indicates if this query was truncated - if it returned more results than the specified ``page_size``. If this is true then the results object will only provide access to the first ``page_size`` rows in the query result. You can disable truncation by passing ``truncate=False`` to the ``db.query()`` method.

``.columns`` - list of strings
    A list of column names returned by the query.

``.rows`` - list of sqlite3.Row
    This property provides direct access to the list of rows returned by the database. You can access specific rows by index using ``results.rows[0]``.

``.first()`` - row or None
    Returns the first row in the results, or ``None`` if no rows were returned.

``.single_value()``
    Returns the value of the first column of the first row of results - but only if the query returned a single row with a single column. Raises a ``datasette.database.MultipleValues`` exception otherwise.

``.__len__()``
    Calling ``len(results)`` returns the (truncated) number of returned results.

.. _database_execute_fn:

await db.execute_fn(fn)
-----------------------

Executes a given callback function against a read-only database connection running in a thread. The function will be passed a SQLite connection, and the return value from the function will be returned by the ``await``.

Example usage:

.. code-block:: python

    def get_version(conn):
        return conn.execute(
            "select sqlite_version()"
        ).fetchall()[0][0]


    version = await db.execute_fn(get_version)

.. _database_execute_write:

await db.execute_write(sql, params=None, block=True)
-----------------------------------------------------

SQLite only allows one database connection to write at a time. Datasette handles this for you by maintaining a queue of writes to be executed against a given database. Plugins can submit write operations to this queue and they will be executed in the order in which they are received.

This method can be used to queue up a non-SELECT SQL query to be executed against a single write connection to the database.

You can pass additional SQL parameters as a tuple or dictionary.

The method will block until the operation is completed, and the return value will be the return from calling ``conn.execute(...)`` using the underlying ``sqlite3`` Python library.

If you pass ``block=False`` this behaviour changes to "fire and forget" - queries will be added to the write queue and executed in a separate thread while your code can continue to do other things. The method will return a UUID representing the queued task.

.. _database_execute_write_script:

await db.execute_write_script(sql, block=True)
-----------------------------------------------

Like ``execute_write()`` but can be used to send multiple SQL statements in a single string separated by semicolons, using the ``sqlite3`` `conn.executescript() <https://docs.python.org/3/library/sqlite3.html#sqlite3.Cursor.executescript>`__ method.

.. _database_execute_write_many:

await db.execute_write_many(sql, params_seq, block=True)
---------------------------------------------------------

Like ``execute_write()`` but uses the ``sqlite3`` `conn.executemany() <https://docs.python.org/3/library/sqlite3.html#sqlite3.Cursor.executemany>`__ method. This will efficiently execute the same SQL statement against each of the parameters in the ``params_seq`` iterator, for example:

.. code-block:: python

    await db.execute_write_many(
        "insert into characters (id, name) values (?, ?)",
        [(1, "Melanie"), (2, "Selma"), (2, "Viktor")],
    )

.. _database_execute_write_fn:

await db.execute_write_fn(fn, block=True)
------------------------------------------

This method works like ``.execute_write()``, but instead of a SQL statement you give it a callable Python function. Your function will be queued up and then called when the write connection is available, passing that connection as the argument to the function.

The function can then perform multiple actions, safe in the knowledge that it has exclusive access to the single writable connection for as long as it is executing.

.. warning::

    ``fn`` needs to be a regular function, not an ``async def`` function.

For example:

.. code-block:: python

    def delete_and_return_count(conn):
        conn.execute("delete from some_table where id > 5")
        return conn.execute(
            "select count(*) from some_table"
        ).fetchone()[0]


    try:
        num_rows_left = await database.execute_write_fn(
            delete_and_return_count
        )
    except Exception as e:
        print("An error occurred:", e)

The value returned from ``await database.execute_write_fn(...)`` will be the return value from your function.

If your function raises an exception that exception will be propagated up to the ``await`` line.

If you specify ``block=False`` the method becomes fire-and-forget, queueing your function to be executed and then allowing your code after the call to ``.execute_write_fn()`` to continue running while the underlying thread waits for an opportunity to run your function. A UUID representing the queued task will be returned. Any exceptions in your code will be silently swallowed.

.. _database_close:

db.close()
----------

Closes all of the open connections to file-backed databases. This is mainly intended to be used by large test suites, to avoid hitting limits on the number of open files.

.. _internals_database_introspection:

Database introspection
----------------------

The ``Database`` class also provides properties and methods for introspecting the database.

``db.name`` - string
    The name of the database - usually the filename without the ``.db`` prefix.

``db.size`` - integer
    The size of the database file in bytes. 0 for ``:memory:`` databases.

``db.mtime_ns`` - integer or None
    The last modification time of the database file in nanoseconds since the epoch. ``None`` for ``:memory:`` databases.

``db.is_mutable`` - boolean
    Is this database mutable, and allowed to accept writes?

``db.is_memory`` - boolean
    Is this database an in-memory database?

``await db.attached_databases()`` - list of named tuples
    Returns a list of additional databases that have been connected to this database using the SQLite ATTACH command. Each named tuple has fields ``seq``, ``name`` and ``file``.

``await db.table_exists(table)`` - boolean
    Check if a table called ``table`` exists.

``await db.table_names()`` - list of strings
    List of names of tables in the database.

``await db.view_names()`` - list of strings
    List of names of views in the database.

``await db.table_columns(table)`` - list of strings
    Names of columns in a specific table.

``await db.table_column_details(table)`` - list of named tuples
    Full details of the columns in a specific table. Each column is represented by a ``Column`` named tuple with fields ``cid`` (integer representing the column position), ``name`` (string), ``type`` (string, e.g. ``REAL`` or ``VARCHAR(30)``), ``notnull`` (integer 1 or 0), ``default_value`` (string or None), ``is_pk`` (integer 1 or 0).

``await db.primary_keys(table)`` - list of strings
    Names of the columns that are part of the primary key for this table.

``await db.fts_table(table)`` - string or None
    The name of the FTS table associated with this table, if one exists.

``await db.label_column_for_table(table)`` - string or None
    The label column that is associated with this table - either automatically detected or using the ``"label_column"`` key from :ref:`metadata`, see :ref:`label_columns`.

``await db.foreign_keys_for_table(table)`` - list of dictionaries
    Details of columns in this table which are foreign keys to other tables. A list of dictionaries where each dictionary is shaped like this: ``{"column": string, "other_table": string, "other_column": string}``.

``await db.hidden_table_names()`` - list of strings
    List of tables which Datasette "hides" by default - usually these are tables associated with SQLite's full-text search feature, the SpatiaLite extension or tables hidden using the :ref:`metadata_hiding_tables` feature.

``await db.get_table_definition(table)`` - string
    Returns the SQL definition for the table - the ``CREATE TABLE`` statement and any associated ``CREATE INDEX`` statements.

``await db.get_view_definition(view)`` - string
    Returns the SQL definition of the named view.

``await db.get_all_foreign_keys()`` - dictionary
    Dictionary representing both incoming and outgoing foreign keys for this table. It has two keys, ``"incoming"`` and ``"outgoing"``, each of which is a list of dictionaries with keys ``"column"``, ``"other_table"`` and ``"other_column"``. For example:

    .. code-block:: json

        {
            "incoming": [],
            "outgoing": [
                {
                    "other_table": "attraction_characteristic",
                    "column": "characteristic_id",
                    "other_column": "pk",
                },
                {
                    "other_table": "roadside_attractions",
                    "column": "attraction_id",
                    "other_column": "pk",
                }
            ]
        }


.. _internals_csrf:

CSRF protection
===============

Datasette uses `asgi-csrf <https://github.com/simonw/asgi-csrf>`__ to guard against CSRF attacks on form POST submissions. Users receive a ``ds_csrftoken`` cookie which is compared against the ``csrftoken`` form field (or ``x-csrftoken`` HTTP header) for every incoming request.

If your plugin implements a ``<form method="POST">`` anywhere you will need to include that token. You can do so with the following template snippet:

.. code-block:: html

    <input type="hidden" name="csrftoken" value="{{ csrftoken() }}">

If you are rendering templates using the :ref:`datasette_render_template` method the ``csrftoken()`` helper will only work if you provide the ``request=`` argument to that method. If you forget to do this you will see the following error::

    form-urlencoded POST field did not match cookie

You can selectively disable CSRF protection using the :ref:`plugin_hook_skip_csrf` hook.

.. _internals_internal:

The _internal database
======================

.. warning::
    This API should be considered unstable - the structure of these tables may change prior to the release of Datasette 1.0.

Datasette maintains an in-memory SQLite database with details of the the databases, tables and columns for all of the attached databases.

By default all actors are denied access to the ``view-database`` permission for the ``_internal`` database, so the database is not visible to anyone unless they :ref:`sign in as root <authentication_root>`.

Plugins can access this database by calling ``db = datasette.get_database("_internal")`` and then executing queries using the :ref:`Database API <internals_database>`.

You can explore an example of this database by `signing in as root <https://latest.datasette.io/login-as-root>`__ to the ``latest.datasette.io`` demo instance and then navigating to `latest.datasette.io/_internal <https://latest.datasette.io/_internal>`__.

.. _internals_utils:

The datasette.utils module
==========================

The ``datasette.utils`` module contains various utility functions used by Datasette. As a general rule you should consider anything in this module to be unstable - functions and classes here could change without warning or be removed entirely between Datasette releases, without being mentioned in the release notes.

The exception to this rule is anythang that is documented here. If you find a need for an undocumented utility function in your own work, consider `opening an issue <https://github.com/simonw/datasette/issues/new>`__ requesting that the function you are using be upgraded to documented and supported status.

.. _internals_utils_parse_metadata:

parse_metadata(content)
-----------------------

This function accepts a string containing either JSON or YAML, expected to be of the format described in :ref:`metadata`. It returns a nested Python dictionary representing the parsed data from that string.

If the metadata cannot be parsed as either JSON or YAML the function will raise a ``utils.BadMetadataError`` exception.

.. autofunction:: datasette.utils.parse_metadata

.. _internals_utils_await_me_maybe:

await_me_maybe(value)
---------------------

Utility function for calling ``await`` on a return value if it is awaitable, otherwise returning the value. This is used by Datasette to support plugin hooks that can optionally return awaitable functions. Read more about this function in `The “await me maybe” pattern for Python asyncio <https://simonwillison.net/2020/Sep/2/await-me-maybe/>`__.

.. autofunction:: datasette.utils.await_me_maybe

.. _internals_tilde_encoding:

Tilde encoding
--------------

Datasette uses a custom encoding scheme in some places, called **tilde encoding**. This is primarily used for table names and row primary keys, to avoid any confusion between ``/`` characters in those values and the Datasette URLs that reference them.

Tilde encoding uses the same algorithm as `URL percent-encoding <https://developer.mozilla.org/en-US/docs/Glossary/percent-encoding>`__, but with the ``~`` tilde character used in place of ``%``.

Any character other than ``ABCDEFGHIJKLMNOPQRSTUVWXYZ abcdefghijklmnopqrstuvwxyz0123456789_-`` will be replaced by the numeric equivalent preceded by a tilde. For example:

- ``/`` becomes ``~2F``
- ``.`` becomes ``~2E``
- ``%`` becomes ``~25``
- ``~`` becomes ``~7E``
- Space becomes ``+``
- ``polls/2022.primary`` becomes ``polls~2F2022~2Eprimary``

Note that the space character is a special case: it will be replaced with a ``+`` symbol.

.. _internals_utils_tilde_encode:

.. autofunction:: datasette.utils.tilde_encode

.. _internals_utils_tilde_decode:

.. autofunction:: datasette.utils.tilde_decode

.. _internals_tracer:

datasette.tracer
================

Running Datasette with ``--setting trace_debug 1`` enables trace debug output, which can then be viewed by adding ``?_trace=1`` to the query string for any page.

You can see an example of this at the bottom of `latest.datasette.io/fixtures/facetable?_trace=1 <https://latest.datasette.io/fixtures/facetable?_trace=1>`__. The JSON output shows full details of every SQL query that was executed to generate the page.

The `datasette-pretty-traces <https://datasette.io/plugins/datasette-pretty-traces>`__ plugin can be installed to provide a more readable display of this information. You can see `a demo of that here <https://latest-with-plugins.datasette.io/github/commits?_trace=1>`__.

You can add your own custom traces to the JSON output using the ``trace()`` context manager. This takes a string that identifies the type of trace being recorded, and records any keyword arguments as additional JSON keys on the resulting trace object.

The start and end time, duration and a traceback of where the trace was executed will be automatically attached to the JSON object.

This example uses trace to record the start, end and duration of any HTTP GET requests made using the function:

.. code-block:: python

    from datasette.tracer import trace
    import httpx


    async def fetch_url(url):
        with trace("fetch-url", url=url):
            async with httpx.AsyncClient() as client:
                return await client.get(url)

.. _internals_tracer_trace_child_tasks:

Tracing child tasks
-------------------

If your code uses a mechanism such as ``asyncio.gather()`` to execute code in additional tasks you may find that some of the traces are missing from the display.

You can use the ``trace_child_tasks()`` context manager to ensure these child tasks are correctly handled.

.. code-block:: python

    from datasette import tracer

    with tracer.trace_child_tasks():
        results = await asyncio.gather(
            # ... async tasks here
        )

This example uses the :ref:`register_routes() <plugin_register_routes>` plugin hook to add a page at ``/parallel-queries`` which executes two SQL queries in parallel using ``asyncio.gather()`` and returns their results.

.. code-block:: python

    from datasette import hookimpl
    from datasette import tracer


    @hookimpl
    def register_routes():
        async def parallel_queries(datasette):
            db = datasette.get_database()
            with tracer.trace_child_tasks():
                one, two = await asyncio.gather(
                    db.execute("select 1"),
                    db.execute("select 2"),
                )
            return Response.json(
                {
                    "one": one.single_value(),
                    "two": two.single_value(),
                }
            )

        return [
            (r"/parallel-queries$", parallel_queries),
        ]


Adding ``?_trace=1`` will show that the trace covers both of those child tasks.

.. _internals_shortcuts:

Import shortcuts
================

The following commonly used symbols can be imported directly from the ``datasette`` module:

.. code-block:: python

    from datasette import Response
    from datasette import Forbidden
    from datasette import NotFound
    from datasette import hookimpl
    from datasette import actor_matches_allow
