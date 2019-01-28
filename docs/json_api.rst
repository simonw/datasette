JSON API
========

Datasette provides a JSON API for your SQLite databases. Anything you can do
through the Datasette user interface can also be accessed as JSON via the API.

To access the API for a page, either click on the ``.json`` link on that page or
edit the URL and add a ``.json`` extension to it.

If you started Datasette with the ``--cors`` option, each JSON endpoint will be
served with the following additional HTTP header::

    Access-Control-Allow-Origin: *

This means JavaScript running on any domain will be able to make cross-origin
requests to fetch the data.

If you start Datasette without the ``--cors`` option only JavaScript running on
the same domain as Datasette will be able to access the API.

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

Special JSON arguments
----------------------

Every Datasette endpoint that can return JSON also accepts the following
querystring arguments:

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

.. _table_arguments:

Special table arguments
-----------------------

The Datasette table view takes a number of special querystring arguments:

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

``?_group_count=COLUMN``
    Executes a SQL query that returns a count of the number of rows matching
    each unique value in that column, with the most common ordered first.

``?_group_count=COLUMN1&_group_count=column2``
    You can pass multiple ``_group_count`` columns to return counts against
    unique combinations of those columns.

``?_next=TOKEN``
    Pagination by continuation token - pass the token that was returned in the
    ``"next"`` property by the previous page.

.. _expand_foreign_keys:

Expanding foreign key references
--------------------------------

Datasette can detect foreign key relationships and resolve those references into
labels. The HTML interface does this by default for every detected foreign key
column - you can turn that off using ``?_labels=off``.

You can request foreign keys be expanded in JSON using the ``_labels=on`` or
``_label=COLUMN`` special querystring parameters. Here's what an expanded row
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
