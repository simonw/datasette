.. _sql:

Running SQL queries
===================

Datasette treats SQLite database files as read-only and immutable. This means it is not possible to execute INSERT or UPDATE statements using Datasette, which allows us to expose SELECT statements to the outside world without needing to worry about SQL injection attacks.

The easiest way to execute custom SQL against Datasette is through the web UI. The database index page includes a SQL editor that lets you run any SELECT query you like. You can also construct queries using the filter interface on the tables page, then click "View and edit SQL" to open that query in the custom SQL editor.

Note that this interface is only available if the :ref:`actions_execute_sql` permission is allowed. See :ref:`authentication_permissions_execute_sql`.

Any Datasette SQL query is reflected in the URL of the page, allowing you to bookmark them, share them with others and navigate through previous queries using your browser back button.

You can also retrieve the results of any query as JSON by adding ``.json`` to the base URL.

.. _sql_parameters:

Named parameters
----------------

Datasette has special support for SQLite named parameters. Consider a SQL query like this:

.. code-block:: sql

    select * from Street_Tree_List
    where "PermitNotes" like :notes
    and "qSpecies" = :species

If you execute this query using the custom query editor, Datasette will extract the two named parameters and use them to construct form fields for you to provide values.

You can also provide values for these fields by constructing a URL::

    /mydatabase?sql=select...&species=44

SQLite string escaping rules will be applied to values passed using named parameters - they will be wrapped in quotes and their content will be correctly escaped.

Values from named parameters are treated as SQLite strings. If you need to perform numeric comparisons on them you should cast them to an integer or float first using ``cast(:name as integer)`` or ``cast(:name as real)``, for example:

.. code-block:: sql

    select * from Street_Tree_List
    where latitude > cast(:min_latitude as real)
    and latitude < cast(:max_latitude as real)

Datasette disallows custom SQL queries containing the string PRAGMA (with a small number `of exceptions <https://github.com/simonw/datasette/issues/761>`__) as SQLite pragma statements can be used to change database settings at runtime. If you need to include the string "pragma" in a query you can do so safely using a named parameter.

.. _sql_views:

Views
-----

If you want to bundle some pre-written SQL queries with your Datasette-hosted database you can do so in two ways. The first is to include SQL views in your database - Datasette will then list those views on your database index page.

The quickest way to create views is with the SQLite command-line interface::

    sqlite3 sf-trees.db

::

    SQLite version 3.19.3 2017-06-27 16:48:08
    Enter ".help" for usage hints.
    sqlite> CREATE VIEW demo_view AS select qSpecies from Street_Tree_List;
    <CTRL+D>

You can also use the `sqlite-utils <https://sqlite-utils.datasette.io/>`__ tool to `create a view <https://sqlite-utils.datasette.io/en/stable/cli.html#creating-views>`__::

    sqlite-utils create-view sf-trees.db demo_view "select qSpecies from Street_Tree_List"

.. _canned_queries:

Canned queries
--------------

As an alternative to adding views to your database, you can define canned queries inside your ``datasette.yaml`` file. Here's an example:

