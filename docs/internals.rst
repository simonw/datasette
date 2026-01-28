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

The object also has the following awaitable methods:

``await request.form(files=False, ...)`` - FormData
    Parses form data from the request body. Supports both ``application/x-www-form-urlencoded`` and ``multipart/form-data`` content types.

    Returns a :ref:`internals_formdata` object with dict-like access to form fields and uploaded files.

    Requirements and errors:

    - A ``Content-Type`` header is required. Missing or unsupported content types raise ``BadRequest``.
    - For ``multipart/form-data``, the ``boundary=...`` parameter is required.

    Parameters:

    - ``files`` (bool, default ``False``): If ``True``, uploaded files are stored and accessible. If ``False`` (default), file content is discarded but form fields are still available.
    - ``max_file_size`` (int, default 50MB): Maximum size per uploaded file in bytes.
    - ``max_request_size`` (int, default 100MB): Maximum total request body size in bytes.
    - ``max_fields`` (int, default 1000): Maximum number of form fields.
    - ``max_files`` (int, default 100): Maximum number of uploaded files.
    - ``max_parts`` (int, default ``max_fields + max_files``): Maximum number of multipart parts in total.
    - ``max_field_size`` (int, default 100KB): Maximum size of a text field value in bytes.
    - ``max_memory_file_size`` (int, default 1MB): File size threshold before uploads spill to disk.
    - ``max_part_header_bytes`` (int, default 16KB): Maximum total bytes allowed in part headers.
    - ``max_part_header_lines`` (int, default 100): Maximum header lines per part.
    - ``min_free_disk_bytes`` (int, default 50MB): Minimum free bytes required in the temp directory before accepting file uploads.

    Example usage:

    .. code-block:: python

        # Parse form fields only (files are discarded)
        form = await request.form()
        username = form["username"]
        tags = form.getlist("tags")  # For multiple values

        # Parse form fields AND files
        form = await request.form(files=True)
        uploaded = form["avatar"]
        content = await uploaded.read()
        print(
            uploaded.filename, uploaded.content_type, uploaded.size
        )

    Cleanup note:

    When using ``files=True``, call ``await form.aclose()`` once you are done with the uploads
    to ensure spooled temporary files are closed promptly. You can also use
    ``async with form: ...`` for automatic cleanup.

    Don't forget to read about :ref:`internals_csrf`!

``await request.post_vars()`` - dictionary
    Returns a dictionary of form variables that were submitted in the request body via ``POST`` using ``application/x-www-form-urlencoded`` encoding. For multipart forms or file uploads, use ``request.form()`` instead.

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

.. _internals_formdata:

The FormData class
==================

``await request.form()`` returns a ``FormData`` object - a dictionary-like object which provides access to form fields and uploaded files. It has a similar interface to ``MultiParams``.

``form[key]`` - string or UploadedFile
    Returns the first value for that key, or raises a ``KeyError`` if the key is missing.

``form.get(key)`` - string, UploadedFile, or None
    Returns the first value for that key, or ``None`` if the key is missing. Pass a second argument to specify a different default.

``form.getlist(key)`` - list
    Returns the list of values for that key. If the key is missing an empty list will be returned.

``form.keys()`` - list of strings
    Returns the list of available keys.

``key in form`` - True or False
    You can use ``if key in form`` to check if a key is present.

``for key in form`` - iterator
    This lets you loop through every available key.

``len(form)`` - integer
    Returns the total number of submitted values.

.. _internals_uploadedfile:

The UploadedFile class
======================

When parsing multipart form data with ``files=True``, file uploads are returned as ``UploadedFile`` objects with the following properties and methods:

``uploaded_file.name`` - string
    The form field name.

``uploaded_file.filename`` - string
    The original filename provided by the client. Note: This is sanitized to remove path components for security.

``uploaded_file.content_type`` - string or None
    The MIME type of the uploaded file, if provided by the client.

``uploaded_file.size`` - integer
    The size of the uploaded file in bytes.

``await uploaded_file.read(size=-1)`` - bytes
    Read and return up to ``size`` bytes from the file. If ``size`` is -1 (default), read the entire file.

``await uploaded_file.seek(offset, whence=0)`` - integer
    Seek to the given position in the file. Returns the new position.

``await uploaded_file.close()``
    Close the underlying file. This is called automatically when the object is garbage collected.

Files smaller than 1MB are stored in memory. Larger files are automatically spilled to temporary files on disk and cleaned up when the request completes.

Example:

.. code-block:: python

    form = await request.form(files=True)
    uploaded = form["document"]

    # Check file metadata
    print(f"Filename: {uploaded.filename}")
    print(f"Content-Type: {uploaded.content_type}")
    print(f"Size: {uploaded.size} bytes")

    # Read file content
    content = await uploaded.read()

    # Or read in chunks
    await uploaded.seek(0)
    while chunk := await uploaded.read(8192):
        process_chunk(chunk)

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

All databases are listed, irrespective of user permissions.

