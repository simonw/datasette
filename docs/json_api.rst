.. _json_api:

JSON API
========

Datasette provides a JSON API for your SQLite databases. Anything you can do
through the Datasette user interface can also be accessed as JSON via the API.

To access the API for a page, either click on the ``.json`` link on that page or
edit the URL and add a ``.json`` extension to it.

.. _json_api_stability:

API stability
-------------

Datasette 1.0 makes a stability promise for its JSON API: the endpoints,
parameters and response keys documented here and on the pages this
documentation links to will not change in backwards-incompatible ways for
the duration of the 1.x release series.

Stability means:

- Documented endpoints will keep their URLs, methods, parameters and
  permission requirements.
- Documented response keys will keep their names and types. New keys may be
  **added** in any release - clients should ignore keys they do not
  recognize.
- The documented ``?_extra=`` names, ``?_shape=`` values and
  :ref:`column filter operators <table_arguments>` are stable.
- Pagination tokens - the ``"next"`` key and ``?_next=`` parameter - are
  **opaque strings**. Pass them back exactly as you received them; their
  internal structure is not part of the API and can change at any time.
- The :ref:`standard error format <json_api_errors>` and the
  :ref:`API token format and restriction semantics <CreateTokenView>` are
  stable, including the action abbreviations stored inside signed tokens.

Some JSON endpoints are **exempt** from this promise:

- Endpoints that are not documented include this marker key in their
  responses and can change at any time::

      "unstable": "This API is not part of Datasette's stable interface and may change at any time"

  This currently covers the instance homepage (``/.json``), the stored
  query ``analyze``/``store``/``definition`` endpoints, ``/-/query/parameters``,
  ``/-/execute-write/analyze`` and the JSON returned by the ``/-/permissions``
  debug playground.
- Debug and support endpoints are documented so you can use them, but their
  JSON shapes are not frozen: :ref:`/-/threads <JsonDataView_threads>`,
  :ref:`/-/actions <JsonDataView_actions>`,
  the :ref:`permission debug endpoints <PermissionsDebugView>`
  (``/-/allowed``, ``/-/rules``, ``/-/check``) and the
  :ref:`table autocomplete endpoint <TableAutocompleteView>`.
- Response keys explicitly labeled as unstable in this documentation, such
  as the ``"analysis"`` block returned by :ref:`execute-write <ExecuteWriteView>`
  and the ``debug`` and ``request`` extras.

.. _json_api_default:

Default representation
----------------------

The default JSON representation of data from a SQLite table or custom query
looks like this:

.. code-block:: json

    {
      "ok": true,
      "rows": [
        {
          "id": 3,
          "name": "Detroit"
        },
        {
          "id": 2,
          "name": "Los Angeles"
        },
        {
          "id": 4,
          "name": "Memnonia"
        },
        {
          "id": 1,
          "name": "San Francisco"
        }
      ],
      "truncated": false
    }

``"ok"`` is always ``true`` if an error did not occur. Every Datasette JSON endpoint that returns an object includes this key on success.

The ``"rows"`` key is a list of objects, each one representing a row.

The ``"truncated"`` key lets you know if the query was truncated. This can happen if a SQL query returns more than 1,000 results (or the :ref:`setting_max_returned_rows` setting).

For table pages, two additional keys are present: ``"next"``, an opaque token that can be used to retrieve the next page using ``?_next=TOKEN``, and ``"next_url"``, the full URL of that next page. Both are ``null`` on the final page. See :ref:`json_api_pagination`.

.. _json_api_errors:

Error responses
---------------

Every JSON error response from Datasette uses the same format:

.. code-block:: json

    {
        "ok": false,
        "error": "Table not found",
        "errors": [
            "Table not found"
        ],
        "status": 404
    }

- ``"ok"`` is always ``false`` for an error.
- ``"errors"`` is a list of one or more error message strings. Endpoints that
  validate multiple things at once - such as the :ref:`insert API <TableInsertView>` -
  may return several messages here.
- ``"error"`` is all of those messages joined with ``"; "``, for
  convenience when displaying a single string.
- ``"status"`` matches the HTTP status code of the response.

Some endpoints add extra context keys. For example, a SQL error from a
:ref:`custom query <json_api_custom_sql>` also includes the empty
``"rows"`` and ``"truncated"`` keys of the response it was unable to
produce.

Permission errors use the same format: a request that fails a permission
check receives a ``403`` with this JSON error body when the URL ends in
``.json`` or the request sends an ``Accept: application/json`` or
``Content-Type: application/json`` header.

.. _json_api_custom_sql:

Executing custom SQL
--------------------

Actors with the :ref:`actions_execute_sql` permission can execute read-only SQL against a database using ``/-/query.json``:

::

    GET /<database>/-/query.json?sql=select+*+from+dogs

Values for named SQL parameters can be provided as additional query string parameters:

::

    GET /<database>/-/query.json?sql=select+*+from+dogs+where+name=:name&name=Cleo

The response uses the same default representation described above.

.. _json_api_shapes:

Different shapes
----------------

The ``_shape`` parameter can be used to access alternative formats for the
``rows`` key which may be more convenient for your application. There are three
options:

* ``?_shape=objects`` - ``"rows"`` is a list of JSON key/value objects - the default
* ``?_shape=arrays`` - ``"rows"`` is a list of lists, where the order of values in each list matches the order of the columns
* ``?_shape=array`` - a JSON array of objects - effectively just the ``"rows"`` key from the default representation
* ``?_shape=array&_nl=on`` - a newline-separated list of JSON objects
* ``?_shape=arrayfirst`` - a flat JSON array containing just the first value from each row
* ``?_shape=object`` - a JSON object keyed using the primary keys of the rows

``_shape=arrays`` looks like this:

.. code-block:: json

    {
      "ok": true,
      "next": null,
      "next_url": null,
      "rows": [
        [3, "Detroit"],
        [2, "Los Angeles"],
        [4, "Memnonia"],
        [1, "San Francisco"]
      ]
    }

``_shape=array`` looks like this:

.. code-block:: json

    [
      {
        "id": 3,
        "name": "Detroit"
      },
      {
        "id": 2,
        "name": "Los Angeles"
      },
      {
        "id": 4,
        "name": "Memnonia"
      },
      {
        "id": 1,
        "name": "San Francisco"
      }
    ]

``_shape=array&_nl=on`` looks like this::

    {"id": 1, "value": "Myoporum laetum :: Myoporum"}
    {"id": 2, "value": "Metrosideros excelsa :: New Zealand Xmas Tree"}
    {"id": 3, "value": "Pinus radiata :: Monterey Pine"}

``_shape=arrayfirst`` looks like this:

.. code-block:: json

    [1, 2, 3]

``_shape=object`` looks like this:

.. code-block:: json

    {
      "1": {
        "id": 1,
        "value": "Myoporum laetum :: Myoporum"
      },
      "2": {
        "id": 2,
        "value": "Metrosideros excelsa :: New Zealand Xmas Tree"
      },
      "3": {
        "id": 3,
        "value": "Pinus radiata :: Monterey Pine"
      }
    ]

The ``object`` shape is only available for queries against tables - custom SQL
queries and views do not have an obvious primary key so cannot be returned using
this format.

The ``object`` keys are always strings. If your table has a compound primary
key, the ``object`` keys will be a comma-separated string.

.. _json_api_pagination:

Pagination
----------

The default JSON representation includes a ``"next_url"`` key which can be used to access the next page of results. If that key is null or missing then it means you have reached the final page of results.

Other representations include pagination information in the ``link`` HTTP header. That header will look something like this::

    link: <https://latest.datasette.io/fixtures/sortable.json?_next=d%2Cv>; rel="next"

Here is an example Python function built using `requests <https://requests.readthedocs.io/>`__ that returns a list of all of the paginated items from one of these API endpoints:

.. code-block:: python

    def paginate(url):
        items = []
        while url:
            response = requests.get(url)
            try:
                url = response.links.get("next").get("url")
            except AttributeError:
                url = None
            items.extend(response.json())
        return items

.. _json_api_special:

Special JSON arguments
----------------------

Boolean query string arguments - such as ``?_labels=`` and
``?_json_infinity=`` - accept ``on``, ``true`` or ``1`` for true and
``off``, ``false`` or ``0`` for false.

Every Datasette endpoint that can return JSON also accepts the following
query string arguments:

``?_shape=SHAPE``
    The shape of the JSON to return, documented above.

``?_nl=on``
    When used with ``?_shape=array`` produces newline-delimited JSON objects.

``?_json=COLUMN1&_json=COLUMN2``
    If any of your SQLite columns contain JSON values, you can use one or more
    ``_json=`` parameters to request that those columns be returned as regular
    JSON. Without this argument those columns will be returned as JSON objects
    that have been double-encoded into a JSON string value.

    Compare `this query without the argument <https://fivethirtyeight.datasettes.com/fivethirtyeight.json?sql=select+%27{%22this+is%22%3A+%22a+json+object%22}%27+as+d&_shape=array>`_ to `this query using the argument <https://fivethirtyeight.datasettes.com/fivethirtyeight.json?sql=select+%27{%22this+is%22%3A+%22a+json+object%22}%27+as+d&_shape=array&_json=d>`_

``?_json_infinity=on``
    If your data contains infinity or -infinity values, Datasette will replace
    them with None when returning them as JSON. If you pass ``_json_infinity=1``
    Datasette will instead return them as ``Infinity`` or ``-Infinity`` which is
    invalid JSON but can be processed by some custom JSON parsers.

``?_timelimit=MS``
    Sets a custom time limit for the query in ms. You can use this for optimistic
    queries where you would like Datasette to give up if the query takes too
    long, for example if you want to implement autocomplete search but only if
    it can be executed in less than 10ms.

