.. _pages:

=========================
 Pages and API endpoints
=========================

The Datasette web application offers a number of different pages that can be accessed to explore the data in question, each of which is accompanied by an equivalent JSON API.

.. _IndexView:

Top-level index
===============

The root page of any Datasette installation is an index page that lists all of the currently attached databases. Some examples:

* `fivethirtyeight.datasettes.com <https://fivethirtyeight.datasettes.com/>`_
* `register-of-members-interests.datasettes.com <https://register-of-members-interests.datasettes.com/>`_

Add ``/.json`` to the end of the URL for the JSON version of the underlying data:

* `fivethirtyeight.datasettes.com/.json <https://fivethirtyeight.datasettes.com/.json>`_
* `register-of-members-interests.datasettes.com/.json <https://register-of-members-interests.datasettes.com/.json>`_

The index page can also be accessed at ``/-/``, useful for if the default index page has been replaced using an :ref:`index.html custom template <customization_custom_templates>`. The ``/-/`` page will always render the default Datasette ``index.html`` template.

.. _DatabaseView:

Database
========

Each database has a page listing the tables, views and canned queries available for that database. If the :ref:`actions_execute_sql` permission is enabled (it's on by default) there will also be an interface for executing arbitrary SQL select queries against the data.

Examples:

* `fivethirtyeight.datasettes.com/fivethirtyeight <https://fivethirtyeight.datasettes.com/fivethirtyeight>`_
* `datasette.io/global-power-plants <https://datasette.io/global-power-plants>`_

The JSON version of this page provides programmatic access to the underlying data:

* `fivethirtyeight.datasettes.com/fivethirtyeight.json <https://fivethirtyeight.datasettes.com/fivethirtyeight.json>`_
* `datasette.io/global-power-plants.json <https://datasette.io/global-power-plants.json>`_

.. _DatabaseView_hidden:

Hidden tables
-------------

Some tables listed on the database page are treated as hidden. Hidden tables are not completely invisible - they can be accessed through the "hidden tables" link at the bottom of the page. They are hidden because they represent low-level implementation details which are generally not useful to end-users of Datasette.

The following tables are hidden by default:

- Any table with a name that starts with an underscore - this is a Datasette convention to help plugins easily hide their own internal tables.
- Tables that have been configured as ``"hidden": true`` using :ref:`metadata_hiding_tables`.
- ``*_fts`` tables that implement SQLite full-text search indexes.
- Tables relating to the inner workings of the SpatiaLite SQLite extension.
- ``sqlite_stat`` tables used to store statistics used by the query optimizer.

.. _QueryView:

Queries
=======

The ``/database-name/-/query`` page can be used to execute an arbitrary SQL query against that database, if the :ref:`actions_execute_sql` permission is enabled. This query is passed as the ``?sql=`` query string parameter.

This means you can link directly to a query by constructing the following URL:

``/database-name/-/query?sql=SELECT+*+FROM+table_name``

Each configured :ref:`canned query <canned_queries>` has its own page, at ``/database-name/query-name``. Viewing this page will execute the query and display the results.

In both cases adding a ``.json`` extension to the URL will return the results as JSON.

.. _TableView:

Table
=====

The table page is the heart of Datasette: it allows users to interactively explore the contents of a database table, including sorting, filtering, :ref:`full_text_search` and applying :ref:`facets`.

The HTML interface is worth spending some time exploring. As with other pages, you can return the JSON data by appending ``.json`` to the URL path, before any `?` query string arguments.

The query string arguments are described in more detail here: :ref:`table_arguments`

You can also use the table page to interactively construct a SQL query - by applying different filters and a sort order for example - and then click the "View and edit SQL" link to see the SQL query that was used for the page and edit and re-submit it.

Some examples:

* `../items <https://register-of-members-interests.datasettes.com/regmem/items>`_ lists all of the line-items registered by UK MPs as potential conflicts of interest. It demonstrates Datasette's support for :ref:`full_text_search`.
* `../antiquities-act%2Factions_under_antiquities_act <https://fivethirtyeight.datasettes.com/fivethirtyeight/antiquities-act%2Factions_under_antiquities_act>`_ is an interface for exploring the "actions under the antiquities act" data table published by FiveThirtyEight.
* `../global-power-plants?country_long=United+Kingdom&primary_fuel=Gas <https://datasette.io/global-power-plants/global-power-plants?_facet=primary_fuel&_facet=owner&_facet=country_long&country_long__exact=United+Kingdom&primary_fuel=Gas>`_ is a filtered table page showing every Gas power plant in the United Kingdom. It includes some default facets (configured using `its metadata.json <https://datasette.io/-/metadata>`_) and uses the `datasette-cluster-map <https://github.com/simonw/datasette-cluster-map>`_ plugin to show a map of the results.

.. _RowView:

Row
===

Every row in every Datasette table has its own URL. This means individual records can be linked to directly.

Table cells with extremely long text contents are truncated on the table view according to the :ref:`setting_truncate_cells_html` setting. If a cell has been truncated the full length version of that cell will be available on the row page.

Rows which are the targets of foreign key references from other tables will show a link to a filtered search for all records that reference that row. Here's an example from the Registers of Members Interests database:

`../people/uk~2Eorg~2Epublicwhip~2Fperson~2F10001 <https://register-of-members-interests.datasettes.com/regmem/people/uk~2Eorg~2Epublicwhip~2Fperson~2F10001>`_

Note that this URL includes the encoded primary key of the record.

Here's that same page as JSON:

`../people/uk~2Eorg~2Epublicwhip~2Fperson~2F10001.json <https://register-of-members-interests.datasettes.com/regmem/people/uk~2Eorg~2Epublicwhip~2Fperson~2F10001.json>`_


.. _pages_schemas:

Schemas
=======

Datasette offers ``/-/schema`` endpoints to expose the SQL schema for databases and tables.

.. _InstanceSchemaView:

Instance schema
---------------

Access ``/-/schema`` to see the complete schema for all attached databases in the Datasette instance.

Use ``/-/schema.md`` to get the same information as Markdown.

Use ``/-/schema.json`` to get the same information as JSON, which looks like this:

.. code-block:: json

    {
      "schemas": [
        {
          "database": "content",
          "schema": "create table posts ..."
        }
    }

.. _DatabaseSchemaView:

Database schema
---------------

Use ``/database-name/-/schema`` to see the complete schema for a specific database. The ``.md`` and ``.json`` extensions work here too. The JSON returns an object with ``"database"`` and ``"schema"`` keys.

.. _TableSchemaView:

Table schema
------------

Use ``/database-name/table-name/-/schema`` to see the schema for a specific table. The ``.md`` and ``.json`` extensions work here too. The JSON returns an object with ``"database"``, ``"table"``, and ``"schema"`` keys.