.. _datasette_actions:

.actions
--------

Property exposing a dictionary of actions that have been registered using the :ref:`plugin_register_actions` plugin hook.

The dictionary keys are the action names - e.g. ``view-instance`` - and the values are ``Action()`` objects describing the permission.

.. _datasette_plugin_config:

.plugin_config(plugin_name, database=None, table=None)
------------------------------------------------------

``plugin_name`` - string
    The name of the plugin to look up configuration for. Usually this is something similar to ``datasette-cluster-map``.

``database`` - None or string
    The database the user is interacting with.

``table`` - None or string
    The table the user is interacting with.

This method lets you read plugin configuration values that were set in  ``datasette.yaml``. See :ref:`writing_plugins_configuration` for full details of how this method should be used.

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

.. _datasette_actors_from_ids:

await .actors_from_ids(actor_ids)
---------------------------------

``actor_ids`` - list of strings or integers
    A list of actor IDs to look up.

Returns a dictionary, where the keys are the IDs passed to it and the values are the corresponding actor dictionaries.

This method is mainly designed to be used with plugins. See the :ref:`plugin_hook_actors_from_ids` documentation for details.

If no plugins that implement that hook are installed, the default return value looks like this:

.. code-block:: json

    {
        "1": {"id": "1"},
        "2": {"id": "2"}
    }

.. _datasette_allowed:

await .allowed(\*, action, resource, actor=None)
------------------------------------------------

``action`` - string
    The name of the action that is being permission checked.

``resource`` - Resource object
    A Resource object representing the database, table, or other resource. Must be an instance of a Resource class such as ``TableResource``, ``DatabaseResource``, ``QueryResource``, or ``InstanceResource``.

``actor`` - dictionary, optional
    The authenticated actor. This is usually ``request.actor``. Defaults to ``None`` for unauthenticated requests.

This method checks if the given actor has permission to perform the given action on the given resource. All parameters must be passed as keyword arguments.

Example usage:

.. code-block:: python

    from datasette.resources import (
        TableResource,
        DatabaseResource,
    )

    # Check if actor can view a specific table
    can_view = await datasette.allowed(
        action="view-table",
        resource=TableResource(
            database="fixtures", table="facetable"
        ),
        actor=request.actor,
    )

    # Check if actor can execute SQL on a database
    can_execute = await datasette.allowed(
        action="execute-sql",
        resource=DatabaseResource(database="fixtures"),
        actor=request.actor,
    )

The method returns ``True`` if the permission is granted, ``False`` if denied.

.. _datasette_allowed_resources:

await .allowed_resources(action, actor=None, \*, parent=None, include_is_private=False, include_reasons=False, limit=100, next=None)
------------------------------------------------------------------------------------------------------------------------------------

Returns a ``PaginatedResources`` object containing resources that the actor can access for the specified action, with support for keyset pagination.

``action`` - string
    The action name (e.g., "view-table", "view-database")

``actor`` - dictionary, optional
    The authenticated actor. Defaults to ``None`` for unauthenticated requests.

``parent`` - string, optional
    Optional parent filter (e.g., database name) to limit results

``include_is_private`` - boolean, optional
    If True, adds a ``.private`` attribute to each Resource indicating whether anonymous users can access it

``include_reasons`` - boolean, optional
    If True, adds a ``.reasons`` attribute with a list of strings describing why access was granted (useful for debugging)

``limit`` - integer, optional
    Maximum number of results to return per page (1-1000, default 100)

``next`` - string, optional
    Keyset token from a previous page for pagination

The method returns a ``PaginatedResources`` object (from ``datasette.utils``) with the following attributes:

``resources`` - list
    List of ``Resource`` objects for the current page

``next`` - string or None
    Token for the next page, or ``None`` if no more results exist

Example usage:

.. code-block:: python

    # Get first page of tables
    page = await datasette.allowed_resources(
        "view-table",
        actor=request.actor,
        parent="fixtures",
        limit=50,
    )

    for table in page.resources:
        print(table.parent, table.child)
        if hasattr(table, "private"):
            print(f"  Private: {table.private}")

    # Get next page if available
    if page.next:
        next_page = await datasette.allowed_resources(
            "view-table", actor=request.actor, next=page.next
        )

    # Iterate through all results automatically
    page = await datasette.allowed_resources(
        "view-table", actor=request.actor
    )
    async for table in page.all():
        print(table.parent, table.child)

    # With reasons for debugging
    page = await datasette.allowed_resources(
        "view-table", actor=request.actor, include_reasons=True
    )
    for table in page.resources:
        print(f"{table.child}: {table.reasons}")

The ``page.all()`` async generator automatically handles pagination, fetching additional pages and yielding all resources one at a time.

This method uses :ref:`datasette_allowed_resources_sql` under the hood and is an efficient way to list the databases, tables or other resources that an actor can access for a specific action.

.. _datasette_allowed_resources_sql:

await .allowed_resources_sql(\*, action, actor=None, parent=None, include_is_private=False)
-------------------------------------------------------------------------------------------