``?_ttl=SECONDS``
    For how many seconds should this response be cached by HTTP proxies? Use
    ``?_ttl=0`` to disable HTTP caching entirely for this request.

``?_trace=1``
    Turns on tracing for this page: SQL queries executed during the request will
    be gathered and included in the response, either in a new ``"_traces"`` key
    for JSON responses or at the bottom of the page if the response is in HTML.

    The structure of the data returned here should be considered highly unstable
    and very likely to change.

    Only available if the :ref:`setting_trace_debug` setting is enabled.

.. _json_api_extra:

Expanding JSON responses
------------------------

Table, row and query JSON responses can be expanded with one or more ``?_extra=`` parameters.
These can be repeated or comma-separated:

::

    ?_extra=columns&_extra=count,count_sql

Requesting an ``_extra`` name that does not exist returns a ``400`` error in the :ref:`standard error format <json_api_errors>`, for example ``{"ok": false, "error": "Unknown _extra: nope", ...}``.

.. [[[cog
    from json_api_doc import table_extras
    table_extras(cog)
.. ]]]

Table JSON responses
~~~~~~~~~~~~~~~~~~~~

The available table extras are listed below.

``count``
    Total count of rows matching these filters (May execute additional queries.)

    ``GET /fixtures/facetable.json?_extra=count``

    .. code-block:: json

        15

``count_truncated``
    True if the count hit Datasette's counting limit, meaning the real number of matching rows is at least the reported count. (May execute additional queries.)

    ``GET /fixtures/facetable.json?_extra=count,count_truncated``

    .. code-block:: json

        false

``count_sql``
    SQL query string used to calculate the total count for the current table view, including active filters.

    ``GET /fixtures/facetable.json?_size=0&_extra=count_sql``

    .. code-block:: json

        "select count(*) from facetable "

``facet_results``
    Results of facets calculated against this data. A dictionary with ``results`` and ``timed_out`` keys: ``results`` maps facet names to facet dictionaries with ``name``, ``type``, ``results`` and URL keys, and each facet result item includes ``value``, ``label``, ``count`` and ``toggle_url``. (May execute additional queries. See :ref:`facets` for details of how facets work.)

    Shape abbreviated from /fixtures/facetable.json?_facet=state&_extra=facet_results.

    .. code-block:: json

        {
          "results": {
            "state": {
              "name": "state",
              "type": "column",
              "results": [
                {
                  "value": "CA",
                  "label": "CA",
                  "count": 10
                },
                {
                  "value": "MI",
                  "label": "MI",
                  "count": 4
                }
              ]
            }
          },
          "timed_out": []
        }

``facets_timed_out``
    List of names of facet calculations that exceeded the facet time limit.

    ``GET /fixtures/facetable.json?_facet=state&_extra=facets_timed_out``

    A list of the names of any facets that exceeded the :ref:`setting_facet_time_limit_ms` time limit - an empty list if every facet calculation completed.

    .. code-block:: json

        []

``suggested_facets``
    Suggestions for facets that might return interesting results. Each item is a dictionary with ``name`` and ``toggle_url`` keys, and may include extra keys such as ``type`` or ``label`` depending on the facet class. (May execute additional queries. Suggestions are controlled by the :ref:`setting_suggest_facets` setting.)

    Shape abbreviated from /fixtures/facetable.json?_extra=suggested_facets.

    .. code-block:: json

        [
          {
            "name": "state",
            "toggle_url": "http://localhost/fixtures/facetable.json?_extra=suggested_facets&_facet=state"
          }
        ]

``human_description_en``
    Human-readable description of the filters

    ``GET /fixtures/facetable.json?state=CA&_sort=pk&_extra=human_description_en``

    .. code-block:: json

        "where state = \"CA\" sorted by pk"

``columns``
    List of column names returned by this table, row or query.

    ``GET /fixtures/facetable.json?_extra=columns``

    .. code-block:: json

        [
          "pk",
          "created",
          "planet_int",
          "on_earth",
          "state",
          "_city_id",
          "_neighborhood",
          "tags",
          "complex_array",
          "distinct_some_null",
          "n"
        ]

``all_columns``
    List of all column names in the table, regardless of ``_col=`` or ``_nocol=`` filtering.

    ``GET /fixtures/facetable.json?_col=pk&_extra=all_columns``

    .. code-block:: json

        [
          "pk",
          "created",
          "planet_int",
          "on_earth",
          "state",
          "_city_id",
          "_neighborhood",
          "tags",
          "complex_array",
          "distinct_some_null",
          "n"
        ]

``primary_keys``
    List of primary key column names for this table, or an empty list if the table has no explicit primary key.

    ``GET /fixtures/facetable.json?_extra=primary_keys``

    .. code-block:: json

        [
          "pk"
        ]

