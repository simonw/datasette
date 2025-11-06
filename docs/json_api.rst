.. _json_api:

JSON API
========

Datasette provides a JSON API for your SQLite databases. Anything you can do
through the Datasette user interface can also be accessed as JSON via the API.

To access the API for a page, either click on the ``.json`` link on that page or
edit the URL and add a ``.json`` extension to it.

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

``"ok"`` is always ``true`` if an error did not occur.

The ``"rows"`` key is a list of objects, each one representing a row. 

The ``"truncated"`` key lets you know if the query was truncated. This can happen if a SQL query returns more than 1,000 results (or the :ref:`setting_max_returned_rows` setting).

For table pages, an additional key ``"next"`` may be present. This indicates that the next page in the pagination set can be retrieved using ``?_next=VALUE``.

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

Table extras
~~~~~~~~~~~~

JSON responses for table pages can include additional keys that are omitted by default. Pass one or more ``?_extra=NAME``
parameters (either repeating the argument or providing a comma-separated list) to opt in to the data that you need. The
following extras are available:

``?_extra=count``
    Returns the total number of rows that match the current filters, or ``null`` if the calculation times out or is
    otherwise unavailable. ``count`` may be served from cached introspection data for immutable databases when
    possible.【F:datasette/views/table.py†L1284-L1311】

``?_extra=count_sql``
    Returns the SQL that Datasette will execute in order to calculate the total row count.【F:datasette/views/table.py†L1284-L1290】

``?_extra=facet_results``
    Includes the full set of facet results calculated for the table view. The returned object has a ``results`` mapping
    of facet definitions to their buckets and a ``timed_out`` list describing any facets that hit the time limit.【F:datasette/views/table.py†L1316-L1365】

``?_extra=facets_timed_out``
    Adds just the list of facets that timed out while executing, without the full facet payload.【F:datasette/views/table.py†L1592-L1617】

``?_extra=suggested_facets``
    Returns suggestions for additional facets to apply, each with a ``name`` and ``toggle_url`` that can be used to
    activate that facet.【F:datasette/views/table.py†L1367-L1386】

``?_extra=human_description_en``
    Adds a human-readable sentence describing the current filters and sort order.【F:datasette/views/table.py†L1388-L1403】

``?_extra=next_url``
    Includes an absolute URL for the next page of results, or ``null`` if there is no next page.【F:datasette/views/table.py†L1404-L1406】

``?_extra=columns``
    Restores the ``columns`` list to the JSON output. Datasette removes this list by default to avoid duplicating
    information unless it is explicitly requested using this extra.【F:datasette/renderer.py†L110-L123】

``?_extra=primary_keys``
    Adds the list of primary key columns for the table.【F:datasette/views/table.py†L1408-L1414】

``?_extra=query``
    Returns the SQL query and parameters used to produce the current page of results.【F:datasette/views/table.py†L1484-L1490】

``?_extra=metadata``
    Includes metadata for the table and its columns, combining values from configuration and the ``metadata_columns``
    table.【F:datasette/views/table.py†L1491-L1527】

``?_extra=database`` and ``?_extra=table``
    Return the database name and table name for the current view.【F:datasette/views/table.py†L1510-L1517】

``?_extra=database_color``
    Adds the configured color for the database, useful for mirroring Datasette's UI styling.【F:datasette/views/table.py†L1518-L1520】

``?_extra=renderers``
    Lists the alternative output renderers available for the data, mapping renderer names to URLs that apply the
    requested renderer.【F:datasette/views/table.py†L1554-L1577】

``?_extra=custom_table_templates``
    Returns the ordered list of template names Datasette will consider when rendering the HTML table view.【F:datasette/views/table.py†L1533-L1540】

``?_extra=sorted_facet_results``
    Provides the facet definitions sorted by the number of results they contain, ready for display in descending order.【F:datasette/views/table.py†L1541-L1549】

``?_extra=table_definition`` and ``?_extra=view_definition``
    Include the ``CREATE TABLE`` or ``CREATE VIEW`` SQL definitions where available.【F:datasette/views/table.py†L1548-L1553】

``?_extra=is_view`` and ``?_extra=private``
    Report whether the current resource is a view and whether it is private to the current actor.【F:datasette/views/table.py†L1439-L1453】【F:datasette/views/table.py†L1581-L1587】

``?_extra=expandable_columns``
    Lists foreign key columns that can be expanded, each entry pairing the foreign key description with the column used
    for labels when expanding that relationship.【F:datasette/views/table.py†L1584-L1588】

``?_extra=form_hidden_args``
    Returns the ``("key", "value")`` pairs that Datasette includes as hidden fields in table forms for the current set
    of ``_`` query string arguments.【F:datasette/views/table.py†L1519-L1530】