Builds the SQL query that Datasette uses to determine which resources an actor may access for a specific action. Returns a ``(sql: str, params: dict)`` namedtuple that can be executed against the internal ``catalog_*`` database tables. ``parent`` can be used to limit results to a specific database, and ``include_is_private`` adds a column indicating whether anonymous users would be denied access to that resource.

Plugins that need to execute custom analysis over the raw allow/deny rules can use this helper to run the same query that powers the ``/-/allowed`` debugging interface.

The SQL query built by this method will return the following columns:

- ``parent``: The parent resource identifier (or NULL)
- ``child``: The child resource identifier (or NULL)
- ``reason``: The reason from the rule that granted access
- ``is_private``: (if ``include_is_private``) 1 if anonymous users cannot access, 0 otherwise

.. _datasette_ensure_permission:

await .ensure_permission(action, resource=None, actor=None)
-----------------------------------------------------------

``action`` - string
    The action to check. See :ref:`actions` for a list of available actions.

``resource`` - Resource object (optional)
    The resource to check the permission against. Must be an instance of ``InstanceResource``, ``DatabaseResource``, or ``TableResource`` from the ``datasette.resources`` module. If omitted, defaults to ``InstanceResource()`` for instance-level permissions.

``actor`` - dictionary (optional)
    The authenticated actor. This is usually ``request.actor``.

This is a convenience wrapper around :ref:`datasette_allowed` that raises a ``datasette.Forbidden`` exception if the permission check fails. Use this when you want to enforce a permission check and halt execution if the actor is not authorized.

Example:

.. code-block:: python

    from datasette.resources import TableResource

    # Will raise Forbidden if actor cannot view the table
    await datasette.ensure_permission(
        action="view-table",
        resource=TableResource(
            database="fixtures", table="cities"
        ),
        actor=request.actor,
    )

    # For instance-level actions, resource can be omitted:
    await datasette.ensure_permission(
        action="permissions-debug", actor=request.actor
    )

.. _datasette_check_visibility:

await .check_visibility(actor, action, resource=None)
-----------------------------------------------------

``actor`` - dictionary
    The authenticated actor. This is usually ``request.actor``.

``action`` - string
    The name of the action that is being permission checked.

``resource`` - Resource object, optional
    The resource being checked, as a Resource object such as ``DatabaseResource(database=...)``, ``TableResource(database=..., table=...)``, or ``QueryResource(database=..., query=...)``. Only some permissions apply to a resource.

This convenience method can be used to answer the question "should this item be considered private, in that it is visible to me but it is not visible to anonymous users?"

It returns a tuple of two booleans, ``(visible, private)``. ``visible`` indicates if the actor can see this resource. ``private`` will be ``True`` if an anonymous user would not be able to view the resource.

This example checks if the user can access a specific table, and sets ``private`` so that a padlock icon can later be displayed:

.. code-block:: python

    from datasette.resources import TableResource

    visible, private = await datasette.check_visibility(
        request.actor,
        action="view-table",
        resource=TableResource(database=database, table=table),
    )

.. _datasette_create_token:

.create_token(actor_id, expires_after=None, restrict_all=None, restrict_database=None, restrict_resource=None)
--------------------------------------------------------------------------------------------------------------

``actor_id`` - string
    The ID of the actor to create a token for.

``expires_after`` - int, optional
    The number of seconds after which the token should expire.

``restrict_all`` - iterable, optional
    A list of actions that this token should be restricted to across all databases and resources.

``restrict_database`` - dict, optional
    For restricting actions within specific databases, e.g. ``{"mydb": ["view-table", "view-query"]}``.

``restrict_resource`` - dict, optional
    For restricting actions to specific resources (tables, SQL views and :ref:`canned_queries`) within a database. For example: ``{"mydb": {"mytable": ["insert-row", "update-row"]}}``.

This method returns a signed :ref:`API token <CreateTokenView>` of the format ``dstok_...`` which can be used to authenticate requests to the Datasette API.

All tokens must have an ``actor_id`` string indicating the ID of the actor which the token will act on behalf of.

Tokens default to lasting forever, but can be set to expire after a given number of seconds using the ``expires_after`` argument. The following code creates a token for ``user1`` that will expire after an hour:

.. code-block:: python

    token = datasette.create_token(
        actor_id="user1",
        expires_after=3600,
    )

The three ``restrict_*`` arguments can be used to create a token that has additional restrictions beyond what the associated actor is allowed to do.

The following example creates a token that can access ``view-instance`` and ``view-table`` across everything, can additionally use ``view-query`` for anything in the ``docs`` database and is allowed to execute ``insert-row`` and ``update-row`` in the ``attachments`` table in that database:

.. code-block:: python

    token = datasette.create_token(
        actor_id="user1",
        restrict_all=("view-instance", "view-table"),
        restrict_database={"docs": ("view-query",)},
        restrict_resource={
            "docs": {
                "attachments": ("insert-row", "update-row")
            }
        },
    )