``column_details``
    SQLite schema details for columns in this table. The dictionary maps column names to objects describing the schema for each column. (Each object has ``type`` as the declared type string returned by SQLite, or ``""`` if no type was declared; ``sqlite_type`` as the normalized SQLite affinity, one of ``TEXT``, ``INTEGER``, ``REAL``, ``BLOB`` or ``NUMERIC``; ``notnull`` as a boolean; ``default`` as the raw SQL default expression string, such as ``"42"``, ``"'hello'"`` or ``"datetime('now')"``, or ``null`` if there is no default; ``is_pk`` as a boolean; ``pk_position`` as the integer primary key position reported by SQLite, or ``0`` for columns that are not part of the primary key; and ``hidden`` as the integer value reported by SQLite's ``PRAGMA table_xinfo``. ``hidden`` is ``0`` for normal columns, ``1`` for hidden virtual table columns, ``2`` for virtual generated columns and ``3`` for stored generated columns.)

    ``GET /fixtures/binary_data.json?_size=0&_extra=column_details``

    .. code-block:: json

        {
          "data": {
            "type": "BLOB",
            "sqlite_type": "BLOB",
            "notnull": false,
            "default": null,
            "is_pk": false,
            "pk_position": 0,
            "hidden": 0
          }
        }

``display_columns``
    Column metadata used by the HTML table display. Each item includes ``name``, ``sortable``, ``is_pk``, ``type``, ``notnull``, ``description``, ``column_type`` and ``column_type_config`` keys.

    Shape abbreviated from /fixtures/facetable.json?_size=1&_extra=display_columns.

    .. code-block:: json

        [
          {
            "name": "pk",
            "sortable": true,
            "is_pk": true,
            "type": "INTEGER",
            "notnull": 0
          },
          {
            "name": "created",
            "sortable": true,
            "is_pk": false,
            "type": "TEXT",
            "notnull": 0,
            "description": null,
            "column_type": null,
            "column_type_config": null
          }
        ]

``render_cell``
    Rendered HTML for each cell using the render_cell plugin hook (See the :ref:`render_cell() plugin hook <plugin_hook_render_cell>` documentation.)

    The ``render_cell`` array has one item per row, in the same order as the ``rows`` array. Each object is keyed by column name. Only columns whose rendered value differs from the default are included.

    .. code-block:: json

        {
          "rows": [
            {
              "id": 1,
              "content": "hello"
            },
            {
              "id": 4,
              "content": "RENDER_CELL_DEMO"
            }
          ],
          "render_cell": [
            {},
            {
              "content": "<strong>Custom rendered HTML</strong>"
            }
          ]
        }

``debug``
    Extra debug information dictionary. This is intended for development only and its shape is not part of the stable template contract. (The contents of this block are not a stable part of the Datasette API and may change without warning.)

    ``GET /fixtures/facetable.json?_extra=debug``

    .. code-block:: json

        {
          "url_vars": {
            "database": "fixtures",
            "table": "facetable",
            "format": "json"
          },
          "resolved": "ResolvedTable(db=<Database: fixtures (mutable, size=249856)>, table='facetable', is_view=False)",
          "nofacet": null,
          "nosuggest": null
        }

``request``
    Dictionary with request details: ``url``, ``path``, ``full_path``, ``host`` and ``args`` where ``args`` maps query string parameter names to their values.

    ``GET /fixtures/facetable.json?_extra=request``

    .. code-block:: json

        {
          "url": "http://localhost/fixtures/facetable.json?_extra=request",
          "path": "/fixtures/facetable.json",
          "full_path": "/fixtures/facetable.json?_extra=request",
          "host": "localhost",
          "args": {
            "_extra": [
              "request"
            ]
          }
        }

``query``
    Details of the underlying SQL query as a dictionary with ``sql`` and ``params`` keys.

    ``GET /fixtures/facetable.json?_size=1&_extra=query``

    .. code-block:: json

        {
          "sql": "select pk, created, planet_int, on_earth, state, _city_id, _neighborhood, tags, complex_array, distinct_some_null, n from facetable order by pk limit 2",
          "params": {}
        }

``column_types``
    Column type assignments for this table. A dictionary mapping column names to ``{"type": type_name, "config": config}`` dictionaries. (An empty object if no column types have been assigned. Column types can be assigned in :ref:`configuration <table_configuration_column_types>` or using the :ref:`set column type API <TableSetColumnTypeView>`.)

    ``GET /fixtures/facetable.json?_size=0&_extra=column_types``

    This example is from an instance where the ``tags`` column has been assigned the ``json`` column type.

    .. code-block:: json

        {
          "tags": {
            "type": "json",
            "config": null
          }
        }

``set_column_type_ui``
    Information needed to build an interface for assigning column types, or ``None`` if unavailable. When present it has ``path`` and ``columns`` keys; ``columns`` maps column names to ``current`` and ``options`` values. (``null`` unless the current actor is allowed to use the :ref:`set column type API <TableSetColumnTypeView>` for this table.)

    Shape abbreviated to two columns, as seen by an actor with ``set-column-type`` permission. ``current`` is the column type currently assigned to each column and ``options`` lists the types that could be assigned to it.

    .. code-block:: json

        {
          "path": "/fixtures/facetable/-/set-column-type",
          "columns": {
            "created": {
              "current": null,
              "options": [
                {
                  "name": "email",
                  "description": "Email address"
                },
                {
                  "name": "json",
                  "description": "JSON data"
                },
                {
                  "name": "url",
                  "description": "URL"
                }
              ]
            },
            "tags": {
              "current": {
                "type": "json",
                "config": null
              },
              "options": [
                {
                  "name": "email",
                  "description": "Email address"
                },
                {
                  "name": "json",
                  "description": "JSON data"
                },
                {
                  "name": "url",
                  "description": "URL"
                }
              ]
            }
          }
        }

``metadata``
    Metadata dictionary for the table, database or stored query. Table and row metadata include a ``columns`` dictionary mapping column names to descriptions; stored query metadata returns the stored query configuration. (See :ref:`metadata` for how to attach metadata to tables.)

    ``GET /fixtures/facetable.json?_extra=metadata``

    This example is from an instance where the ``facetable`` table has a metadata ``description`` and a :ref:`column description <metadata_column_descriptions>` for its ``state`` column. The ``columns`` object is empty for tables with no column descriptions.

    .. code-block:: json

        {
          "description": "A demo table of places, used to demonstrate facets",
          "columns": {
            "state": "Two letter US state code"
          }
        }

``extras``
    List of ``?_extra=`` blocks that can be used on this page. Each item has ``name``, ``description``, ``toggle_url`` and ``selected`` keys.

    Shape abbreviated from /fixtures/facetable.json?_extra=extras - the full response lists every extra described on this page. ``toggle_url`` is the current URL with that extra added or removed, and ``selected`` is ``true`` for extras included in the current request.

    .. code-block:: json

        [
          {
            "name": "count",
            "description": "Total count of rows matching these filters",
            "toggle_url": "http://localhost/fixtures/facetable.json?_extra=extras&_extra=count",
            "selected": false
          },
          {
            "name": "extras",
            "description": "List of ?_extra= blocks that can be used on this page",
            "toggle_url": "http://localhost/fixtures/facetable.json",
            "selected": true
          }
        ]

``database``
    Database name

    ``GET /fixtures/facetable.json?_extra=database``

    .. code-block:: json

        "fixtures"

``table``
    Table name

    ``GET /fixtures/facetable.json?_extra=table``

    .. code-block:: json

        "facetable"

``database_color``
    Color assigned to the database (A six character hex color, without the leading ``#``, derived from a hash of the database name and used in the Datasette interface.)

    ``GET /fixtures/facetable.json?_extra=database_color``

    .. code-block:: json

        "9403e5"

``renderers``
    Dictionary mapping output format names such as ``json`` or plugin-provided renderer names to URLs for this data in that format.

    ``GET /fixtures/facetable.json?_extra=renderers``

    Each key is the name of an output format, each value the URL for this data in that format. Plugins can add additional formats using the :ref:`register_output_renderer() plugin hook <plugin_register_output_renderer>`.

    .. code-block:: json

        {
          "json": "/fixtures/facetable.json?_extra=renderers&_format=json&_labels=on"
        }

``custom_table_templates``
    List of custom template names considered for rendering table rows, in lookup order. (The first template in this list that exists will be used to render the table on the HTML version of this page. See :ref:`customization_custom_templates`.)

    ``GET /fixtures/facetable.json?_extra=custom_table_templates``

    .. code-block:: json

        [
          "_table-fixtures-facetable.html",
          "_table-table-fixtures-facetable.html",
          "_table.html"
        ]

``sorted_facet_results``
    Facet result dictionaries sorted for display. Each item has the same shape as an entry from ``facet_results['results']``. (The same data as ``facet_results``, as a list in the order used by the HTML interface: facets from :ref:`facet configuration <facets_metadata>` first, then other facets ordered by their number of results.)

    ``GET /fixtures/facetable.json?_facet=state&_extra=sorted_facet_results``

    .. code-block:: json

        [
          {
            "name": "state",
            "type": "column",
            "hideable": true,
            "toggle_url": "/fixtures/facetable.json?_extra=sorted_facet_results",
            "results": [
              {
                "value": "CA",
                "label": "CA",
                "count": 10,
                "toggle_url": "http://localhost/fixtures/facetable.json?_facet=state&_extra=sorted_facet_results&state=CA",
                "selected": false
              },
              {
                "value": "MI",
                "label": "MI",
                "count": 4,
                "toggle_url": "http://localhost/fixtures/facetable.json?_facet=state&_extra=sorted_facet_results&state=MI",
                "selected": false
              },
              {
                "value": "MC",
                "label": "MC",
                "count": 1,
                "toggle_url": "http://localhost/fixtures/facetable.json?_facet=state&_extra=sorted_facet_results&state=MC",
                "selected": false
              }
            ],
            "truncated": false
          }
        ]

``table_definition``
    SQL definition for this table

    ``GET /fixtures/facetable.json?_extra=table_definition``

    .. code-block:: json

        "CREATE TABLE facetable (\n    pk integer primary key,\n    created text,\n    planet_int integer,\n    on_earth integer,\n    state text,\n    _city_id integer,\n    _neighborhood text,\n    tags text,\n    complex_array text,\n    distinct_some_null,\n    n text,\n    FOREIGN KEY (\"_city_id\") REFERENCES [facet_cities](id)\n);"

``view_definition``
    SQL definition for this view

    ``GET /fixtures/simple_view.json?_extra=view_definition``

    .. code-block:: json

        "CREATE VIEW simple_view AS\n    SELECT content, upper(content) AS upper_content FROM simple_primary_key;"

``is_view``
    Whether this resource is a view instead of a table

    ``GET /fixtures/simple_view.json?_extra=is_view``

    .. code-block:: json

        true

``private``
    Whether this resource is private to the current actor (``true`` if the current actor can see this resource but an anonymous user could not. See :ref:`authentication_permissions`.)

    ``GET /fixtures/facetable.json?_extra=private``

    .. code-block:: json

        false

``expandable_columns``
    List of foreign key columns that can be expanded with labels. Each item is a ``(foreign_key, label_column)`` pair where ``foreign_key`` is the SQLite foreign key dictionary and ``label_column`` is the label column in the referenced table, or ``None``. (See :ref:`expand_foreign_keys` for how to expand these labels.)

    ``GET /fixtures/facetable.json?_extra=expandable_columns``

    Each item is a ``[foreign_key, label_column]`` pair: the foreign key relationship, then the column in the other table that would be used as the label for each expanded value.

    .. code-block:: json

        [
          [
            {
              "column": "_city_id",
              "other_table": "facet_cities",
              "other_column": "id"
            },
            "name"
          ]
        ]

``form_hidden_args``
    List of ``(name, value)`` pairs for hidden form fields used by the HTML table interface to preserve current query string options.

    ``GET /fixtures/facetable.json?_facet=state&_size=1&_extra=form_hidden_args``

    .. code-block:: json

        [
          [
            "_facet",
            "state"
          ],
          [
            "_size",
            "1"
          ],
          [
            "_extra",
            "form_hidden_args"
          ]
        ]

Row JSON responses
~~~~~~~~~~~~~~~~~~

The following extras are available for row JSON responses.

``columns``
    List of column names returned by this table, row or query.

    ``GET /fixtures/simple_primary_key/1.json?_extra=columns``

    .. code-block:: json

        [
          "id",
          "content"
        ]

``primary_keys``
    List of primary key column names for this table, or an empty list if the table has no explicit primary key.

    ``GET /fixtures/simple_primary_key/1.json?_extra=primary_keys``

    .. code-block:: json

        [
          "id"
        ]

``column_details``
    SQLite schema details for columns in this table. The dictionary maps column names to objects describing the schema for each column. (Each object has ``type`` as the declared type string returned by SQLite, or ``""`` if no type was declared; ``sqlite_type`` as the normalized SQLite affinity, one of ``TEXT``, ``INTEGER``, ``REAL``, ``BLOB`` or ``NUMERIC``; ``notnull`` as a boolean; ``default`` as the raw SQL default expression string, such as ``"42"``, ``"'hello'"`` or ``"datetime('now')"``, or ``null`` if there is no default; ``is_pk`` as a boolean; ``pk_position`` as the integer primary key position reported by SQLite, or ``0`` for columns that are not part of the primary key; and ``hidden`` as the integer value reported by SQLite's ``PRAGMA table_xinfo``. ``hidden`` is ``0`` for normal columns, ``1`` for hidden virtual table columns, ``2`` for virtual generated columns and ``3`` for stored generated columns.)

    ``GET /fixtures/binary_data/1.json?_extra=column_details``

    .. code-block:: json

        {
          "data": {
            "type": "BLOB",
            "sqlite_type": "BLOB",
            "notnull": false,
            "default": null,
            "is_pk": false,
            "pk_position": 0,
            "hidden": 0
          }
        }

``render_cell``
    Rendered HTML for each cell using the render_cell plugin hook (See the :ref:`render_cell() plugin hook <plugin_hook_render_cell>` documentation.)

    The ``render_cell`` array has one item for the requested row. The object is keyed by column name. Only columns whose rendered value differs from the default are included.

    .. code-block:: json

        {
          "rows": [
            {
              "id": 4,
              "content": "RENDER_CELL_DEMO"
            }
          ],
          "render_cell": [
            {
              "content": "<strong>Custom rendered HTML</strong>"
            }
          ]
        }

``debug``
    Extra debug information dictionary. This is intended for development only and its shape is not part of the stable template contract. (The contents of this block are not a stable part of the Datasette API and may change without warning.)

    ``GET /fixtures/simple_primary_key/1.json?_extra=debug``

    .. code-block:: json

        {
          "url_vars": {
            "database": "fixtures",
            "table": "simple_primary_key",
            "pks": "1",
            "format": "json"
          },
          "resolved": {
            "table": "simple_primary_key",
            "sql": "select * from simple_primary_key where \"id\"=:p0",
            "params": {
              "p0": "1"
            },
            "pks": [
              "id"
            ],
            "pk_values": [
              "1"
            ]
          }
        }

``request``
    Dictionary with request details: ``url``, ``path``, ``full_path``, ``host`` and ``args`` where ``args`` maps query string parameter names to their values.

    ``GET /fixtures/simple_primary_key/1.json?_extra=request``

    .. code-block:: json

        {
          "url": "http://localhost/fixtures/simple_primary_key/1.json?_extra=request",
          "path": "/fixtures/simple_primary_key/1.json",
          "full_path": "/fixtures/simple_primary_key/1.json?_extra=request",
          "host": "localhost",
          "args": {
            "_extra": [
              "request"
            ]
          }
        }

``query``
    Details of the underlying SQL query as a dictionary with ``sql`` and ``params`` keys.

    ``GET /fixtures/simple_primary_key/1.json?_extra=query``

    .. code-block:: json

        {
          "sql": "select * from simple_primary_key where \"id\"=:p0",
          "params": {
            "p0": "1"
          }
        }

``column_types``
    Column type assignments for this table. A dictionary mapping column names to ``{"type": type_name, "config": config}`` dictionaries. (An empty object if no column types have been assigned. Column types can be assigned in :ref:`configuration <table_configuration_column_types>` or using the :ref:`set column type API <TableSetColumnTypeView>`.)

    ``GET /fixtures/facetable/1.json?_extra=column_types``

    This example is from an instance where the ``tags`` column has been assigned the ``json`` column type.

    .. code-block:: json

        {
          "tags": {
            "type": "json",
            "config": null
          }
        }

``metadata``
    Metadata dictionary for the table, database or stored query. Table and row metadata include a ``columns`` dictionary mapping column names to descriptions; stored query metadata returns the stored query configuration. (See :ref:`metadata` for how to attach metadata to tables.)

    ``GET /fixtures/simple_primary_key/1.json?_extra=metadata``

    This table has no metadata, so only an empty ``columns`` object is returned.

    .. code-block:: json

        {
          "columns": {}
        }

``extras``
    List of ``?_extra=`` blocks that can be used on this page. Each item has ``name``, ``description``, ``toggle_url`` and ``selected`` keys.

    Shape abbreviated from /fixtures/facetable.json?_extra=extras - the full response lists every extra described on this page. ``toggle_url`` is the current URL with that extra added or removed, and ``selected`` is ``true`` for extras included in the current request.

    .. code-block:: json

        [
          {
            "name": "count",
            "description": "Total count of rows matching these filters",
            "toggle_url": "http://localhost/fixtures/facetable.json?_extra=extras&_extra=count",
            "selected": false
          },
          {
            "name": "extras",
            "description": "List of ?_extra= blocks that can be used on this page",
            "toggle_url": "http://localhost/fixtures/facetable.json",
            "selected": true
          }
        ]

``database``
    Database name

    ``GET /fixtures/simple_primary_key/1.json?_extra=database``

    .. code-block:: json

        "fixtures"

``table``
    Table name

    ``GET /fixtures/simple_primary_key/1.json?_extra=table``

    .. code-block:: json

        "simple_primary_key"

``database_color``
    Color assigned to the database (A six character hex color, without the leading ``#``, derived from a hash of the database name and used in the Datasette interface.)

    ``GET /fixtures/simple_primary_key/1.json?_extra=database_color``

    .. code-block:: json

        "9403e5"

``private``
    Whether this resource is private to the current actor (``true`` if the current actor can see this resource but an anonymous user could not. See :ref:`authentication_permissions`.)

    ``GET /fixtures/simple_primary_key/1.json?_extra=private``

    .. code-block:: json

        false

``foreign_key_tables``
    List of tables that link to this row using foreign keys. Each item includes the foreign key fields plus ``count`` for matching rows and ``link`` for the filtered table URL. (May execute additional queries.)

    ``GET /fixtures/simple_primary_key/1.json?_extra=foreign_key_tables``

    ``count`` is the number of rows in the other table that reference this row, and ``link`` is a URL to browse those rows.

    .. code-block:: json

        [
          {
            "other_table": "complex_foreign_keys",
            "column": "id",
            "other_column": "f1",
            "count": 1,
            "link": "/fixtures/complex_foreign_keys?f1=1"
          },
          {
            "other_table": "complex_foreign_keys",
            "column": "id",
            "other_column": "f2",
            "count": 0,
            "link": "/fixtures/complex_foreign_keys?f2=1"
          },
          {
            "other_table": "complex_foreign_keys",
            "column": "id",
            "other_column": "f3",
            "count": 1,
            "link": "/fixtures/complex_foreign_keys?f3=1"
          },
          {
            "other_table": "foreign_key_references",
            "column": "id",
            "other_column": "foreign_key_with_blank_label",
            "count": 0,
            "link": "/fixtures/foreign_key_references?foreign_key_with_blank_label=1"
          },
          {
            "other_table": "foreign_key_references",
            "column": "id",
            "other_column": "foreign_key_with_label",
            "count": 1,
            "link": "/fixtures/foreign_key_references?foreign_key_with_label=1"
          }
        ]

Query JSON responses
~~~~~~~~~~~~~~~~~~~~

The following extras are available for arbitrary SQL query responses and stored, named query responses.

``columns``
    List of column names returned by this table, row or query.

    ``GET /fixtures/-/query.json?sql=select+1+as+one&_extra=columns``

    .. code-block:: json

        [
          "one"
        ]

``render_cell``
    Rendered HTML for each cell using the render_cell plugin hook (See the :ref:`render_cell() plugin hook <plugin_hook_render_cell>` documentation.)

    The ``render_cell`` array has one item per query result row, in the same order as the ``rows`` array. Each object is keyed by column name. Only columns whose rendered value differs from the default are included.

    .. code-block:: json

        {
          "rows": [
            {
              "content": "RENDER_CELL_DEMO"
            }
          ],
          "render_cell": [
            {
              "content": "<strong>Custom rendered HTML</strong>"
            }
          ]
        }

``debug``
    Extra debug information dictionary. This is intended for development only and its shape is not part of the stable template contract. (The contents of this block are not a stable part of the Datasette API and may change without warning.)

    ``GET /fixtures/-/query.json?sql=select+1+as+one&_extra=debug``

    .. code-block:: json

        {
          "url_vars": {
            "database": "fixtures",
            "format": "json"
          }
        }

``request``
    Dictionary with request details: ``url``, ``path``, ``full_path``, ``host`` and ``args`` where ``args`` maps query string parameter names to their values.

    ``GET /fixtures/-/query.json?sql=select+1+as+one&_extra=request``

    .. code-block:: json

        {
          "url": "http://localhost/fixtures/-/query.json?sql=select+1+as+one&_extra=request",
          "path": "/fixtures/-/query.json",
          "full_path": "/fixtures/-/query.json?sql=select+1+as+one&_extra=request",
          "host": "localhost",
          "args": {
            "sql": [
              "select 1 as one"
            ],
            "_extra": [
              "request"
            ]
          }
        }

``query``
    Details of the underlying SQL query as a dictionary with ``sql`` and ``params`` keys.

    ``GET /fixtures/-/query.json?sql=select+1+as+one&_extra=query``

    .. code-block:: json

        {
          "sql": "select 1 as one",
          "params": {}
        }

    ``GET /fixtures/neighborhood_search.json?text=town&_extra=query``

    .. code-block:: json

        {
          "sql": "\nselect _neighborhood, facet_cities.name, state\nfrom facetable\n    join facet_cities\n        on facetable._city_id = facet_cities.id\nwhere _neighborhood like '%' || :text || '%'\norder by _neighborhood;\n",
          "params": {
            "text": "town"
          }
        }

``metadata``
    Metadata dictionary for the table, database or stored query. Table and row metadata include a ``columns`` dictionary mapping column names to descriptions; stored query metadata returns the stored query configuration. (See :ref:`metadata` for how to attach metadata to tables.)

    ``GET /fixtures/neighborhood_search.json?text=town&_extra=metadata``

    For stored queries this returns the full configuration of the query, including the :ref:`stored query options <queries_options>`. For ``?sql=`` queries it returns an empty object.

    .. code-block:: json

        {
          "database": "fixtures",
          "name": "neighborhood_search",
          "sql": "\nselect _neighborhood, facet_cities.name, state\nfrom facetable\n    join facet_cities\n        on facetable._city_id = facet_cities.id\nwhere _neighborhood like '%' || :text || '%'\norder by _neighborhood;\n",
          "title": "Search neighborhoods",
          "description": null,
          "description_html": null,
          "hide_sql": false,
          "fragment": null,
          "parameters": [],
          "is_write": false,
          "is_private": false,
          "is_trusted": true,
          "owner_id": null,
          "on_success_message": null,
          "on_success_message_sql": null,
          "on_success_redirect": null,
          "on_error_message": null,
          "on_error_redirect": null
        }

``extras``
    List of ``?_extra=`` blocks that can be used on this page. Each item has ``name``, ``description``, ``toggle_url`` and ``selected`` keys.

    Shape abbreviated from /fixtures/facetable.json?_extra=extras - the full response lists every extra described on this page. ``toggle_url`` is the current URL with that extra added or removed, and ``selected`` is ``true`` for extras included in the current request.

    .. code-block:: json

        [
          {
            "name": "count",
            "description": "Total count of rows matching these filters",
            "toggle_url": "http://localhost/fixtures/facetable.json?_extra=extras&_extra=count",
            "selected": false
          },
          {
            "name": "extras",
            "description": "List of ?_extra= blocks that can be used on this page",
            "toggle_url": "http://localhost/fixtures/facetable.json",
            "selected": true
          }
        ]

``database``
    Database name

    ``GET /fixtures/-/query.json?sql=select+1+as+one&_extra=database``

    .. code-block:: json

        "fixtures"

``database_color``
    Color assigned to the database (A six character hex color, without the leading ``#``, derived from a hash of the database name and used in the Datasette interface.)

    ``GET /fixtures/-/query.json?sql=select+1+as+one&_extra=database_color``

    .. code-block:: json

        "9403e5"

``private``
    Whether this resource is private to the current actor (``true`` if the current actor can see this resource but an anonymous user could not. See :ref:`authentication_permissions`.)

    ``GET /fixtures/-/query.json?sql=select+1+as+one&_extra=private``

    .. code-block:: json

        false

.. [[[end]]]

.. _TableAutocompleteView:

Table autocomplete
------------------

The ``/<database>/<table>/-/autocomplete`` endpoint returns up to 10 primary key
matches for a table, intended for building autocomplete interfaces such as
foreign key pickers.

::

    GET /<database>/<table>/-/autocomplete?q=search

The ``q`` parameter is required. If it is omitted or blank, the endpoint returns
an empty ``"rows"`` list.

The response includes a ``"pks"`` object containing the primary key value or
values for each row. If Datasette can detect a label column, or one has been
configured using ``label_column``, each row will also include ``"label"``:

.. code-block:: json

    {
        "rows": [
            {
                "pks": {
                    "id": 1
                },
                "label": "Example row"
            }
        ]
    }

The endpoint searches the primary key column or columns and the label column
using escaped SQL ``LIKE`` queries. A single-column primary key exact match is
returned first. Other matches are ordered by the shortest matching label value
where a label column is available.

The initial search runs with a 500ms time limit. If that query times out,
Datasette falls back to a prefix match against the first primary key column so
SQLite can use the primary key index.

.. _table_arguments:

Table arguments
---------------

The Datasette table view takes a number of special query string arguments.

Column filter arguments
~~~~~~~~~~~~~~~~~~~~~~~

You can filter the data returned by the table based on column values using a query string argument.

``?column__exact=value`` or ``?_column=value``
    Returns rows where the specified column exactly matches the value.

``?column__not=value``
    Returns rows where the column does not match the value.

``?column__contains=value``
    Rows where the string column contains the specified value (``column like "%value%"`` in SQL).

``?column__notcontains=value``
    Rows where the string column does not contain the specified value (``column not like "%value%"`` in SQL).

``?column__endswith=value``
    Rows where the string column ends with the specified value (``column like "%value"`` in SQL).

``?column__startswith=value``
    Rows where the string column starts with the specified value (``column like "value%"`` in SQL).

``?column__gt=value``
    Rows which are greater than the specified value.

``?column__gte=value``
    Rows which are greater than or equal to the specified value.

``?column__lt=value``
    Rows which are less than the specified value.

``?column__lte=value``
    Rows which are less than or equal to the specified value.

``?column__like=value``
    Match rows with a LIKE clause, case insensitive and with ``%`` as the wildcard character.

``?column__notlike=value``
    Match rows that do not match the provided LIKE clause.

``?column__glob=value``
    Similar to LIKE but uses Unix wildcard syntax and is case sensitive.

``?column__in=value1,value2,value3``
    Rows where column matches any of the provided values.

    You can use a comma separated string, or you can use a JSON array.

    The JSON array option is useful if one of your matching values itself contains a comma:

    ``?column__in=["value","value,with,commas"]``

``?column__notin=value1,value2,value3``
    Rows where column does not match any of the provided values. The inverse of ``__in=``. Also supports JSON arrays.

``?column__arraycontains=value``
    Works against columns that contain JSON arrays - matches if any of the values in that array match the provided value.

    This is only available if the ``json1`` SQLite extension is enabled.

``?column__arraynotcontains=value``
    Works against columns that contain JSON arrays - matches if none of the values in that array match the provided value.

    This is only available if the ``json1`` SQLite extension is enabled.

``?column__date=value``
    Column is a datestamp occurring on the specified YYYY-MM-DD date, e.g. ``2018-01-02``.

``?column__isnull=1``
    Matches rows where the column is null.

``?column__notnull=1``
    Matches rows where the column is not null.

``?column__isblank=1``
    Matches rows where the column is blank, meaning null or the empty string.

``?column__notblank=1``
    Matches rows where the column is not blank.

.. _json_api_table_arguments:

Special table arguments
~~~~~~~~~~~~~~~~~~~~~~~

``?_col=COLUMN1&_col=COLUMN2``
    List specific columns to display. These will be shown along with any primary keys.

``?_nocol=COLUMN1&_nocol=COLUMN2``
    List specific columns to hide - any column not listed will be displayed. Primary keys cannot be hidden.

``?_labels=on/off``
    Expand foreign key references for every possible column. See below.

``?_label=COLUMN1&_label=COLUMN2``
    Expand foreign key references for one or more specified columns.

``?_size=1000`` or ``?_size=max``
    Sets a custom page size. This cannot exceed the ``max_returned_rows`` limit
    passed to ``datasette serve``. Use ``max`` to get ``max_returned_rows``.

``?_sort=COLUMN``
    Sorts the results by the specified column.

``?_sort_desc=COLUMN``
    Sorts the results by the specified column in descending order.

``?_search=keywords``
    For SQLite tables that have been configured for
    `full-text search <https://www.sqlite.org/fts3.html>`_ executes a search
    with the provided keywords.

``?_search_COLUMN=keywords``
    Like ``_search=`` but allows you to specify the column to be searched, as
    opposed to searching all columns that have been indexed by FTS.

``?_searchmode=raw``
    With this option, queries passed to ``?_search=`` or ``?_search_COLUMN=`` will
    not have special characters escaped. This means you can make use of the full
    set of `advanced SQLite FTS syntax <https://www.sqlite.org/fts5.html#full_text_query_syntax>`__,
    though this could potentially result in errors if the wrong syntax is used.

``?_where=SQL-fragment``
    If the :ref:`actions_execute_sql` permission is enabled, this parameter
    can be used to pass one or more additional SQL fragments to be used in the
    `WHERE` clause of the SQL used to query the table.

    This is particularly useful if you are building a JavaScript application
    that needs to do something creative but still wants the other conveniences
    provided by the table view (such as faceting) and hence would like not to
    have to construct a completely custom SQL query.

    Some examples:

    * `facetable?_where=_neighborhood like "%c%"&_where=_city_id=3 <https://latest.datasette.io/fixtures/facetable?_where=_neighborhood%20like%20%22%c%%22&_where=_city_id=3>`__
    * `facetable?_where=_city_id in (select id from facet_cities where name != "Detroit") <https://latest.datasette.io/fixtures/facetable?_where=_city_id%20in%20(select%20id%20from%20facet_cities%20where%20name%20!=%20%22Detroit%22)>`__

``?_through={json}``
    This can be used to filter rows via a join against another table.

    The JSON parameter must include three keys: ``table``, ``column`` and ``value``.

    ``table`` must be a table that the current table is related to via a foreign key relationship.

    ``column`` must be a column in that other table.

    ``value`` is the value that you want to match against.

    For example, to filter ``roadside_attractions`` to just show the attractions that have a characteristic of "museum", you would construct this JSON::

        {
            "table": "roadside_attraction_characteristics",
            "column": "characteristic_id",
            "value": "1"
        }

    As a URL, that looks like this:

    ``?_through={%22table%22:%22roadside_attraction_characteristics%22,%22column%22:%22characteristic_id%22,%22value%22:%221%22}``

    Here's `an example <https://latest.datasette.io/fixtures/roadside_attractions?_through={%22table%22:%22roadside_attraction_characteristics%22,%22column%22:%22characteristic_id%22,%22value%22:%221%22}>`__.

``?_next=TOKEN``
    Pagination by continuation token - pass the token that was returned in the
    ``"next"`` property by the previous page.

``?_facet=column``
    Facet by column. Can be applied multiple times, see :ref:`facets`. Only works on the default JSON output, not on any of the custom shapes.

``?_facet_size=100``
    Increase the number of facet results returned for each facet. Use ``?_facet_size=max`` for the maximum available size, determined by :ref:`setting_max_returned_rows`.

``?_nofacet=1``
    Disable all facets and facet suggestions for this page, including any defined by :ref:`facets_metadata`.

``?_nosuggest=1``
    Disable facet suggestions for this page.

``?_nocount=1``
    Disable the ``select count(*)`` query used on this page - a count of ``None`` will be returned instead.

.. _expand_foreign_keys:

Expanding foreign key references
--------------------------------

Datasette can detect foreign key relationships and resolve those references into
labels. The HTML interface does this by default for every detected foreign key
column - you can turn that off using ``?_labels=off``.

You can request foreign keys be expanded in JSON using the ``_labels=on`` or
``_label=COLUMN`` special query string parameters. Here's what an expanded row
looks like:

.. code-block:: json

    [
        {
            "rowid": 1,
            "TreeID": 141565,
            "qLegalStatus": {
                "value": 1,
                "label": "Permitted Site"
            },
            "qSpecies": {
                "value": 1,
                "label": "Myoporum laetum :: Myoporum"
            },
            "qAddress": "501X Baker St",
            "SiteOrder": 1
        }
    ]

The column in the foreign key table that is used for the label can be specified
in ``datasette.yaml`` - see :ref:`table_configuration_label_column`.

.. _json_api_discover_alternate:

Discovering the JSON for a page
-------------------------------

Most of the HTML pages served by Datasette provide a mechanism for discovering their JSON equivalents using the HTML ``link`` mechanism.

You can find this near the top of the source code of those pages, looking like this:

.. code-block:: html

    <link rel="alternate"
      type="application/json+datasette"
      href="https://latest.datasette.io/fixtures/sortable.json">

The JSON URL is also made available in a ``Link`` HTTP header for the page::

    Link: <https://latest.datasette.io/fixtures/sortable.json>; rel="alternate"; type="application/json+datasette"

.. _json_api_cors:

Enabling CORS
-------------

If you start Datasette with the ``--cors`` option, each JSON endpoint will be
served with the following additional HTTP headers:

.. [[[cog
    from datasette.utils import add_cors_headers
    import textwrap
    headers = {}
    add_cors_headers(headers)
    output = "\n".join("{}: {}".format(k, v) for k, v in headers.items())
    cog.out("\n::\n\n")
    cog.out(textwrap.indent(output, '    '))
    cog.out("\n\n")
.. ]]]

::

    Access-Control-Allow-Origin: *
    Access-Control-Allow-Headers: Authorization, Content-Type
    Access-Control-Expose-Headers: Link
    Access-Control-Allow-Methods: GET, POST, HEAD, OPTIONS
    Access-Control-Max-Age: 3600

.. [[[end]]]

This allows JavaScript running on any domain to make cross-origin
requests to interact with the Datasette API.

If you start Datasette without the ``--cors`` option only JavaScript running on
the same domain as Datasette will be able to access the API.

Here's how to serve ``data.db`` with CORS enabled::

    datasette data.db --cors

.. _json_api_write:

The JSON write API
------------------

Datasette provides a write API for JSON data. This is a POST-only API that requires an authenticated API token, see :ref:`CreateTokenView`. The token will need to have the specified :ref:`authentication_permissions`.

The request body is always parsed as JSON, regardless of the request's ``Content-Type`` header - a body that is not valid JSON returns a ``400`` error. Cross-site request forgery is prevented by Datasette's ``Origin`` and ``Sec-Fetch-Site`` header checks rather than by content type requirements.

The row-based write APIs can write :ref:`binary values in JSON <binary_json_format>` using Datasette's Base64 representation for BLOB data.

.. _ExecuteWriteView:

Executing write SQL
~~~~~~~~~~~~~~~~~~~

Actors with the :ref:`actions_execute_write_sql` permission can execute arbitrary writable SQL against a mutable database using ``/-/execute-write``.

::

    POST /<database>/-/execute-write
    Content-Type: application/json
    Authorization: Bearer dstok_<rest-of-token>

The request body must include a ``"sql"`` string. Named SQL parameters can be provided using the optional ``"params"`` object:

.. code-block:: json

    {
        "sql": "insert into dogs (name) values (:name)",
        "params": {
            "name": "Cleo"
        }
    }

The SQL must be writable. Read-only ``select`` queries should use the regular :ref:`custom SQL query JSON API <json_api_custom_sql>` instead.

Datasette analyzes the SQL before executing it. The actor must have ``execute-write-sql`` permission for the database, and must also have any permissions required by the operations in the SQL. For example, inserts and updates against a table require ``insert-row``, ``update-row`` and ``delete-row`` permissions for that table. Reads performed as part of the write, such as ``insert into dogs select ... from other_table``, require ``view-table`` permission on the source table. Schema changes require ``create-table``, ``alter-table`` or ``drop-table`` permissions as appropriate.

Unsupported SQL operations are rejected by default. ``VACUUM`` is not allowed in arbitrary write SQL, and writes to SQLite virtual tables or shadow tables are rejected. SQL functions are allowed and are not separately restricted by Datasette permissions.

A successful response includes a message, the SQLite ``rowcount``, a ``"rows"``
list, a ``"truncated"`` flag and a summary of the operations that were executed:

The shape of the ``"analysis"`` block is not part of the :ref:`stable API <json_api_stability>` and may change in future Datasette releases.

.. code-block:: json

    {
        "ok": true,
        "message": "Query executed, 1 row affected",
        "rowcount": 1,
        "rows": [],
        "truncated": false,
        "analysis": [
            {
                "operation": "insert",
                "database": "data",
                "table": "dogs",
                "required_permission": "insert-row, update-row, delete-row",
                "source": null
            }
        ]
    }

If SQLite reports ``-1`` for the row count, the message will be ``"Query executed"``.

For most write statements ``"rows"`` will be an empty list and ``"truncated"``
will be ``false``. If the SQL uses SQLite's ``RETURNING`` clause, ``"rows"``
will contain returned rows using the same default representation as table and
query JSON responses. ``"truncated"`` indicates if more rows were returned than
the execute-write returning row limit, which defaults to 10:

.. code-block:: json

    {
        "ok": true,
        "message": "Query executed, 1 row affected",
        "rowcount": 1,
        "rows": [
            {
                "id": 1,
                "name": "Cleo"
            }
        ],
        "truncated": false,
        "analysis": [
            {
                "operation": "insert",
                "database": "data",
                "table": "dogs",
                "required_permission": "insert-row, update-row, delete-row",
                "source": null
            },
            {
                "operation": "read",
                "database": "data",
                "table": "dogs",
                "required_permission": "view-table",
                "source": null
            }
        ]
    }

Errors use the :ref:`standard Datasette error format <json_api_errors>`:

.. code-block:: json

    {
        "ok": false,
        "error": "Permission denied: need execute-write-sql",
        "errors": [
            "Permission denied: need execute-write-sql"
        ],
        "status": 403
    }

.. _TableInsertView:

Inserting rows
~~~~~~~~~~~~~~

This requires the :ref:`actions_insert_row` permission.

A single row can be inserted using the ``"row"`` key:

::

    POST /<database>/<table>/-/insert
    Content-Type: application/json
    Authorization: Bearer dstok_<rest-of-token>

.. code-block:: json

    {
        "row": {
            "column1": "value1",
            "column2": "value2"
        }
    }

Column values can use the :ref:`binary value JSON format <binary_json_format>` to write BLOB data.

If successful, this will return a ``201`` status code and the newly inserted row, for example:

.. code-block:: json

    {
        "rows": [
            {
                "id": 1,
                "column1": "value1",
                "column2": "value2"
            }
        ]
    }

To insert multiple rows at a time, use the same API method but send a list of dictionaries as the ``"rows"`` key:

::

    POST /<database>/<table>/-/insert
    Content-Type: application/json
    Authorization: Bearer dstok_<rest-of-token>

.. code-block:: json

    {
        "rows": [
            {
                "column1": "value1",
                "column2": "value2"
            },
            {
                "column1": "value3",
                "column2": "value4"
            }
        ]
    }

If successful, this will return a ``201`` status code and a ``{"ok": true}`` response body.

The maximum number rows that can be submitted at once defaults to 100, but this can be changed using the :ref:`setting_max_insert_rows` setting.

To return the newly inserted rows, add the ``"return": true`` key to the request body:

.. code-block:: json

    {
        "rows": [
            {
                "column1": "value1",
                "column2": "value2"
            },
            {
                "column1": "value3",
                "column2": "value4"
            }
        ],
        "return": true
    }

This will return the same ``"rows"`` key as the single row example above. There is a small performance penalty for using this option.

If any of your rows have a primary key that is already in use, you will get an error and none of the rows will be inserted:

.. code-block:: json

    {
        "ok": false,
        "error": "UNIQUE constraint failed: new_table.id",
        "errors": [
            "UNIQUE constraint failed: new_table.id"
        ],
        "status": 400
    }

Pass ``"ignore": true`` to ignore these errors and insert the other rows:

.. code-block:: json

    {
        "rows": [
            {
                "id": 1,
                "column1": "value1",
                "column2": "value2"
            },
            {
                "id": 2,
                "column1": "value3",
                "column2": "value4"
            }
        ],
        "ignore": true
    }

Or you can pass ``"replace": true`` to replace any rows with conflicting primary keys with the new values. This requires the :ref:`actions_update_row` permission.

Pass ``"alter: true`` to automatically add any missing columns to the table. This requires the :ref:`actions_alter_table` permission.

.. _TableUpsertView:

Upserting rows
~~~~~~~~~~~~~~

An upsert is an insert or update operation. If a row with a matching primary key already exists it will be updated - otherwise a new row will be inserted.

The upsert API is mostly the same shape as the :ref:`insert API <TableInsertView>`. It requires both the :ref:`actions_insert_row` and :ref:`actions_update_row` permissions.

It also accepts the same :ref:`binary value JSON format <binary_json_format>`.

::

    POST /<database>/<table>/-/upsert
    Content-Type: application/json
    Authorization: Bearer dstok_<rest-of-token>

.. code-block:: json

    {
        "rows": [
            {
                "id": 1,
                "title": "Updated title for 1",
                "description": "Updated description for 1"
            },
            {
                "id": 2,
                "description": "Updated description for 2",
            },
            {
                "id": 3,
                "title": "Item 3",
                "description": "Description for 3"
            }
        ]
    }

Imagine a table with a primary key of ``id`` and which already has rows with ``id`` values of ``1`` and ``2``.

The above example will:

- Update the row with ``id`` of ``1`` to set both ``title`` and ``description`` to the new values
- Update the row with ``id`` of ``2`` to set ``title`` to the new value - ``description`` will be left unchanged
- Insert a new row with ``id`` of ``3`` and both ``title`` and ``description`` set to the new values

Similar to ``/-/insert``, a ``row`` key with an object can be used instead of a ``rows`` array to upsert a single row.

If successful, this will return a ``200`` status code and a ``{"ok": true}`` response body. This is deliberately different from the ``201`` returned by :ref:`insert <TableInsertView>`: an upsert may update existing rows without creating anything, so it does not claim resource creation.

Add ``"return": true`` to the request body to return full copies of the affected rows after they have been inserted or updated:

.. code-block:: json

    {
        "rows": [
            {
                "id": 1,
                "title": "Updated title for 1",
                "description": "Updated description for 1"
            },
            {
                "id": 2,
                "description": "Updated description for 2",
            },
            {
                "id": 3,
                "title": "Item 3",
                "description": "Description for 3"
            }
        ],
        "return": true
    }

This will return the following:

.. code-block:: json

    {
        "ok": true,
        "rows": [
            {
                "id": 1,
                "title": "Updated title for 1",
                "description": "Updated description for 1"
            },
            {
                "id": 2,
                "title": "Item 2",
                "description": "Updated description for 2"
            },
            {
                "id": 3,
                "title": "Item 3",
                "description": "Description for 3"
            }
        ]
    }

When using upsert you must provide the primary key column (or columns if the table has a compound primary key) for every row, or you will get a ``400`` error:

.. code-block:: json

    {
        "ok": false,
        "error": "Row 0 is missing primary key column(s): \"id\"",
        "errors": [
            "Row 0 is missing primary key column(s): \"id\""
        ],
        "status": 400
    }

If your table does not have an explicit primary key you should pass the SQLite ``rowid`` key instead.

Pass ``"alter: true`` to automatically add any missing columns to the table. This requires the :ref:`actions_alter_table` permission.

.. _RowUpdateView:

Updating a row
~~~~~~~~~~~~~~

To update a row, make a ``POST`` to ``/<database>/<table>/<row-pks>/-/update``. This requires the :ref:`actions_update_row` permission.

::

    POST /<database>/<table>/<row-pks>/-/update
    Content-Type: application/json
    Authorization: Bearer dstok_<rest-of-token>

.. code-block:: json

    {
        "update": {
            "text_column": "New text string",
            "integer_column": 3,
            "float_column": 3.14
        }
    }

``<row-pks>`` here is the :ref:`tilde-encoded <internals_tilde_encoding>` primary key value of the row to update - or a comma-separated list of primary key values if the table has a composite primary key.

You only need to pass the columns you want to update. Any other columns will be left unchanged.

Updated values can use the :ref:`binary value JSON format <binary_json_format>`.

If successful, this will return a ``200`` status code and a ``{"ok": true}`` response body.

Add ``"return": true`` to the request body to return the updated row:

.. code-block:: json

    {
        "update": {
            "title": "New title"
        },
        "return": true
    }

The returned JSON will look like this:

.. code-block:: json

    {
        "ok": true,
        "rows": [
            {
                "id": 1,
                "title": "New title",
                "other_column": "Will be present here too"
            }
        ]
    }

Any errors will use the :ref:`standard error format <json_api_errors>`, with a ``400`` status code for a bad input or a ``403`` status code for an authentication or permission error.

Pass ``"alter: true`` to automatically add any missing columns to the table. This requires the :ref:`actions_alter_table` permission.

.. _RowDeleteView:

Deleting a row
~~~~~~~~~~~~~~

To delete a row, make a ``POST`` to ``/<database>/<table>/<row-pks>/-/delete``. This requires the :ref:`actions_delete_row` permission.

::

    POST /<database>/<table>/<row-pks>/-/delete
    Content-Type: application/json
    Authorization: Bearer dstok_<rest-of-token>

``<row-pks>`` here is the :ref:`tilde-encoded <internals_tilde_encoding>` primary key value of the row to delete - or a comma-separated list of primary key values if the table has a composite primary key.

If successful, this will return a ``200`` status code and a ``{"ok": true}`` response body.

Any errors will use the :ref:`standard error format <json_api_errors>`, with a ``400`` status code for a bad input or a ``403`` status code for an authentication or permission error.

.. _TableCreateView:

Creating a table
~~~~~~~~~~~~~~~~

To create a table, make a ``POST`` to ``/<database>/-/create``. This requires the :ref:`actions_create_table` permission.

::

    POST /<database>/-/create
    Content-Type: application/json
    Authorization: Bearer dstok_<rest-of-token>

.. code-block:: json

    {
        "table": "name_of_new_table",
        "columns": [
            {
                "name": "id",
                "type": "integer"
            },
            {
                "name": "title",
                "type": "text",
                "not_null": true,
                "default": "Untitled"
            },
            {
                "name": "created",
                "type": "text",
                "default_expr": "current_timestamp"
            }
        ],
        "pk": "id"
    }

The JSON here describes the table that will be created:

* ``table`` is the name of the table to create. This field is required.
* ``columns`` is a list of columns to create. Each column is a dictionary with ``name`` and ``type`` keys.

  - ``name`` is the name of the column. This is required.
  - ``type`` is the type of the column. This is optional - if not provided, ``text`` will be assumed. The valid types are ``text``, ``integer``, ``float`` and ``blob``.
  - ``not_null`` can be set to ``true`` to create this column with a ``NOT NULL`` constraint.
  - ``default`` can be used to set a literal default value for this column.
  - ``default_expr`` can be used instead of ``default`` to set a SQLite default expression. See :ref:`default_expr values <json_api_default_expr_values>`.
  - ``fk_table`` can be used to create a single-column foreign key constraint referencing another table. ``fk_column`` is optional and can be used to specify the referenced column - if omitted, Datasette will use the single primary key of ``fk_table``.

* ``pk`` is the primary key for the table. This is optional - if not provided, Datasette will create a SQLite table with a hidden ``rowid`` column.

  If the primary key is an integer column, it will be configured to automatically increment for each new record.

  If you set this to ``id`` without including an ``id`` column in the list of ``columns``, Datasette will create an auto-incrementing integer ID column for you.

* ``pks`` can be used instead of ``pk`` to create a compound primary key. It should be a JSON list of column names to use in that primary key.
* ``ignore`` can be set to ``true`` to ignore existing rows by primary key if the table already exists.
* ``replace`` can be set to ``true`` to replace existing rows by primary key if the table already exists. This requires the :ref:`actions_update_row` permission.
* ``alter`` can be set to ``true`` if you want to automatically add any missing columns to the table. This requires the :ref:`actions_alter_table` permission.

.. _json_api_default_expr_values:

``default_expr`` accepts these values:

.. list-table::
   :header-rows: 1

   * - Value
     - Recommended column type
     - Example inserted value
   * - ``current_timestamp``
     - ``text``
     - ``2026-05-01 13:34:00``
   * - ``current_date``
     - ``text``
     - ``2026-05-01``
   * - ``current_time``
     - ``text``
     - ``13:34:00``
   * - ``current_unixtime``
     - ``integer``
     - ``1777642440``
   * - ``current_unixtime_ms``
     - ``integer``
     - ``1777642440000``

This example creates a foreign key from ``projects.owner_id`` to the single primary key of ``owners``:

.. code-block:: json

    {
        "table": "projects",
        "columns": [
            {
                "name": "id",
                "type": "integer"
            },
            {
                "name": "owner_id",
                "type": "integer",
                "fk_table": "owners"
            },
            {
                "name": "title",
                "type": "text"
            }
        ],
        "pk": "id"
    }

If the table is successfully created this will return a ``201`` status code and the following response:

.. code-block:: json

    {
        "ok": true,
        "database": "data",
        "table": "name_of_new_table",
        "table_url": "http://127.0.0.1:8001/data/name_of_new_table",
        "table_api_url": "http://127.0.0.1:8001/data/name_of_new_table.json",
        "schema": "CREATE TABLE [name_of_new_table] (\n   [id] INTEGER PRIMARY KEY,\n   [title] TEXT NOT NULL DEFAULT 'Untitled',\n   [created] TEXT DEFAULT CURRENT_TIMESTAMP\n)"
    }

.. _TableCreateView_example:

Creating a table from example data
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Instead of specifying ``columns`` directly you can instead pass a single example ``row`` or a list of ``rows``.
Datasette will create a table with a schema that matches those rows and insert them for you:

::

    POST /<database>/-/create
    Content-Type: application/json
    Authorization: Bearer dstok_<rest-of-token>

.. code-block:: json

    {
        "table": "creatures",
        "rows": [
            {
                "id": 1,
                "name": "Tarantula"
            },
            {
                "id": 2,
                "name": "Kākāpō"
            }
        ],
        "pk": "id"
    }

Example rows can use the :ref:`binary value JSON format <binary_json_format>`, allowing Datasette to infer ``BLOB`` columns.

Doing this requires both the :ref:`actions_create_table` and :ref:`actions_insert_row` permissions.

The ``201`` response here will be similar to the ``columns`` form, but will also include the number of rows that were inserted as ``row_count``:

.. code-block:: json

    {
        "ok": true,
        "database": "data",
        "table": "creatures",
        "table_url": "http://127.0.0.1:8001/data/creatures",
        "table_api_url": "http://127.0.0.1:8001/data/creatures.json",
        "schema": "CREATE TABLE [creatures] (\n   [id] INTEGER PRIMARY KEY,\n   [name] TEXT\n)",
        "row_count": 2
    }

You can call the create endpoint multiple times for the same table provided you are specifying the table using the ``rows`` or ``row`` option. New rows will be inserted into the table each time. This means you can use this API if you are unsure if the relevant table has been created yet.

If you pass a row to the create endpoint with a primary key that already exists you will get an error that looks like this:

.. code-block:: json

    {
        "ok": false,
        "error": "UNIQUE constraint failed: creatures.id",
        "errors": [
            "UNIQUE constraint failed: creatures.id"
        ],
        "status": 400
    }

You can avoid this error by passing the same ``"ignore": true`` or ``"replace": true`` options to the create endpoint as you can to the :ref:`insert endpoint <TableInsertView>`.

To use the ``"replace": true`` option you will also need the :ref:`actions_update_row` permission.

Pass ``"alter": true`` to automatically add any missing columns to the existing table that are present in the rows you are submitting. This requires the :ref:`actions_alter_table` permission.

.. _DatabaseForeignKeyTargetsView:

Database foreign key targets
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``/<database>/-/foreign-key-targets`` endpoint returns the list of tables in a database that can be referenced by a single-column foreign key. This requires the :ref:`actions_create_table` permission.

::

    GET /<database>/-/foreign-key-targets

The response includes only tables with exactly one primary key column. Hidden tables, tables with compound primary keys and tables with no explicit primary key are omitted.

Each target includes the normalized SQLite type affinity for the primary key column in ``type``. The type is calculated using SQLite's documented affinity rules: ``INT`` maps to ``integer``; ``CHAR``, ``CLOB`` or ``TEXT`` maps to ``text``; ``BLOB`` or no type maps to ``blob``; ``REAL`` and floating-point declared types map to ``real``; everything else maps to ``numeric``.

.. code-block:: json

    {
        "ok": true,
        "database": "data",
        "targets": [
            {
                "fk_table": "owners",
                "fk_column": "id",
                "type": "integer"
            },
            {
                "fk_table": "categories",
                "fk_column": "slug",
                "type": "text"
            }
        ]
    }

.. _TableForeignKeySuggestionsView:

Table foreign key suggestions
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``/<database>/<table>/-/foreign-key-suggestions`` endpoint suggests possible single-column foreign key relationships for a table. This requires the :ref:`actions_alter_table` permission.

::

    GET /<database>/<table>/-/foreign-key-suggestions

The response includes every type-compatible single-column primary key target for each column in ``options``. Datasette also performs a bounded data check against up to 500 rows in the table: if the sampled non-null values for a column all exist in a target primary key, that target is included in ``suggestions``.

If the bounded check takes too long, the endpoint fails open. It still returns the type-compatible ``options`` for each column, but ``row_check.status`` will be ``"timed_out"`` and there may be no ``suggestions``.

.. code-block:: json

    {
        "ok": true,
        "database": "data",
        "table": "projects",
        "row_check": {
            "attempted": true,
            "status": "completed",
            "row_limit": 500,
            "sampled_rows": 3,
            "checked_options": 4
        },
        "columns": [
            {
                "column": "owner_id",
                "type": "INTEGER",
                "affinity": "integer",
                "current": null,
                "suggestions": [
                    {
                        "fk_table": "owners",
                        "fk_column": "id",
                        "confidence": "sampled",
                        "sampled_values": 3,
                        "reasons": [
                            "type_match",
                            "sample_values_exist",
                            "name_match"
                        ]
                    }
                ],
                "options": [
                    {
                        "fk_table": "owners",
                        "fk_column": "id",
                        "type": "INTEGER"
                    }
                ]
            }
        ]
    }

.. _TableAlterView:

Altering tables
~~~~~~~~~~~~~~~

To alter an existing table, make a ``POST`` to ``/<database>/<table>/-/alter``. This requires the :ref:`actions_alter_table` permission.

::

    POST /<database>/<table>/-/alter
    Content-Type: application/json
    Authorization: Bearer dstok_<rest-of-token>

The request body should include an ``operations`` array. Each operation has the same top-level shape: an ``op`` string and an ``args`` object.

.. code-block:: json

    {
        "operations": [
            {
                "op": "add_column",
                "args": {
                    "name": "slug",
                    "type": "text",
                    "not_null": true,
                    "default": ""
                }
            },
            {
                "op": "add_column",
                "args": {
                    "name": "created",
                    "type": "text",
                    "default_expr": "current_timestamp"
                }
            },
            {
                "op": "rename_column",
                "args": {
                    "name": "title",
                    "to": "headline"
                }
            },
            {
                "op": "rename_table",
                "args": {
                    "to": "published_posts"
                }
            },
            {
                "op": "alter_column",
                "args": {
                    "name": "score",
                    "type": "float"
                }
            },
            {
                "op": "drop_column",
                "args": {
                    "name": "draft_notes"
                }
            },
            {
                "op": "set_primary_key",
                "args": {
                    "columns": ["id"]
                }
            },
            {
                "op": "add_foreign_key",
                "args": {
                    "column": "owner_id",
                    "fk_table": "owners"
                }
            },
            {
                "op": "drop_foreign_key",
                "args": {
                    "column": "old_owner_id"
                }
            },
            {
                "op": "set_foreign_keys",
                "args": {
                    "foreign_keys": [
                        {
                            "column": "owner_id",
                            "fk_table": "owners",
                            "fk_column": "id"
                        }
                    ]
                }
            },
            {
                "op": "reorder_columns",
                "args": {
                    "columns": ["id", "headline", "slug", "created", "score"]
                }
            }
        ]
    }

Supported operations:

* ``add_column`` adds a new column. ``args`` accepts ``name``, optional ``type`` of ``text``, ``integer``, ``float`` or ``blob``, optional ``not_null``, optional literal ``default`` and optional ``default_expr``. If ``not_null`` is ``true`` either a non-null ``default`` or ``default_expr`` is required.
* ``rename_column`` renames a column. ``args`` accepts ``name`` and ``to``.
* ``rename_table`` renames the table. ``args`` accepts ``to``, the new table name. If combined with other operations, Datasette applies the column, primary key, foreign key and column order changes before renaming the table.
* ``alter_column`` changes column properties. ``args`` accepts ``name`` and at least one of ``type``, ``not_null``, literal ``default`` or ``default_expr``. Passing ``"default": null`` removes an existing default.
* ``drop_column`` drops a column. ``args`` accepts ``name``.
* ``set_primary_key`` changes the table primary key. ``args`` accepts ``columns``, a list of one or more column names.
* ``add_foreign_key`` adds a single-column foreign key constraint. ``args`` accepts ``column``, ``fk_table`` and optional ``fk_column``. If ``fk_column`` is omitted, Datasette will use the single primary key of ``fk_table``.
* ``drop_foreign_key`` removes the foreign key constraint for a column. ``args`` accepts ``column``.
* ``set_foreign_keys`` replaces all foreign key constraints on the table. ``args`` accepts ``foreign_keys``, a list of objects that each have ``column``, ``fk_table`` and optional ``fk_column``. An empty list removes all foreign key constraints.
* ``reorder_columns`` reorders columns. ``args`` accepts ``columns``, a list of one or more column names. Columns omitted from this list will appear afterwards in their existing order.

``default`` is always treated as a literal value. ``default_expr`` accepts the values shown in :ref:`default_expr values <json_api_default_expr_values>` and is rendered as the corresponding SQLite default expression.

For foreign key operations that omit ``fk_column``, the referenced ``fk_table`` must have a single-column primary key. Datasette will return an error if it cannot identify a single primary key column for that table.

A successful response returns the new schema and the previous schema. If the request used ``rename_table``, ``table``, ``table_url`` and ``table_api_url`` will use the new table name. Renaming a table through this endpoint triggers the :class:`~datasette.events.RenameTableEvent` event.

.. code-block:: json

    {
        "ok": true,
        "database": "data",
        "table": "published_posts",
        "table_url": "http://127.0.0.1:8001/data/published_posts",
        "table_api_url": "http://127.0.0.1:8001/data/published_posts.json",
        "altered": true,
        "schema": "CREATE TABLE ...",
        "before_schema": "CREATE TABLE ...",
        "operations_applied": 11
    }

Any errors will use the :ref:`standard error format <json_api_errors>`, with a ``400`` status code for a bad input or a ``403`` status code for an authentication or permission error.

.. _TableSetColumnTypeView:

Setting a column type
~~~~~~~~~~~~~~~~~~~~~

To set a column type for a table column, make a ``POST`` to ``/<database>/<table>/-/set-column-type``. This requires the :ref:`actions_set_column_type` permission.

::

    POST /<database>/<table>/-/set-column-type
    Content-Type: application/json
    Authorization: Bearer dstok_<rest-of-token>

.. code-block:: json

    {
        "column": "title",
        "column_type": {
            "type": "email"
        }
    }

This will return a ``200`` response like this:

.. code-block:: json

    {
        "ok": true,
        "database": "data",
        "table": "posts",
        "column": "title",
        "column_type": {
            "type": "email",
            "config": null
        }
    }

To provide column type configuration, include a ``config`` object:

.. code-block:: json

    {
        "column": "title",
        "column_type": {
            "type": "url",
            "config": {
                "max_length": 200
            }
        }
    }

To clear an existing column type assignment, set ``column_type`` to ``null``:

.. code-block:: json

    {
        "column": "title",
        "column_type": null
    }

This API stores the assignment in Datasette's internal database, so it can be used with immutable databases as well as mutable ones.

Any errors will use the :ref:`standard error format <json_api_errors>`, with a ``400`` status code for a bad input or a ``403`` status code for an authentication or permission error.

.. _TableDropView:

Dropping tables
~~~~~~~~~~~~~~~

To drop a table, make a ``POST`` to ``/<database>/<table>/-/drop``. This requires the :ref:`actions_drop_table` permission.

::

    POST /<database>/<table>/-/drop
    Content-Type: application/json
    Authorization: Bearer dstok_<rest-of-token>

Without a POST body this will return a status ``200`` with a note about how many rows will be deleted:

.. code-block:: json

    {
        "ok": true,
        "database": "<database>",
        "table": "<table>",
        "row_count": 5,
        "message": "Pass \"confirm\": true to confirm"
    }

If you pass the following POST body:

.. code-block:: json

    {
        "confirm": true
    }

Then the table will be dropped and a status ``200`` response of ``{"ok": true}`` will be returned.

Any errors will use the :ref:`standard error format <json_api_errors>`, with a ``400`` status code for a bad input or a ``403`` status code for an authentication or permission error.