``?_extra=extras``
    Provides metadata about all available extras, including toggle URLs that can be used to turn them on and off in the
    current query string.【F:datasette/views/table.py†L1592-L1611】

``?_extra=debug`` and ``?_extra=request``
    Return debugging context, including the resolved SQL details and request metadata such as the full URL and query
    string arguments.【F:datasette/views/table.py†L1442-L1467】

In addition to these API-friendly extras, Datasette exposes a handful of extras that are primarily intended for its HTML
interface—``actions``, ``filters``, ``display_columns`` and ``display_rows``. These currently return Python objects such
as callables or ``sqlite3.Row`` instances and may raise serialization errors if requested as JSON extras.【F:datasette/views/table.py†L1415-L1526】【F:datasette/renderer.py†L120-L123】

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
in ``metadata.json`` - see :ref:`label_columns`.

Row detail extras
-----------------

Row detail JSON is available at ``/<database>/<table>/<row-pks>.json``. Responses include the database and table names,
``rows`` and ``columns`` for the matched record, the primary key column names, the primary key values, and a ``query_ms``
timing for the lookup. Pass ``?_extras=foreign_key_tables`` (note the plural parameter name) to include a
``foreign_key_tables`` array describing incoming foreign keys, the number of related rows and navigation links to view
those rows.【F:datasette/views/row.py†L41-L111】

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
        "errors": [
            "UNIQUE constraint failed: new_table.id"
        ]
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

If successful, this will return a ``200`` status code and a ``{"ok": true}`` response body.

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
        "errors": [
            "Row 0 is missing primary key column(s): \"id\""
        ]
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
        "row": {
            "id": 1,
            "title": "New title",
            "other_column": "Will be present here too"
        }
    }

Any errors will return ``{"errors": ["... descriptive message ..."], "ok": false}``, and a ``400`` status code for a bad input or a ``403`` status code for an authentication or permission error.

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

Any errors will return ``{"errors": ["... descriptive message ..."], "ok": false}``, and a ``400`` status code for a bad input or a ``403`` status code for an authentication or permission error.

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
                "type": "text"
            }
        ],
        "pk": "id"
    }

The JSON here describes the table that will be created:

* ``table`` is the name of the table to create. This field is required.
* ``columns`` is a list of columns to create. Each column is a dictionary with ``name`` and ``type`` keys.

  - ``name`` is the name of the column. This is required.
  - ``type`` is the type of the column. This is optional - if not provided, ``text`` will be assumed. The valid types are ``text``, ``integer``, ``float`` and ``blob``.

* ``pk`` is the primary key for the table. This is optional - if not provided, Datasette will create a SQLite table with a hidden ``rowid`` column.

  If the primary key is an integer column, it will be configured to automatically increment for each new record.

  If you set this to ``id`` without including an ``id`` column in the list of ``columns``, Datasette will create an auto-incrementing integer ID column for you.

* ``pks`` can be used instead of ``pk`` to create a compound primary key. It should be a JSON list of column names to use in that primary key.
* ``ignore`` can be set to ``true`` to ignore existing rows by primary key if the table already exists.
* ``replace`` can be set to ``true`` to replace existing rows by primary key if the table already exists. This requires the :ref:`actions_update_row` permission.
* ``alter`` can be set to ``true`` if you want to automatically add any missing columns to the table. This requires the :ref:`actions_alter_table` permission.

If the table is successfully created this will return a ``201`` status code and the following response:

.. code-block:: json

    {
        "ok": true,
        "database": "data",
        "table": "name_of_new_table",
        "table_url": "http://127.0.0.1:8001/data/name_of_new_table",
        "table_api_url": "http://127.0.0.1:8001/data/name_of_new_table.json",
        "schema": "CREATE TABLE [name_of_new_table] (\n   [id] INTEGER PRIMARY KEY,\n   [title] TEXT\n)"
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
        "errors": [
            "UNIQUE constraint failed: creatures.id"
        ]
    }

You can avoid this error by passing the same ``"ignore": true`` or ``"replace": true`` options to the create endpoint as you can to the :ref:`insert endpoint <TableInsertView>`.

To use the ``"replace": true`` option you will also need the :ref:`actions_update_row` permission.

Pass ``"alter": true`` to automatically add any missing columns to the existing table that are present in the rows you are submitting. This requires the :ref:`actions_alter_table` permission.

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

Any errors will return ``{"errors": ["... descriptive message ..."], "ok": false}``, and a ``400`` status code for a bad input or a ``403`` status code for an authentication or permission error.