.. _datasette_get_database:

.get_database(name)
-------------------

``name`` - string, optional
    The name of the database - optional.

Returns the specified database object. Raises a ``KeyError`` if the database does not exist. Call this method without an argument to return the first connected database.

.. _get_internal_database:

.get_internal_database()
------------------------

Returns a database object for reading and writing to the private :ref:`internal database <internals_internal>`.

.. _datasette_get_set_metadata:

Getting and setting metadata
----------------------------

Metadata about the instance, databases, tables and columns is stored in tables in :ref:`internals_internal`. The following methods are the supported API for plugins to read and update that stored metadata.

.. _datasette_get_instance_metadata:

await .get_instance_metadata(self)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Returns metadata keys and values for the entire Datasette instance as a dictionary.
Internally queries the ``metadata_instance`` table inside the :ref:`internal database <internals_internal>`.

.. _datasette_get_database_metadata:

await .get_database_metadata(self, database_name)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``database_name`` - string
    The name of the database to query.

Returns metadata keys and values for the specified database as a dictionary.
Internally queries the ``metadata_databases`` table inside the :ref:`internal database <internals_internal>`.

.. _datasette_get_resource_metadata:

await .get_resource_metadata(self, database_name, resource_name)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``database_name`` - string
    The name of the database to query.
``resource_name`` - string
    The name of the resource (table, view, or canned query) inside ``database_name`` to query.

Returns metadata keys and values for the specified "resource" as a dictionary.
A "resource" in this context can be a table, view, or canned query.
Internally queries the ``metadata_resources`` table inside the :ref:`internal database <internals_internal>`.

.. _datasette_get_column_metadata:

await .get_column_metadata(self, database_name, resource_name, column_name)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``database_name`` - string
    The name of the database to query.
``resource_name`` - string
    The name of the resource (table, view, or canned query) inside ``database_name`` to query.
``column_name`` - string
    The name of the column inside ``resource_name`` to query.


Returns metadata keys and values for the specified column, resource, and table as a dictionary.
Internally queries the ``metadata_columns`` table inside the :ref:`internal database <internals_internal>`.

.. _datasette_set_instance_metadata:

await .set_instance_metadata(self, key, value)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``key`` - string
    The metadata entry key to insert (ex ``title``, ``description``, etc.)
``value`` - string
    The value of the metadata entry to insert.

Adds a new metadata entry for the entire Datasette instance.
Any previous instance-level metadata entry with the same ``key`` will be overwritten.
Internally upserts the value into the  the ``metadata_instance`` table inside the :ref:`internal database <internals_internal>`.

.. _datasette_set_database_metadata:

await .set_database_metadata(self, database_name, key, value)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``database_name`` - string
    The database the metadata entry belongs to.
``key`` - string
    The metadata entry key to insert (ex ``title``, ``description``, etc.)
``value`` - string
    The value of the metadata entry to insert.

Adds a new metadata entry for the specified database.
Any previous database-level metadata entry with the same ``key`` will be overwritten.
Internally upserts the value into the  the ``metadata_databases`` table inside the :ref:`internal database <internals_internal>`.

.. _datasette_set_resource_metadata:

await .set_resource_metadata(self, database_name, resource_name, key, value)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``database_name`` - string
    The database the metadata entry belongs to.
``resource_name`` - string
    The resource (table, view, or canned query) the metadata entry belongs to.
``key`` - string
    The metadata entry key to insert (ex ``title``, ``description``, etc.)
``value`` - string
    The value of the metadata entry to insert.

Adds a new metadata entry for the specified "resource".
Any previous resource-level metadata entry with the same ``key`` will be overwritten.
Internally upserts the value into the  the ``metadata_resources`` table inside the :ref:`internal database <internals_internal>`.

.. _datasette_set_column_metadata:

await .set_column_metadata(self, database_name, resource_name, column_name, key, value)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``database_name`` - string
    The database the metadata entry belongs to.
``resource_name`` - string
    The resource (table, view, or canned query) the metadata entry belongs to.
``column-name`` - string
    The column the metadata entry belongs to.
``key`` - string
    The metadata entry key to insert (ex ``title``, ``description``, etc.)
``value`` - string
    The value of the metadata entry to insert.

Adds a new metadata entry for the specified column.
Any previous column-level metadata entry with the same ``key`` will be overwritten.
Internally upserts the value into the  the ``metadata_columns`` table inside the :ref:`internal database <internals_internal>`.

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

.add_memory_database(memory_name, name=None, route=None)
--------------------------------------------------------

Adds a shared in-memory database with the specified name:

.. code-block:: python

    datasette.add_memory_database("statistics")

This is a shortcut for the following:

.. code-block:: python

    from datasette.database import Database

    datasette.add_database(
        Database(datasette, memory_name="statistics")
    )

Using either of these patterns will result in the in-memory database being served at ``/statistics``.

The ``name`` and ``route`` parameters are optional and work the same way as they do for :ref:`datasette_add_database`.

