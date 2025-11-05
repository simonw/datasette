.. _json_api:

JSON API
========

Datasette provides a JSON API for your SQLite databases. Anything you can do
through the Datasette user interface can also be accessed as JSON via the API.

To access the API for a page, either click on the ``.json`` link on that page or
edit the URL and add a ``.json`` extension to it.

If you started Datasette with the ``--cors`` option, each JSON endpoint will be
served with the following additional HTTP headers::

    Access-Control-Allow-Origin: *
    Access-Control-Allow-Headers: Authorization
    Access-Control-Expose-Headers: Link

This means JavaScript running on any domain will be able to make cross-origin
requests to fetch the data.

If you start Datasette without the ``--cors`` option only JavaScript running on
the same domain as Datasette will be able to access the API.

.. _json_api_shapes:

Different shapes
----------------

The default JSON representation of data from a SQLite table or custom query
looks like this::

    {
        "database": "sf-trees",
        "table": "qSpecies",
        "columns": [
            "id",
            "value"
        ],
        "rows": [
            [
                1,
                "Myoporum laetum :: Myoporum"
            ],
            [
                2,
                "Metrosideros excelsa :: New Zealand Xmas Tree"
            ],
            [
                3,
                "Pinus radiata :: Monterey Pine"
            ]
        ],
        "truncated": false,
        "next": "100",
        "next_url": "http://127.0.0.1:8001/sf-trees-02c8ef1/qSpecies.json?_next=100",
        "query_ms": 1.9571781158447266
    }

The ``columns`` key lists the columns that are being returned, and the ``rows``
key then returns a list of lists, each one representing a row. The order of the
values in each row corresponds to the columns.

The ``_shape`` parameter can be used to access alternative formats for the
``rows`` key which may be more convenient for your application. There are three
options:

* ``?_shape=arrays`` - ``"rows"`` is the default option, shown above
* ``?_shape=objects`` - ``"rows"`` is a list of JSON key/value objects
* ``?_shape=array`` - an JSON array of objects
* ``?_shape=array&_nl=on`` - a newline-separated list of JSON objects
* ``?_shape=arrayfirst`` - a flat JSON array containing just the first value from each row
* ``?_shape=object`` - a JSON object keyed using the primary keys of the rows

``_shape=objects`` looks like this::

    {
        "database": "sf-trees",
        ...
        "rows": [
            {
                "id": 1,
                "value": "Myoporum laetum :: Myoporum"
            },
            {
                "id": 2,
                "value": "Metrosideros excelsa :: New Zealand Xmas Tree"
            },
            {
                "id": 3,
                "value": "Pinus radiata :: Monterey Pine"
            }
        ]
    }

``_shape=array`` looks like this::

    [
        {
            "id": 1,
            "value": "Myoporum laetum :: Myoporum"
        },
        {
            "id": 2,
            "value": "Metrosideros excelsa :: New Zealand Xmas Tree"
        },
        {
            "id": 3,
            "value": "Pinus radiata :: Monterey Pine"
        }
    ]

``_shape=array&_nl=on`` looks like this::

    {"id": 1, "value": "Myoporum laetum :: Myoporum"}
    {"id": 2, "value": "Metrosideros excelsa :: New Zealand Xmas Tree"}
    {"id": 3, "value": "Pinus radiata :: Monterey Pine"}

``_shape=arrayfirst`` looks like this::

    [1, 2, 3]

``_shape=object`` looks like this::

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
    If the :ref:`permissions_execute_sql` permission is enabled, this parameter
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
looks like::

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

    Link: https://latest.datasette.io/fixtures/sortable.json; rel="alternate"; type="application/json+datasette"