.. [[[cog
    from metadata_doc import config_example, config_example
    config_example(cog, {
        "databases": {
           "sf-trees": {
               "queries": {
                   "just_species": {
                       "sql": "select qSpecies from Street_Tree_List"
                   }
               }
           }
        }
    })
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        databases:
          sf-trees:
            queries:
              just_species:
                sql: select qSpecies from Street_Tree_List


.. tab:: datasette.json

    .. code-block:: json

        {
          "databases": {
            "sf-trees": {
              "queries": {
                "just_species": {
                  "sql": "select qSpecies from Street_Tree_List"
                }
              }
            }
          }
        }
.. [[[end]]]

Then run Datasette like this::

    datasette sf-trees.db -m metadata.json

Each canned query will be listed on the database index page, and will also get its own URL at::

    /database-name/canned-query-name

For the above example, that URL would be::

    /sf-trees/just_species

You can optionally include ``"title"`` and ``"description"`` keys to show a title and description on the canned query page. As with regular table metadata you can alternatively specify ``"description_html"`` to have your description rendered as HTML (rather than having HTML special characters escaped).

.. _canned_queries_named_parameters:

Canned query parameters
~~~~~~~~~~~~~~~~~~~~~~~

Canned queries support named parameters, so if you include those in the SQL you will then be able to enter them using the form fields on the canned query page or by adding them to the URL. This means canned queries can be used to create custom JSON APIs based on a carefully designed SQL statement.

Here's an example of a canned query with a named parameter:

.. code-block:: sql

    select neighborhood, facet_cities.name, state
    from facetable
      join facet_cities on facetable.city_id = facet_cities.id
    where neighborhood like '%' || :text || '%'
    order by neighborhood;

In the canned query configuration looks like this:


.. [[[cog
    config_example(cog, """
    databases:
      fixtures:
        queries:
          neighborhood_search:
            title: Search neighborhoods
            sql: |-
              select neighborhood, facet_cities.name, state
              from facetable
                join facet_cities on facetable.city_id = facet_cities.id
              where neighborhood like '%' || :text || '%'
              order by neighborhood
    """)
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml


        databases:
          fixtures:
            queries:
              neighborhood_search:
                title: Search neighborhoods
                sql: |-
                  select neighborhood, facet_cities.name, state
                  from facetable
                    join facet_cities on facetable.city_id = facet_cities.id
                  where neighborhood like '%' || :text || '%'
                  order by neighborhood


.. tab:: datasette.json

    .. code-block:: json

        {
          "databases": {
            "fixtures": {
              "queries": {
                "neighborhood_search": {
                  "title": "Search neighborhoods",
                  "sql": "select neighborhood, facet_cities.name, state\nfrom facetable\n  join facet_cities on facetable.city_id = facet_cities.id\nwhere neighborhood like '%' || :text || '%'\norder by neighborhood"
                }
              }
            }
          }
        }
.. [[[end]]]

Note that we are using SQLite string concatenation here - the ``||`` operator - to add wildcard ``%`` characters to the string provided by the user.

You can try this canned query out here:
https://latest.datasette.io/fixtures/neighborhood_search?text=town

In this example the ``:text`` named parameter is automatically extracted from the query using a regular expression.

You can alternatively provide an explicit list of named parameters using the ``"params"`` key, like this:

.. [[[cog
    config_example(cog, """
    databases:
      fixtures:
        queries:
          neighborhood_search:
            title: Search neighborhoods
            params:
            - text
            sql: |-
              select neighborhood, facet_cities.name, state
              from facetable
                join facet_cities on facetable.city_id = facet_cities.id
              where neighborhood like '%' || :text || '%'
              order by neighborhood
    """)
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml


        databases:
          fixtures:
            queries:
              neighborhood_search:
                title: Search neighborhoods
                params:
                - text
                sql: |-
                  select neighborhood, facet_cities.name, state
                  from facetable
                    join facet_cities on facetable.city_id = facet_cities.id
                  where neighborhood like '%' || :text || '%'
                  order by neighborhood


.. tab:: datasette.json

    .. code-block:: json

        {
          "databases": {
            "fixtures": {
              "queries": {
                "neighborhood_search": {
                  "title": "Search neighborhoods",
                  "params": [
                    "text"
                  ],
                  "sql": "select neighborhood, facet_cities.name, state\nfrom facetable\n  join facet_cities on facetable.city_id = facet_cities.id\nwhere neighborhood like '%' || :text || '%'\norder by neighborhood"
                }
              }
            }
          }
        }
.. [[[end]]]

.. _dictionary_based_canned_query_parameters:

Dictionary-based canned query parameters
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The items in the ``params`` list can also be dictionaries. This allows for more detailed configuration of each parameter. When using a dictionary, it **must** have a ``name`` key. The following optional keys can also be used:

*   ``description``: A string providing a human-readable description for the parameter. In the web interface, this description will be used as the ``title`` attribute for the input field, typically appearing as a tooltip when the user hovers over the field.
*   ``default``: A string specifying the default value for the parameter. This value will pre-populate the input field in the form.

Here's an example of a canned query that uses dictionary-based parameter definitions to provide descriptions and default values:

.. [[[cog
    config_example(cog, """
    databases:
      my_store:
        queries:
          product_filter:
            title: Filter Products
            sql: |-
              SELECT name, price, category, stock_quantity
              FROM products
              WHERE category = :category
                AND price < :max_price
                AND stock_quantity >= :min_stock
              LIMIT :results_limit;
            params:
              - name: "category"
                description: "The product category to filter by (e.g., electronics, books)"
                default: "electronics"
              - name: "max_price"
                description: "The maximum price for the product (e.g., 100.00)"
                default: "100.00"
              - name: "min_stock"
                description: "Minimum stock quantity"
                default: "1"
              - name: "results_limit"
                description: "Maximum number of results to return"
                default: "10"
    """)
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml


        databases:
          my_store:
            queries:
              product_filter:
                title: Filter Products
                sql: |-
                  SELECT name, price, category, stock_quantity
                  FROM products
                  WHERE category = :category
                    AND price < :max_price
                    AND stock_quantity >= :min_stock
                  LIMIT :results_limit;
                params:
                  - name: "category"
                    description: "The product category to filter by (e.g., electronics, books)"
                    default: "electronics"
                  - name: "max_price"
                    description: "The maximum price for the product (e.g., 100.00)"
                    default: "100.00"
                  - name: "min_stock"
                    description: "Minimum stock quantity"
                    default: "1"
                  - name: "results_limit"
                    description: "Maximum number of results to return"
                    default: "10"


.. tab:: datasette.json

    .. code-block:: json

        {
          "databases": {
            "my_store": {
              "queries": {
                "product_filter": {
                  "title": "Filter Products",
                  "sql": "SELECT name, price, category, stock_quantity\nFROM products\nWHERE category = :category\n  AND price < :max_price\n  AND stock_quantity >= :min_stock\nLIMIT :results_limit;",
                  "params": [
                    {
                      "name": "category",
                      "description": "The product category to filter by (e.g., electronics, books)",
                      "default": "electronics"
                    },
                    {
                      "name": "max_price",
                      "description": "The maximum price for the product (e.g., 100.00)",
                      "default": "100.00"
                    },
                    {
                      "name": "min_stock",
                      "description": "Minimum stock quantity",
                      "default": "1"
                    },
                    {
                      "name": "results_limit",
                      "description": "Maximum number of results to return",
                      "default": "10"
                    }
                  ]
                }
              }
            }
          }
        }
.. [[[end]]]

This configuration would generate a form with four input fields. The "category" field would default to "electronics" and have a tooltip explaining its purpose. Similar defaults and tooltips would apply to "max_price", "min_stock", and "results_limit".


.. _canned_queries_options:

Additional canned query options
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Additional options can be specified for canned queries in the YAML or JSON configuration.

hide_sql
++++++++

Canned queries default to displaying their SQL query at the top of the page. If the query is extremely long you may want to hide it by default, with a "show" link that can be used to make it visible.

Add the ``"hide_sql": true`` option to hide the SQL query by default.

fragment
++++++++

Some plugins, such as `datasette-vega <https://github.com/simonw/datasette-vega>`__, can be configured by including additional data in the fragment hash of the URL - the bit that comes after a ``#`` symbol.

You can set a default fragment hash that will be included in the link to the canned query from the database index page using the ``"fragment"`` key.

This example demonstrates both ``fragment`` and ``hide_sql``:

.. [[[cog
    config_example(cog, """
    databases:
      fixtures:
        queries:
          neighborhood_search:
            fragment: fragment-goes-here
            hide_sql: true
            sql: |-
              select neighborhood, facet_cities.name, state
              from facetable join facet_cities on facetable.city_id = facet_cities.id
              where neighborhood like '%' || :text || '%' order by neighborhood;
    """)
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml


        databases:
          fixtures:
            queries:
              neighborhood_search:
                fragment: fragment-goes-here
                hide_sql: true
                sql: |-
                  select neighborhood, facet_cities.name, state
                  from facetable join facet_cities on facetable.city_id = facet_cities.id
                  where neighborhood like '%' || :text || '%' order by neighborhood;


.. tab:: datasette.json

    .. code-block:: json

        {
          "databases": {
            "fixtures": {
              "queries": {
                "neighborhood_search": {
                  "fragment": "fragment-goes-here",
                  "hide_sql": true,
                  "sql": "select neighborhood, facet_cities.name, state\nfrom facetable join facet_cities on facetable.city_id = facet_cities.id\nwhere neighborhood like '%' || :text || '%' order by neighborhood;"
                }
              }
            }
          }
        }
.. [[[end]]]

`See here <https://latest.datasette.io/fixtures#queries>`__ for a demo of this in action.

.. _canned_queries_writable:

Writable canned queries
~~~~~~~~~~~~~~~~~~~~~~~

Canned queries by default are read-only. You can use the ``"write": true`` key to indicate that a canned query can write to the database.

See :ref:`authentication_permissions_query` for details on how to add permission checks to canned queries, using the ``"allow"`` key.

.. [[[cog
    config_example(cog, {
        "databases": {
            "mydatabase": {
                "queries": {
                    "add_name": {
                        "sql": "INSERT INTO names (name) VALUES (:name)",
                        "write": True
                    }
                }
            }
        }
    })
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        databases:
          mydatabase:
            queries:
              add_name:
                sql: INSERT INTO names (name) VALUES (:name)
                write: true


.. tab:: datasette.json

    .. code-block:: json

        {
          "databases": {
            "mydatabase": {
              "queries": {
                "add_name": {
                  "sql": "INSERT INTO names (name) VALUES (:name)",
                  "write": true
                }
              }
            }
          }
        }
.. [[[end]]]

This configuration will create a page at ``/mydatabase/add_name`` displaying a form with a ``name`` field. Submitting that form will execute the configured ``INSERT`` query.

You can customize how Datasette represents success and errors using the following optional properties:

- ``on_success_message`` - the message shown when a query is successful
- ``on_success_message_sql`` - alternative to ``on_success_message``: a SQL query that should be executed to generate the message
- ``on_success_redirect`` - the path or URL the user is redirected to on success
- ``on_error_message`` - the message shown when a query throws an error
- ``on_error_redirect`` - the path or URL the user is redirected to on error

For example:

.. [[[cog
    config_example(cog, {
        "databases": {
            "mydatabase": {
                "queries": {
                    "add_name": {
                        "sql": "INSERT INTO names (name) VALUES (:name)",
                        "params": ["name"],
                        "write": True,
                        "on_success_message_sql": "select 'Name inserted: ' || :name",
                        "on_success_redirect": "/mydatabase/names",
                        "on_error_message": "Name insert failed",
                        "on_error_redirect": "/mydatabase",
                    }
                }
            }
        }
    })
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        databases:
          mydatabase:
            queries:
              add_name:
                sql: INSERT INTO names (name) VALUES (:name)
                params:
                - name
                write: true
                on_success_message_sql: 'select ''Name inserted: '' || :name'
                on_success_redirect: /mydatabase/names
                on_error_message: Name insert failed
                on_error_redirect: /mydatabase


.. tab:: datasette.json

    .. code-block:: json

        {
          "databases": {
            "mydatabase": {
              "queries": {
                "add_name": {
                  "sql": "INSERT INTO names (name) VALUES (:name)",
                  "params": [
                    "name"
                  ],
                  "write": true,
                  "on_success_message_sql": "select 'Name inserted: ' || :name",
                  "on_success_redirect": "/mydatabase/names",
                  "on_error_message": "Name insert failed",
                  "on_error_redirect": "/mydatabase"
                }
              }
            }
          }
        }
.. [[[end]]]

You can use ``"params"`` to explicitly list the named parameters that should be displayed as form fields - otherwise they will be automatically detected. ``"params"`` is not necessary in the above example, since without it ``"name"`` would be automatically detected from the query.

You can pre-populate form fields when the page first loads using a query string, e.g. ``/mydatabase/add_name?name=Prepopulated``. The user will have to submit the form to execute the query.

If you specify a query in ``"on_success_message_sql"``, that query will be executed after the main query. The first column of the first row return by that query will be displayed as a success message. Named parameters from the main query will be made available to the success message query as well.

.. _canned_queries_magic_parameters:

Magic parameters
~~~~~~~~~~~~~~~~

Named parameters that start with an underscore are special: they can be used to automatically add values created by Datasette that are not contained in the incoming form fields or query string.

These magic parameters are only supported for canned queries: to avoid security issues (such as queries that extract the user's private cookies) they are not available to SQL that is executed by the user as a custom SQL query.

Available magic parameters are:

``_actor_*`` - e.g. ``_actor_id``, ``_actor_name``
    Fields from the currently authenticated :ref:`authentication_actor`.

``_header_*`` - e.g. ``_header_user_agent``
    Header from the incoming HTTP request. The key should be in lower case and with hyphens converted to underscores e.g. ``_header_user_agent`` or ``_header_accept_language``.

``_cookie_*`` - e.g. ``_cookie_lang``
    The value of the incoming cookie of that name.

``_now_epoch``
    The number of seconds since the Unix epoch.

``_now_date_utc``
    The date in UTC, e.g. ``2020-06-01``

``_now_datetime_utc``
    The ISO 8601 datetime in UTC, e.g. ``2020-06-24T18:01:07Z``

``_random_chars_*`` - e.g. ``_random_chars_128``
    A random string of characters of the specified length.

Here's an example configuration that adds a message from the authenticated user, storing various pieces of additional metadata using magic parameters:

.. [[[cog
    config_example(cog, """
    databases:
      mydatabase:
        queries:
          add_message:
            allow:
              id: "*"
            sql: |-
              INSERT INTO messages (
                user_id, message, datetime
              ) VALUES (
                :_actor_id, :message, :_now_datetime_utc
              )
            write: true
    """)
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml


        databases:
          mydatabase:
            queries:
              add_message:
                allow:
                  id: "*"
                sql: |-
                  INSERT INTO messages (
                    user_id, message, datetime
                  ) VALUES (
                    :_actor_id, :message, :_now_datetime_utc
                  )
                write: true


.. tab:: datasette.json

    .. code-block:: json

        {
          "databases": {
            "mydatabase": {
              "queries": {
                "add_message": {
                  "allow": {
                    "id": "*"
                  },
                  "sql": "INSERT INTO messages (\n  user_id, message, datetime\n) VALUES (\n  :_actor_id, :message, :_now_datetime_utc\n)",
                  "write": true
                }
              }
            }
          }
        }
.. [[[end]]]

The form presented at ``/mydatabase/add_message`` will have just a field for ``message`` - the other parameters will be populated by the magic parameter mechanism.

Additional custom magic parameters can be added by plugins using the :ref:`plugin_hook_register_magic_parameters` hook.

.. _canned_queries_json_api:

JSON API for writable canned queries
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Writable canned queries can also be accessed using a JSON API. You can POST data to them using JSON, and you can request that their response is returned to you as JSON.

To submit JSON to a writable canned query, encode key/value parameters as a JSON document::

    POST /mydatabase/add_message

    {"message": "Message goes here"}

You can also continue to submit data using regular form encoding, like so::

    POST /mydatabase/add_message

    message=Message+goes+here

There are three options for specifying that you would like the response to your request to return JSON data, as opposed to an HTTP redirect to another page.

- Set an ``Accept: application/json`` header on your request
- Include ``?_json=1`` in the URL that you POST to
- Include ``"_json": 1`` in your JSON body, or ``&_json=1`` in your form encoded body

The JSON response will look like this:

.. code-block:: json

    {
        "ok": true,
        "message": "Query executed, 1 row affected",
        "redirect": "/data/add_name"
    }

The ``"message"`` and ``"redirect"`` values here will take into account ``on_success_message``, ``on_success_message_sql``,  ``on_success_redirect``, ``on_error_message`` and ``on_error_redirect``, if they have been set.

.. _pagination:

Pagination
----------

Datasette's default table pagination is designed to be extremely efficient. SQL OFFSET/LIMIT pagination can have a significant performance penalty once you get into multiple thousands of rows, as each page still requires the database to scan through every preceding row to find the correct offset.

When paginating through tables, Datasette instead orders the rows in the table by their primary key and performs a WHERE clause against the last seen primary key for the previous page. For example:

.. code-block:: sql

    select rowid, * from Tree_List where rowid > 200 order by rowid limit 101

This represents page three for this particular table, with a page size of 100.

Note that we request 101 items in the limit clause rather than 100. This allows us to detect if we are on the last page of the results: if the query returns less than 101 rows we know we have reached the end of the pagination set. Datasette will only return the first 100 rows - the 101st is used purely to detect if there should be another page.

Since the where clause acts against the index on the primary key, the query is extremely fast even for records that are a long way into the overall pagination set.

.. _cross_database_queries:

Cross-database queries
----------------------

SQLite has the ability to run queries that join across multiple databases. Up to ten databases can be attached to a single SQLite connection and queried together.

Datasette can execute joins across multiple databases if it is started with the ``--crossdb`` option::

    datasette fixtures.db extra_database.db --crossdb

If it is started in this way, the ``/_memory`` page can be used to execute queries that join across multiple databases.

References to tables in attached databases should be preceded by the database name and a period.

For example, this query will show a list of tables across both of the above databases:

.. code-block:: sql

    select
      'fixtures' as database, *
    from
      [fixtures].sqlite_master
    union
    select
      'extra_database' as database, *
    from
      [extra_database].sqlite_master

`Try that out here <https://latest.datasette.io/_memory?sql=select%0D%0A++%27fixtures%27+as+database%2C+*%0D%0Afrom%0D%0A++%5Bfixtures%5D.sqlite_master%0D%0Aunion%0D%0Aselect%0D%0A++%27extra_database%27+as+database%2C+*%0D%0Afrom%0D%0A++%5Bextra_database%5D.sqlite_master>`__.