.. _datasette_remove_database:

.remove_database(name)
----------------------

``name`` - string
    The name of the database to be removed.

This removes a database that has been previously added. ``name=`` is the unique name of that database.

.. _datasette_track_event:

await .track_event(event)
-------------------------

``event`` - ``Event``
    An instance of a subclass of ``datasette.events.Event``.

Plugins can call this to track events, using classes they have previously registered. See :ref:`plugin_event_tracking` for details.

The event will then be passed to all plugins that have registered to receive events using the :ref:`plugin_hook_track_event` hook.

Example usage, assuming the plugin has previously registered the ``BanUserEvent`` class:

.. code-block:: python

    await datasette.track_event(
        BanUserEvent(user={"id": 1, "username": "cleverbot"})
    )

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

.. _datasette_resolve_database:

.resolve_database(request)
--------------------------

``request`` - :ref:`internals_request`
    A request object

If you are implementing your own custom views, you may need to resolve the database that the user is requesting based on a URL path. If the regular expression for your route declares a ``database`` named group, you can use this method to resolve the database object.

This returns a :ref:`Database <internals_database>` instance.

If the database cannot be found, it raises a ``datasette.utils.asgi.DatabaseNotFound`` exception - which is a subclass of ``datasette.utils.asgi.NotFound`` with a ``.database_name`` attribute set to the name of the database that was requested.

.. _datasette_resolve_table:

.resolve_table(request)
-----------------------

``request`` - :ref:`internals_request`
    A request object

This assumes that the regular expression for your route declares both a ``database`` and a ``table`` named group.

It returns a ``ResolvedTable`` named tuple instance with the following fields:

``db`` - :ref:`Database <internals_database>`
    The database object

``table`` - string
    The name of the table (or view)

``is_view`` - boolean
    ``True`` if this is a view, ``False`` if it is a table

If the database or table cannot be found it raises a ``datasette.utils.asgi.DatabaseNotFound`` exception.

If the table does not exist it raises a ``datasette.utils.asgi.TableNotFound`` exception - a subclass of ``datasette.utils.asgi.NotFound`` with ``.database_name`` and ``.table`` attributes.

.. _datasette_resolve_row:

.resolve_row(request)
---------------------

``request`` - :ref:`internals_request`
    A request object

This method assumes your route declares named groups for ``database``, ``table`` and ``pks``.

It returns a ``ResolvedRow`` named tuple instance with the following fields:

``db`` - :ref:`Database <internals_database>`
    The database object

``table`` - string
    The name of the table

``sql`` - string
    SQL snippet that can be used in a ``WHERE`` clause to select the row

``params`` - dict
    Parameters that should be passed to the SQL query

``pks`` - list
    List of primary key column names

``pk_values`` - list
    List of primary key values decoded from the URL

``row`` - ``sqlite3.Row``
    The row itself

If the database or table cannot be found it raises a ``datasette.utils.asgi.DatabaseNotFound`` exception.

If the table does not exist it raises a ``datasette.utils.asgi.TableNotFound`` exception.

If the row cannot be found it raises a ``datasette.utils.asgi.RowNotFound`` exception. This has ``.database_name``, ``.table`` and ``.pk_values`` attributes, extracted from the request path.

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

Bypassing permission checks
~~~~~~~~~~~~~~~~~~~~~~~~~~~

All ``datasette.client`` methods accept an optional ``skip_permission_checks=True`` parameter. When set, all permission checks will be bypassed for that request, allowing access to any resource regardless of the configured permissions.

This is useful for plugins and internal operations that need to access all resources without being subject to permission restrictions.

Example usage:

.. code-block:: python

    # Regular request - respects permissions
    response = await datasette.client.get(
        "/private-db/secret-table.json"
    )
    # May return 403 Forbidden if access is denied

    # With skip_permission_checks - bypasses all permission checks
    response = await datasette.client.get(
        "/private-db/secret-table.json",
        skip_permission_checks=True,
    )
    # Will return 200 OK and the data, regardless of permissions

This parameter works with all HTTP methods (``get``, ``post``, ``put``, ``patch``, ``delete``, ``options``, ``head``) and the generic ``request`` method.

.. warning::

    Use ``skip_permission_checks=True`` with caution. It completely bypasses Datasette's permission system and should only be used in trusted plugin code or internal operations where you need guaranteed access to resources.

.. _internals_datasette_is_client:

Detecting internal client requests
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``datasette.in_client()`` - returns bool
    Returns ``True`` if the current code is executing within a ``datasette.client`` request, ``False`` otherwise.

This method is useful for plugins that need to behave differently when called through ``datasette.client`` versus when handling external HTTP requests.

Example usage:

.. code-block:: python

    async def fetch_documents(datasette):
        if not datasette.in_client():
            return Response.text(
                "Only available via internal client requests",
                status=403,
            )
        ...

Note that ``datasette.in_client()`` is independent of ``skip_permission_checks``. A request made through ``datasette.client`` will always have ``in_client()`` return ``True``, regardless of whether ``skip_permission_checks`` is set.

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

