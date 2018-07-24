Running SQL queries
===================

Datasette treats SQLite database files as read-only and immutable. This means it
is not possible to execute INSERT or UPDATE statements using Datasette, which
allows us to expose SELECT statements to the outside world without needing to
worry about SQL injection attacks.

The easiest way to execute custom SQL against Datasette is through the web UI.
The database index page includes a SQL editor that lets you run any SELECT query
you like. You can also construct queries using the filter interface on the
tables page, then click "View and edit SQL" to open that query in the cgustom
SQL editor.

Any Datasette SQL query is reflected in the URL of the page, allowing you to
bookmark them, share them with others and navigate through previous queries
using your browser back button.

You can also retrieve the results of any query as JSON by adding ``.json`` to
the base URL.

Named parameters
----------------

Datasette has special support for SQLite named parameters. Consider a SQL query
like this:

.. code-block:: sql

    select * from Street_Tree_List
    where "PermitNotes" like :notes
    and "qSpecies" = :species

If you execute this query using the custom query editor, Datasette will extract
the two named parameters and use them to construct form fields for you to
provide values.

You can also provide values for these fields by constructing a URL::

    /mydatabase?sql=select...&species=44

SQLite string escaping rules will be applied to values passed using named
parameters - they will be wrapped in quotes and their content will be correctly
escaped.

Datasette disallows custom SQL containing the string PRAGMA, as SQLite pragma
statements can be used to change database settings at runtime. If you need to
include the string "pragma" in a query you can do so safely using a named
parameter.

Views
-----

If you want to bundle some pre-written SQL queries with your Datasette-hosted
database you can do so in two ways. The first is to include SQL views in your
database - Datasette will then list those views on your database index page.

The easiest way to create views is with the SQLite command-line interface::

    $ sqlite3 sf-trees.db
    SQLite version 3.19.3 2017-06-27 16:48:08
    Enter ".help" for usage hints.
    sqlite> CREATE VIEW demo_view AS select qSpecies from Street_Tree_List;
    <CTRL+D>

.. _canned_queries:

Canned queries
--------------

As an alternative to adding views to your database, you can define canned
queries inside your ``metadata.json`` file. Here's an example::

    {
        "databases": {
           "sf-trees": {
               "queries": {
                   "just_species": {
                       "sql": select qSpecies from Street_Tree_List"
                   }
               }
           }
        }
    }

Then run datasette like this::

    datasette sf-trees.db -m metadata.json

Each canned query will be listed on the database index page, and will also get
its own URL at::

    /database-name/canned-query-name

For the above example, that URL would be::

    /sf-trees/just_species

You can optionally include ``"title"`` and ``"description"`` keys to show a
title and description on the canned query page. As with regular table metadata
you can alternatively specify ``"description_html"`` to have your description
rendered as HTML (rather than having HTML special characters escaped).

Canned queries support named parameters, so if you include those in the SQL you
will then be able to enter them using the form fields on the canned query page
or by adding them to the URL. This means canned queries can be used to create
custom JSON APIs based on a carefully designed SQL statement.

Here's an example of a canned query with a named parameter:

.. code-block:: sql

    select neighborhood, facet_cities.name, state
    from facetable join facet_cities on facetable.city_id = facet_cities.id
    where neighborhood like '%' || :text || '%' order by neighborhood;

In the canned query JSON it looks like this::

    {
        "databases": {
           "fixtures": {
               "queries": {
                   "neighborhood_search": {
                       "sql": "select neighborhood, facet_cities.name, state\nfrom facetable join facet_cities on facetable.city_id = facet_cities.id\nwhere neighborhood like '%' || :text || '%' order by neighborhood;",
                       "title": "Search neighborhoods",
                       "description_html": "<b>Demonstrating</b> simple like search"
                   }
               }
           }
        }
    }

You can try this canned query out here:
https://latest.datasette.io/fixtures/neighborhood_search?text=town

Note that we are using SQLite string concatenation here - the ``||`` operator -
to add wildcard ``%`` characters to the string provided by the user.

.. _pagination:

Pagination
----------

Datasette's default table pagination is designed to be extremely efficient. SQL
OFFSET/LIMIT pagination can have a significant performance penalty once you get
into multiple thousands of rows, as each page still requires the database to
scan through every preceding row to find the correct offset.

When paginating through tables, Datasette instead orders the rows in the table
by their primary key and performs a WHERE clause against the last seen primary
key for the previous page. For example:

.. code-block:: sql

    select rowid, * from Tree_List where rowid > 200 order by rowid limit 101

This represents page three for this particular table, with a page size of 100.

Note that we request 101 items in the limit clause rather than 100. This allows
us to detect if we are on the last page of the results: if the query returns
less than 101 rows we know we have reached the end of the pagination set.
Datasette will only return the first 100 rows - the 101st is used purely to
detect if there should be another page.

Since the where clause acts against the index on the primary key, the query is
extremely fast even for records that are a long way into the overall pagination
set.
