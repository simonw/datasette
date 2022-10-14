.. _full_text_search:

Full-text search
================

SQLite includes `a powerful mechanism for enabling full-text search <https://www.sqlite.org/fts3.html>`_ against SQLite records. Datasette can detect if a table has had full-text search configured for it in the underlying database and display a search interface for filtering that table.

Here's `an example search <https://register-of-members-interests.datasettes.com/regmem/items?_search=hamper&_sort_desc=date>`__:

.. image:: https://raw.githubusercontent.com/simonw/datasette-screenshots/0.62/non-retina/regmem-search.png
   :alt: Screenshot showing a search for hampers against a table full of items - 453 results are returned.

Datasette automatically detects which tables have been configured for full-text search.

.. _full_text_search_table_view_api:

The table page and table view API
---------------------------------

Table views that support full-text search can be queried using the ``?_search=TERMS`` query string parameter. This will run the search against content from all of the columns that have been included in the index.

Try this example: `fara.datasettes.com/fara/FARA_All_ShortForms?_search=manafort <https://fara.datasettes.com/fara/FARA_All_ShortForms?_search=manafort>`__

SQLite full-text search supports wildcards. This means you can easily implement prefix auto-complete by including an asterisk at the end of the search term - for example::

    /dbname/tablename/?_search=rob*

This will return all records containing at least one word that starts with the letters ``rob``.

You can also run searches against just the content of a specific named column by using ``_search_COLNAME=TERMS`` - for example, this would search for just rows where the ``name`` column in the FTS index mentions ``Sarah``::

    /dbname/tablename/?_search_name=Sarah


.. _full_text_search_advanced_queries:

Advanced SQLite search queries
------------------------------

SQLite full-text search includes support for `a variety of advanced queries <https://www.sqlite.org/fts5.html#full_text_query_syntax>`__, including ``AND``, ``OR``, ``NOT`` and ``NEAR``.

By default Datasette disables these features to ensure they do not cause errors or confusion for users who are not aware of them. You can disable this escaping and use the advanced queries by adding ``&_searchmode=raw`` to the table page query string.

If you want to enable these operators by default for a specific table, you can do so by adding ``"searchmode": "raw"`` to the metadata configuration for that table, see :ref:`full_text_search_table_or_view`.

If that option has been specified in the table metadata but you want to over-ride it and return to the default behavior you can append ``&_searchmode=escaped`` to the query string.

.. _full_text_search_table_or_view:

Configuring full-text search for a table or view
------------------------------------------------

If a table has a corresponding FTS table set up using the ``content=`` argument to ``CREATE VIRTUAL TABLE`` shown below, Datasette will detect it automatically and add a search interface to the table page for that table.

You can also manually configure which table should be used for full-text search using query string parameters or :ref:`metadata`. You can set the associated FTS table for a specific table and you can also set one for a view - if you do that, the page for that SQL view will offer a search option.

Use ``?_fts_table=x`` to over-ride the FTS table for a specific page. If the primary key was something other than ``rowid`` you can use ``?_fts_pk=col`` to set that as well. This is particularly useful for views, for example:

https://latest.datasette.io/fixtures/searchable_view?_fts_table=searchable_fts&_fts_pk=pk

The ``fts_table`` metadata property can be used to specify an associated FTS table. If the primary key column in your table which was used to populate the FTS table is something other than ``rowid``, you can specify the column to use with the ``fts_pk`` property.

The ``"searchmode": "raw"`` property can be used to default the table to accepting SQLite advanced search operators, as described in :ref:`full_text_search_advanced_queries`.

Here is an example which enables full-text search (with SQLite advanced search operators) for a ``display_ads`` view which is defined against the ``ads`` table and hence needs to run FTS against the ``ads_fts`` table, using the ``id`` as the primary key:

.. code-block:: json

    {
        "databases": {
            "russian-ads": {
                "tables": {
                    "display_ads": {
                        "fts_table": "ads_fts",
                        "fts_pk": "id",
                        "searchmode": "raw"
                    }
                }
            }
        }
    }

.. _full_text_search_custom_sql:

Searches using custom SQL
-------------------------

You can include full-text search results in custom SQL queries. The general pattern with SQLite search is to run the search as a sub-select that returns rowid values, then include those rowids in another part of the query.

You can see the syntax for a basic search by running that search on a table page and then clicking "View and edit SQL" to see the underlying SQL. For example, consider this search for `manafort is the US FARA database <https://fara.datasettes.com/fara/FARA_All_ShortForms?_search=manafort>`_::

    /fara/FARA_All_ShortForms?_search=manafort