.. _internals_permission_classes:

Permission classes and utilities
================================

.. _internals_permission_sql:

PermissionSQL class
-------------------

The ``PermissionSQL`` class is used by plugins to contribute SQL-based permission rules through the :ref:`plugin_hook_permission_resources_sql` hook. This enables efficient permission checking across multiple resources by leveraging SQLite's query engine.

.. code-block:: python

    from datasette.permissions import PermissionSQL


    @dataclass
    class PermissionSQL:
        source: str  # Plugin name for auditing
        sql: str  # SQL query returning permission rules
        params: Dict[str, Any]  # Parameters for the SQL query

**Attributes:**

``source`` - string
    An identifier for the source of these permission rules, typically the plugin name. This is used for debugging and auditing.

``sql`` - string
    A SQL query that returns permission rules. The query must return rows with the following columns:

    - ``parent`` (TEXT or NULL) - The parent resource identifier (e.g., database name)
    - ``child`` (TEXT or NULL) - The child resource identifier (e.g., table name)
    - ``allow`` (INTEGER) - 1 for allow, 0 for deny
    - ``reason`` (TEXT) - A human-readable explanation of why this permission was granted or denied

``params`` - dictionary
    A dictionary of parameters to bind into the SQL query. Parameter names should not include the ``:`` prefix.

.. _permission_sql_parameters:

Available SQL parameters
~~~~~~~~~~~~~~~~~~~~~~~~

When writing SQL for ``PermissionSQL``, the following parameters are automatically available:

``:actor`` - JSON string or NULL
    The full actor dictionary serialized as JSON. Use SQLite's ``json_extract()`` function to access fields:

    .. code-block:: sql

        json_extract(:actor, '$.role') = 'admin'
        json_extract(:actor, '$.team') = 'engineering'

``:actor_id`` - string or NULL
    The actor's ``id`` field, for simple equality comparisons:

    .. code-block:: sql

        :actor_id = 'alice'

``:action`` - string
    The action being checked (e.g., ``"view-table"``, ``"insert-row"``, ``"execute-sql"``).

**Example usage:**

Here's an example plugin that grants view-table permissions to users with an "analyst" role for tables in the "analytics" database:

.. code-block:: python

    from datasette import hookimpl
    from datasette.permissions import PermissionSQL


    @hookimpl
    def permission_resources_sql(datasette, actor, action):
        if action != "view-table":
            return None

        return PermissionSQL(
            source="my_analytics_plugin",
            sql="""
                SELECT 'analytics' AS parent,
                       NULL AS child,
                       1 AS allow,
                       'Analysts can view analytics database' AS reason
                WHERE json_extract(:actor, '$.role') = 'analyst'
                  AND :action = 'view-table'
            """,
            params={},
        )

A more complex example that uses custom parameters:

.. code-block:: python

    @hookimpl
    def permission_resources_sql(datasette, actor, action):
        if not actor:
            return None

        user_teams = actor.get("teams", [])

        return PermissionSQL(
            source="team_permissions_plugin",
            sql="""
                SELECT
                    team_database AS parent,
                    team_table AS child,
                    1 AS allow,
                    'User is member of team: ' || team_name AS reason
                FROM team_permissions
                WHERE user_id = :user_id
                  AND :action IN ('view-table', 'insert-row', 'update-row')
            """,
            params={"user_id": actor.get("id")},
        )

**Permission resolution rules:**

When multiple ``PermissionSQL`` objects return conflicting rules for the same resource, Datasette applies the following precedence:

1. **Specificity**: Child-level rules (with both ``parent`` and ``child``) override parent-level rules (with only ``parent``), which override root-level rules (with neither ``parent`` nor ``child``)
2. **Deny over allow**: At the same specificity level, deny (``allow=0``) takes precedence over allow (``allow=1``)
3. **Implicit deny**: If no rules match a resource, access is denied by default

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

``.rows`` - list of ``sqlite3.Row``
    This property provides direct access to the list of rows returned by the database. You can access specific rows by index using ``results.rows[0]``.

``.dicts()`` - list of ``dict``
    This method returns a list of Python dictionaries, one for each row.

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
----------------------------------------------------

SQLite only allows one database connection to write at a time. Datasette handles this for you by maintaining a queue of writes to be executed against a given database. Plugins can submit write operations to this queue and they will be executed in the order in which they are received.

This method can be used to queue up a non-SELECT SQL query to be executed against a single write connection to the database.

You can pass additional SQL parameters as a tuple or dictionary.

The method will block until the operation is completed, and the return value will be the return from calling ``conn.execute(...)`` using the underlying ``sqlite3`` Python library.

If you pass ``block=False`` this behavior changes to "fire and forget" - queries will be added to the write queue and executed in a separate thread while your code can continue to do other things. The method will return a UUID representing the queued task.

Each call to ``execute_write()`` will be executed inside a transaction.

