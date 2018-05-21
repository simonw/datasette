.. _config:

Configuration
=============

Datasette provides a number of configuration options. These can be set using the ``--config name:value`` option to ``datasette serve``.

To prevent rogue, long-running queries from making a Datasette instance inaccessible to other users, Datasette imposes some limits on the SQL that you can execute. These are exposed as config options which you can over-ride.

default_page_size
-----------------

The default number of rows returned by the table page. You can over-ride this on a per-page basis using the ``?_size=80`` querystring parameter, provided you do not specify a value higher than the ``max_returned_rows`` setting. You can set this default using ``--config`` like so::

    datasette mydatabase.db --config default_page_size:50


sql_time_limit_ms
-----------------

By default, queries have a time limit of one second. If a query takes longer than this to run Datasette will terminate the query and return an error.

If this time limit is too short for you, you can customize it using the ``sql_time_limit_ms`` limit - for example, to increase it to 3.5 seconds::

    datasette mydatabase.db --config sql_time_limit_ms:3500

You can optionally set a lower time limit for an individual query using the ``?_timelimit=100`` query string argument::

    /my-database/my-table?qSpecies=44&_timelimit=100

This would set the time limit to 100ms for that specific query. This feature is useful if you are working with databases of unknown size and complexity - a query that might make perfect sense for a smaller table could take too long to execute on a table with millions of rows. By setting custom time limits you can execute queries "optimistically" - e.g. give me an exact count of rows matching this query but only if it takes less than 100ms to calculate.

max_returned_rows
-----------------

Datasette returns a maximum of 1,000 rows of data at a time. If you execute a query that returns more than 1,000 rows, Datasette will return the first 1,000 and include a warning that the result set has been truncated. You can use OFFSET/LIMIT or other methods in your SQL to implement pagination if you need to return more than 1,000 rows.

You can increase or decrease this limit like so::

    datasette mydatabase.db --config max_returned_rows:2000

default_facet_size
------------------

The default number of unique rows returned by :ref:`facets` is 30. You can customize it like this::

    datasette mydatabase.db --config default_facet_size:50

facet_time_limit_ms
-------------------

This is the time limit Datasette allows for calculating a facet, which defaults to 200ms::

    datasette mydatabase.db --config facet_time_limit_ms:1000

facet_suggest_time_limit_ms
---------------------------

When Datasette calculates suggested facets it needs to run a SQL query for every column in your table. The default for this time limit is 50ms to account for the fact that it needs to run once for every column. If the time limit is exceeded the column will not be suggested as a facet.

You can increase this time limit like so::

    datasette mydatabase.db --config facet_suggest_time_limit_ms:500