If you click `View and edit SQL <https://fara.datasettes.com/fara?sql=select%0D%0A++rowid%2C%0D%0A++Short_Form_Termination_Date%2C%0D%0A++Short_Form_Date%2C%0D%0A++Short_Form_Last_Name%2C%0D%0A++Short_Form_First_Name%2C%0D%0A++Registration_Number%2C%0D%0A++Registration_Date%2C%0D%0A++Registrant_Name%2C%0D%0A++Address_1%2C%0D%0A++Address_2%2C%0D%0A++City%2C%0D%0A++State%2C%0D%0A++Zip%0D%0Afrom%0D%0A++FARA_All_ShortForms%0D%0Awhere%0D%0A++rowid+in+%28%0D%0A++++select%0D%0A++++++rowid%0D%0A++++from%0D%0A++++++FARA_All_ShortForms_fts%0D%0A++++where%0D%0A++++++FARA_All_ShortForms_fts+match+escape_fts%28%3Asearch%29%0D%0A++%29%0D%0Aorder+by%0D%0A++rowid%0D%0Alimit%0D%0A++101&search=manafort>`_ you'll see that the underlying SQL looks like this:

.. code-block:: sql

    select
      rowid,
      Short_Form_Termination_Date,
      Short_Form_Date,
      Short_Form_Last_Name,
      Short_Form_First_Name,
      Registration_Number,
      Registration_Date,
      Registrant_Name,
      Address_1,
      Address_2,
      City,
      State,
      Zip
    from
      FARA_All_ShortForms
    where
      rowid in (
        select
          rowid
        from
          FARA_All_ShortForms_fts
        where
          FARA_All_ShortForms_fts match escape_fts(:search)
      )
    order by
      rowid
    limit
      101

.. _full_text_search_enabling:

Enabling full-text search for a SQLite table
--------------------------------------------

Datasette takes advantage of the `external content <https://www.sqlite.org/fts3.html#_external_content_fts4_tables_>`_ mechanism in SQLite, which allows a full-text search virtual table to be associated with the contents of another SQLite table.

To set up full-text search for a table, you need to do two things:

* Create a new FTS virtual table associated with your table
* Populate that FTS table with the data that you would like to be able to run searches against

Configuring FTS using sqlite-utils
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

`sqlite-utils <https://sqlite-utils.datasette.io/>`__ is a CLI utility and Python library for manipulating SQLite databases. You can use `it from Python code <https://sqlite-utils.datasette.io/en/latest/python-api.html#enabling-full-text-search>`__ to configure FTS search, or you can achieve the same goal `using the accompanying command-line tool <https://sqlite-utils.datasette.io/en/latest/cli.html#configuring-full-text-search>`__.

Here's how to use ``sqlite-utils`` to enable full-text search for an ``items`` table across the ``name`` and ``description`` columns::

    $ sqlite-utils enable-fts mydatabase.db items name description

Configuring FTS using csvs-to-sqlite
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If your data starts out in CSV files, you can use Datasette's companion tool `csvs-to-sqlite <https://github.com/simonw/csvs-to-sqlite>`__ to convert that file into a SQLite database and enable full-text search on specific columns. For a file called ``items.csv`` where you want full-text search to operate against the ``name`` and ``description`` columns you would run the following::

    $ csvs-to-sqlite items.csv items.db -f name -f description

Configuring FTS by hand
~~~~~~~~~~~~~~~~~~~~~~~

We recommend using `sqlite-utils <https://sqlite-utils.datasette.io/>`__, but if you want to hand-roll a SQLite full-text search table you can do so using the following SQL.

To enable full-text search for a table called ``items`` that works against the ``name`` and ``description`` columns, you would run this SQL to create a new ``items_fts`` FTS virtual table:

.. code-block:: sql

    CREATE VIRTUAL TABLE "items_fts" USING FTS4 (
        name,
        description,
        content="items"
    );

This creates a set of tables to power full-text search against ``items``. The new ``items_fts`` table will be detected by Datasette as the ``fts_table`` for the ``items`` table.

Creating the table is not enough: you also need to populate it with a copy of the data that you wish to make searchable. You can do that using the following SQL:

.. code-block:: sql

    INSERT INTO "items_fts" (rowid, name, description)
        SELECT rowid, name, description FROM items;

If your table has columns that are foreign key references to other tables you can include that data in your full-text search index using a join. Imagine the ``items`` table has a foreign key column called ``category_id`` which refers to a ``categories`` table - you could create a full-text search table like this:

.. code-block:: sql

    CREATE VIRTUAL TABLE "items_fts" USING FTS4 (
        name,
        description,
        category_name,
        content="items"
    );

And then populate it like this:

.. code-block:: sql

    INSERT INTO "items_fts" (rowid, name, description, category_name)
        SELECT items.rowid,
        items.name,
        items.description,
        categories.name
        FROM items JOIN categories ON items.category_id=categories.id;

You can use this technique to populate the full-text search index from any combination of tables and joins that makes sense for your project.

.. _full_text_search_fts_versions:

FTS versions
------------

There are three different versions of the SQLite FTS module: FTS3, FTS4 and FTS5. You can tell which versions are supported by your instance of Datasette by checking the ``/-/versions`` page.

FTS5 is the most advanced module but may not be available in the SQLite version that is bundled with your Python installation. Most importantly, FTS5 is the only version that has the ability to order by search relevance without needing extra code.

If you can't be sure that FTS5 will be available, you should use FTS4.