.. _database_execute_write_script:

await db.execute_write_script(sql, block=True)
----------------------------------------------

Like ``execute_write()`` but can be used to send multiple SQL statements in a single string separated by semicolons, using the ``sqlite3`` `conn.executescript() <https://docs.python.org/3/library/sqlite3.html#sqlite3.Cursor.executescript>`__ method.

Each call to ``execute_write_script()`` will be executed inside a transaction.

.. _database_execute_write_many:

await db.execute_write_many(sql, params_seq, block=True)
--------------------------------------------------------

Like ``execute_write()`` but uses the ``sqlite3`` `conn.executemany() <https://docs.python.org/3/library/sqlite3.html#sqlite3.Cursor.executemany>`__ method. This will efficiently execute the same SQL statement against each of the parameters in the ``params_seq`` iterator, for example:

.. code-block:: python

    await db.execute_write_many(
        "insert into characters (id, name) values (?, ?)",
        [(1, "Melanie"), (2, "Selma"), (2, "Viktor")],
    )

Each call to ``execute_write_many()`` will be executed inside a transaction.

.. _database_execute_write_fn:

await db.execute_write_fn(fn, block=True, transaction=True)
-----------------------------------------------------------

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

By default your function will be executed inside a transaction. You can pass ``transaction=False`` to disable this behavior, though if you do that you should be careful to manually apply transactions - ideally using the ``with conn:`` pattern, or you may see ``OperationalError: database table is locked`` errors.

If you specify ``block=False`` the method becomes fire-and-forget, queueing your function to be executed and then allowing your code after the call to ``.execute_write_fn()`` to continue running while the underlying thread waits for an opportunity to run your function. A UUID representing the queued task will be returned. Any exceptions in your code will be silently swallowed.

.. _database_execute_isolated_fn:

await db.execute_isolated_fn(fn)
--------------------------------

This method works is similar to :ref:`execute_write_fn() <database_execute_write_fn>` but executes the provided function in an entirely isolated SQLite connection, which is opened, used and then closed again in a single call to this method.

The :ref:`prepare_connection() <plugin_hook_prepare_connection>` plugin hook is not executed against this connection.

This allows plugins to execute database operations that might conflict with how database connections are usually configured. For example, running a ``VACUUM`` operation while bypassing any restrictions placed by the `datasette-sqlite-authorizer <https://github.com/datasette/datasette-sqlite-authorizer>`__ plugin.

Plugins can also use this method to load potentially dangerous SQLite extensions, use them to perform an operation and then have them safely unloaded at the end of the call, without risk of exposing them to other connections.

Functions run using ``execute_isolated_fn()`` share the same queue as ``execute_write_fn()``, which guarantees that no writes can be executed at the same time as the isolated function is executing.

The return value of the function will be returned by this method. Any exceptions raised by the function will be raised out of the ``await`` line as well.

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

``await db.view_exists(view)`` - boolean
    Check if a view called ``view`` exists.

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
    Dictionary representing both incoming and outgoing foreign keys for every table in this database. Each key is a table name that points to a dictionary with two keys, ``"incoming"`` and ``"outgoing"``, each of which is a list of dictionaries with keys ``"column"``, ``"other_table"`` and ``"other_column"``. For example:

    .. code-block:: json

        {
          "documents": {
            "incoming": [
              {
                "other_table": "pages",
                "column": "id",
                "other_column": "document_id"
              }
            ],
            "outgoing": []
          },
          "pages": {
            "incoming": [
              {
                "other_table": "organization_pages",
                "column": "id",
                "other_column": "page_id"
              }
            ],
            "outgoing": [
              {
                "other_table": "documents",
                "column": "document_id",
                "other_column": "id"
              }
            ]
          },
          "organization": {
            "incoming": [
              {
                "other_table": "organization_pages",
                "column": "id",
                "other_column": "organization_id"
              }
            ],
            "outgoing": []
          },
          "organization_pages": {
            "incoming": [],
            "outgoing": [
              {
                "other_table": "pages",
                "column": "page_id",
                "other_column": "id"
              },
              {
                "other_table": "organization",
                "column": "organization_id",
                "other_column": "id"
              }
            ]
          }
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

Datasette's internal database
=============================

Datasette maintains an "internal" SQLite database used for configuration, caching, and storage. Plugins can store configuration, settings, and other data inside this database. By default, Datasette will use a temporary in-memory SQLite database as the internal database, which is created at startup and destroyed at shutdown. Users of Datasette can optionally pass in a ``--internal`` flag to specify the path to a SQLite database to use as the internal database, which will persist internal data across Datasette instances.

Datasette maintains tables called ``catalog_databases``, ``catalog_tables``, ``catalog_views``, ``catalog_columns``, ``catalog_indexes``, ``catalog_foreign_keys`` with details of the attached databases and their schemas. These tables should not be considered a stable API - they may change between Datasette releases.

Metadata is stored in tables ``metadata_instance``, ``metadata_databases``, ``metadata_resources`` and ``metadata_columns``. Plugins can interact with these tables via the :ref:`get_*_metadata() and set_*_metadata() methods <datasette_get_set_metadata>`.

The internal database is not exposed in the Datasette application by default, which means private data can safely be stored without worry of accidentally leaking information through the default Datasette interface and API. However, other plugins do have full read and write access to the internal database.

Plugins can access this database by calling ``internal_db = datasette.get_internal_database()`` and then executing queries using the :ref:`Database API <internals_database>`.

Plugin authors are asked to practice good etiquette when using the internal database, as all plugins use the same database to store data. For example:

1. Use a unique prefix when creating tables, indices, and triggers in the internal database. If your plugin is called ``datasette-xyz``, then prefix names with ``datasette_xyz_*``.
2. Avoid long-running write statements that may stall or block other plugins that are trying to write at the same time.
3. Use temporary tables or shared in-memory attached databases when possible.
4. Avoid implementing features that could expose private data stored in the internal database by other plugins.

.. _internals_internal_schema:

Internal database schema
------------------------

The internal database schema is as follows:

.. [[[cog
    from metadata_doc import internal_schema
    internal_schema(cog)
.. ]]]

.. code-block:: sql

    CREATE TABLE catalog_databases (
        database_name TEXT PRIMARY KEY,
        path TEXT,
        is_memory INTEGER,
        schema_version INTEGER
    );
    CREATE TABLE catalog_tables (
        database_name TEXT,
        table_name TEXT,
        rootpage INTEGER,
        sql TEXT,
        PRIMARY KEY (database_name, table_name),
        FOREIGN KEY (database_name) REFERENCES catalog_databases(database_name)
    );
    CREATE TABLE catalog_views (
        database_name TEXT,
        view_name TEXT,
        rootpage INTEGER,
        sql TEXT,
        PRIMARY KEY (database_name, view_name),
        FOREIGN KEY (database_name) REFERENCES catalog_databases(database_name)
    );
    CREATE TABLE catalog_columns (
        database_name TEXT,
        table_name TEXT,
        cid INTEGER,
        name TEXT,
        type TEXT,
        "notnull" INTEGER,
        default_value TEXT, -- renamed from dflt_value
        is_pk INTEGER, -- renamed from pk
        hidden INTEGER,
        PRIMARY KEY (database_name, table_name, name),
        FOREIGN KEY (database_name) REFERENCES catalog_databases(database_name),
        FOREIGN KEY (database_name, table_name) REFERENCES catalog_tables(database_name, table_name)
    );
    CREATE TABLE catalog_indexes (
        database_name TEXT,
        table_name TEXT,
        seq INTEGER,
        name TEXT,
        "unique" INTEGER,
        origin TEXT,
        partial INTEGER,
        PRIMARY KEY (database_name, table_name, name),
        FOREIGN KEY (database_name) REFERENCES catalog_databases(database_name),
        FOREIGN KEY (database_name, table_name) REFERENCES catalog_tables(database_name, table_name)
    );
    CREATE TABLE catalog_foreign_keys (
        database_name TEXT,
        table_name TEXT,
        id INTEGER,
        seq INTEGER,
        "table" TEXT,
        "from" TEXT,
        "to" TEXT,
        on_update TEXT,
        on_delete TEXT,
        match TEXT,
        PRIMARY KEY (database_name, table_name, id, seq),
        FOREIGN KEY (database_name) REFERENCES catalog_databases(database_name),
        FOREIGN KEY (database_name, table_name) REFERENCES catalog_tables(database_name, table_name)
    );
    CREATE TABLE metadata_instance (
        key text,
        value text,
        unique(key)
    );
    CREATE TABLE metadata_databases (
        database_name text,
        key text,
        value text,
        unique(database_name, key)
    );
    CREATE TABLE metadata_resources (
        database_name text,
        resource_name text,
        key text,
        value text,
        unique(database_name, resource_name, key)
    );
    CREATE TABLE metadata_columns (
        database_name text,
        resource_name text,
        column_name text,
        key text,
        value text,
        unique(database_name, resource_name, column_name, key)
    );

.. [[[end]]]

.. _internals_utils:

The datasette.utils module
==========================

The ``datasette.utils`` module contains various utility functions used by Datasette. As a general rule you should consider anything in this module to be unstable - functions and classes here could change without warning or be removed entirely between Datasette releases, without being mentioned in the release notes.

The exception to this rule is anything that is documented here. If you find a need for an undocumented utility function in your own work, consider `opening an issue <https://github.com/simonw/datasette/issues/new>`__ requesting that the function you are using be upgraded to documented and supported status.

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

.. _internals_utils_named_parameters:

named_parameters(sql)
---------------------

Derive the list of ``:named`` parameters referenced in a SQL query.

.. autofunction:: datasette.utils.named_parameters

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

Note that running parallel SQL queries in this way has `been known to cause problems in the past <https://github.com/simonw/datasette/issues/2189>`__, so treat this example with caution.

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
