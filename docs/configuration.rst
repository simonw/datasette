.. _configuration:

Configuration
=============

Datasette offers several ways to configure your Datasette instances: server settings, plugin configuration, authentication, and more.

Most configuration can be handled using a ``datasette.yaml`` configuration file, passed to datasette using the ``-c/--config`` flag:

.. code-block:: bash

    datasette mydatabase.db --config datasette.yaml

This file can also use JSON, as ``datasette.json``. YAML is recommended over JSON due to its support for comments and multi-line strings.

.. _configuration_cli:

Configuration via the command-line
----------------------------------

The recommended way to configure Datasette is using a ``datasette.yaml`` file passed to ``-c/--config``. You can also pass individual settings to Datasette using the ``-s/--setting`` option, which can be used multiple times:

.. code-block:: bash

    datasette mydatabase.db \
      --setting settings.default_page_size 50 \
      --setting settings.sql_time_limit_ms 3500

This option takes dotted-notation for the first argument and a value for the second argument. This means you can use it to set any configuration value that would be valid in a ``datasette.yaml`` file.

It also works for plugin configuration, for example for `datasette-cluster-map <https://datasette.io/plugins/datasette-cluster-map>`_:

.. code-block:: bash

    datasette mydatabase.db \
      --setting plugins.datasette-cluster-map.latitude_column xlat \
      --setting plugins.datasette-cluster-map.longitude_column xlon

If the value you provide is a valid JSON object or list it will be treated as nested data, allowing you to configure plugins that accept lists such as `datasette-proxy-url <https://datasette.io/plugins/datasette-proxy-url>`_:

.. code-block:: bash

    datasette mydatabase.db \
      -s plugins.datasette-proxy-url.paths '[{"path": "/proxy", "backend": "http://example.com/"}]'

This is equivalent to a ``datasette.yaml`` file containing the following:

.. [[[cog
    from metadata_doc import config_example
    import textwrap
    config_example(cog, textwrap.dedent(
      """
      plugins:
        datasette-proxy-url:
          paths:
          - path: /proxy
            backend: http://example.com/
      """).strip()
      )
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        plugins:
          datasette-proxy-url:
            paths:
            - path: /proxy
              backend: http://example.com/

.. tab:: datasette.json

    .. code-block:: json

        {
          "plugins": {
            "datasette-proxy-url": {
              "paths": [
                {
                  "path": "/proxy",
                  "backend": "http://example.com/"
                }
              ]
            }
          }
        }
.. [[[end]]]

.. _configuration_reference:

``datasette.yaml`` reference
----------------------------

The following example shows some of the valid configuration options that can exist inside ``datasette.yaml``.

.. [[[cog
    from metadata_doc import config_example
    import textwrap
    config_example(cog, textwrap.dedent(
      """
        # Datasette settings block
        settings:
          default_page_size: 50
          sql_time_limit_ms: 3500
          max_returned_rows: 2000

        # top-level plugin configuration
        plugins:
          datasette-my-plugin:
            key: valueA

        # Database and table-level configuration
        databases:
          your_db_name:
            # plugin configuration for the your_db_name database
            plugins:
              datasette-my-plugin:
                key: valueA
            tables:
              your_table_name:
                allow:
                  # Only the root user can access this table
                  id: root
                # plugin configuration for the your_table_name table
                # inside your_db_name database
                plugins:
                  datasette-my-plugin:
                    key: valueB
        """)
      )
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml


        # Datasette settings block
        settings:
          default_page_size: 50
          sql_time_limit_ms: 3500
          max_returned_rows: 2000

        # top-level plugin configuration
        plugins:
          datasette-my-plugin:
            key: valueA

        # Database and table-level configuration
        databases:
          your_db_name:
            # plugin configuration for the your_db_name database
            plugins:
              datasette-my-plugin:
                key: valueA
            tables:
              your_table_name:
                allow:
                  # Only the root user can access this table
                  id: root
                # plugin configuration for the your_table_name table
                # inside your_db_name database
                plugins:
                  datasette-my-plugin:
                    key: valueB


.. tab:: datasette.json

    .. code-block:: json

        {
          "settings": {
            "default_page_size": 50,
            "sql_time_limit_ms": 3500,
            "max_returned_rows": 2000
          },
          "plugins": {
            "datasette-my-plugin": {
              "key": "valueA"
            }
          },
          "databases": {
            "your_db_name": {
              "plugins": {
                "datasette-my-plugin": {
                  "key": "valueA"
                }
              },
              "tables": {
                "your_table_name": {
                  "allow": {
                    "id": "root"
                  },
                  "plugins": {
                    "datasette-my-plugin": {
                      "key": "valueB"
                    }
                  }
                }
              }
            }
          }
        }
.. [[[end]]]

.. _configuration_reference_settings:

Settings
~~~~~~~~

:ref:`settings` can be configured in ``datasette.yaml`` with the ``settings`` key:

.. [[[cog
    from metadata_doc import config_example
    import textwrap
    config_example(cog, textwrap.dedent(
      """
        # inside datasette.yaml
        settings:
          default_allow_sql: off
          default_page_size: 50
        """).strip()
      )
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        # inside datasette.yaml
        settings:
          default_allow_sql: off
          default_page_size: 50

.. tab:: datasette.json

    .. code-block:: json

        {
          "settings": {
            "default_allow_sql": "off",
            "default_page_size": 50
          }
        }
.. [[[end]]]

The full list of settings is available in the :ref:`settings documentation <settings>`. Settings can also be passed to Datasette using one or more ``--setting name value`` command line options.`

.. _configuration_reference_plugins:

Plugin configuration
~~~~~~~~~~~~~~~~~~~~

:ref:`Datasette plugins <plugins>` often require configuration. This plugin configuration should be placed in ``plugins`` keys inside ``datasette.yaml``.

Most plugins are configured at the top-level of the file, using the ``plugins`` key:

.. [[[cog
    from metadata_doc import config_example
    import textwrap
    config_example(cog, textwrap.dedent(
      """
        # inside datasette.yaml
        plugins:
          datasette-my-plugin:
            key: my_value
        """).strip()
      )
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        # inside datasette.yaml
        plugins:
          datasette-my-plugin:
            key: my_value

.. tab:: datasette.json

    .. code-block:: json

        {
          "plugins": {
            "datasette-my-plugin": {
              "key": "my_value"
            }
          }
        }
.. [[[end]]]

Some plugins can be configured at the database or table level. These should use a ``plugins`` key nested under the appropriate place within the ``databases`` object:

.. [[[cog
    from metadata_doc import config_example
    import textwrap
    config_example(cog, textwrap.dedent(
      """
        # inside datasette.yaml
        databases:
          my_database:
            # plugin configuration for the my_database database
            plugins:
              datasette-my-plugin:
                key: my_value
          my_other_database:
            tables:
              my_table:
                # plugin configuration for the my_table table inside the my_other_database database
                plugins:
                  datasette-my-plugin:
                    key: my_value
      """).strip()
      )
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        # inside datasette.yaml
        databases:
          my_database:
            # plugin configuration for the my_database database
            plugins:
              datasette-my-plugin:
                key: my_value
          my_other_database:
            tables:
              my_table:
                # plugin configuration for the my_table table inside the my_other_database database
                plugins:
                  datasette-my-plugin:
                    key: my_value

.. tab:: datasette.json

    .. code-block:: json

        {
          "databases": {
            "my_database": {
              "plugins": {
                "datasette-my-plugin": {
                  "key": "my_value"
                }
              }
            },
            "my_other_database": {
              "tables": {
                "my_table": {
                  "plugins": {
                    "datasette-my-plugin": {
                      "key": "my_value"
                    }
                  }
                }
              }
            }
          }
        }
.. [[[end]]]


.. _configuration_reference_permissions:

Permissions configuration
~~~~~~~~~~~~~~~~~~~~~~~~~

Datasette's :ref:`authentication and permissions <authentication>` system can also be configured using ``datasette.yaml``.

Here is a simple example:

.. [[[cog
    from metadata_doc import config_example
    import textwrap
    config_example(cog, textwrap.dedent(
      """
        # Instance is only available to users 'sharon' and 'percy':
        allow:
          id:
          - sharon
          - percy

        # Only 'percy' is allowed access to the accounting database:
        databases:
          accounting:
            allow:
              id: percy
      """).strip()
      )
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        # Instance is only available to users 'sharon' and 'percy':
        allow:
          id:
          - sharon
          - percy

        # Only 'percy' is allowed access to the accounting database:
        databases:
          accounting:
            allow:
              id: percy

.. tab:: datasette.json

    .. code-block:: json

        {
          "allow": {
            "id": [
              "sharon",
              "percy"
            ]
          },
          "databases": {
            "accounting": {
              "allow": {
                "id": "percy"
              }
            }
          }
        }
.. [[[end]]]

:ref:`authentication_permissions_config` has the full details.

.. _configuration_reference_canned_queries:

Canned queries configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

:ref:`Canned queries <canned_queries>` are named SQL queries that appear in the Datasette interface. They can be configured in ``datasette.yaml`` using the ``queries`` key at the database level:

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

See the :ref:`canned queries documentation <canned_queries>` for more, including how to configure :ref:`writable canned queries <canned_queries_writable>`.

.. _configuration_reference_css_js:

Custom CSS and JavaScript
~~~~~~~~~~~~~~~~~~~~~~~~~

Datasette can load additional CSS and JavaScript files, configured in ``datasette.yaml`` like this:

.. [[[cog
    from metadata_doc import config_example
    config_example(cog, """
        extra_css_urls:
        - https://simonwillison.net/static/css/all.bf8cd891642c.css
        extra_js_urls:
        - https://code.jquery.com/jquery-3.2.1.slim.min.js
    """)
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml


            extra_css_urls:
            - https://simonwillison.net/static/css/all.bf8cd891642c.css
            extra_js_urls:
            - https://code.jquery.com/jquery-3.2.1.slim.min.js


.. tab:: datasette.json

    .. code-block:: json

        {
          "extra_css_urls": [
            "https://simonwillison.net/static/css/all.bf8cd891642c.css"
          ],
          "extra_js_urls": [
            "https://code.jquery.com/jquery-3.2.1.slim.min.js"
          ]
        }
.. [[[end]]]

The extra CSS and JavaScript files will be linked in the ``<head>`` of every page:

.. code-block:: html

    <link rel="stylesheet" href="https://simonwillison.net/static/css/all.bf8cd891642c.css">
    <script src="https://code.jquery.com/jquery-3.2.1.slim.min.js"></script>

You can also specify a SRI (subresource integrity hash) for these assets:

.. [[[cog
    config_example(cog, """
        extra_css_urls:
        - url: https://simonwillison.net/static/css/all.bf8cd891642c.css
          sri: sha384-9qIZekWUyjCyDIf2YK1FRoKiPJq4PHt6tp/ulnuuyRBvazd0hG7pWbE99zvwSznI
        extra_js_urls:
        - url: https://code.jquery.com/jquery-3.2.1.slim.min.js
          sri: sha256-k2WSCIexGzOj3Euiig+TlR8gA0EmPjuc79OEeY5L45g=
    """)
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml


            extra_css_urls:
            - url: https://simonwillison.net/static/css/all.bf8cd891642c.css
              sri: sha384-9qIZekWUyjCyDIf2YK1FRoKiPJq4PHt6tp/ulnuuyRBvazd0hG7pWbE99zvwSznI
            extra_js_urls:
            - url: https://code.jquery.com/jquery-3.2.1.slim.min.js
              sri: sha256-k2WSCIexGzOj3Euiig+TlR8gA0EmPjuc79OEeY5L45g=


.. tab:: datasette.json

    .. code-block:: json

        {
          "extra_css_urls": [
            {
              "url": "https://simonwillison.net/static/css/all.bf8cd891642c.css",
              "sri": "sha384-9qIZekWUyjCyDIf2YK1FRoKiPJq4PHt6tp/ulnuuyRBvazd0hG7pWbE99zvwSznI"
            }
          ],
          "extra_js_urls": [
            {
              "url": "https://code.jquery.com/jquery-3.2.1.slim.min.js",
              "sri": "sha256-k2WSCIexGzOj3Euiig+TlR8gA0EmPjuc79OEeY5L45g="
            }
          ]
        }
.. [[[end]]]

This will produce:

.. code-block:: html

    <link rel="stylesheet" href="https://simonwillison.net/static/css/all.bf8cd891642c.css"
        integrity="sha384-9qIZekWUyjCyDIf2YK1FRoKiPJq4PHt6tp/ulnuuyRBvazd0hG7pWbE99zvwSznI"
        crossorigin="anonymous">
    <script src="https://code.jquery.com/jquery-3.2.1.slim.min.js"
        integrity="sha256-k2WSCIexGzOj3Euiig+TlR8gA0EmPjuc79OEeY5L45g="
        crossorigin="anonymous"></script>

Modern browsers will only execute the stylesheet or JavaScript if the SRI hash
matches the content served. You can generate hashes using `www.srihash.org <https://www.srihash.org/>`_

Items in ``"extra_js_urls"`` can specify ``"module": true`` if they reference JavaScript that uses `JavaScript modules <https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide/Modules>`__. This configuration:

.. [[[cog
    config_example(cog, """
        extra_js_urls:
        - url: https://example.datasette.io/module.js
          module: true
    """)
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml


            extra_js_urls:
            - url: https://example.datasette.io/module.js
              module: true


.. tab:: datasette.json

    .. code-block:: json

        {
          "extra_js_urls": [
            {
              "url": "https://example.datasette.io/module.js",
              "module": true
            }
          ]
        }
.. [[[end]]]

Will produce this HTML:

.. code-block:: html

    <script type="module" src="https://example.datasette.io/module.js"></script>

.. _configuration_reference_table:

Table configuration
~~~~~~~~~~~~~~~~~~~

Datasette supports a number of table-level configuration options inside ``datasette.yaml``. These are placed under ``databases.database_name.tables.table_name``.

.. _table_configuration_sort:

``sort`` / ``sort_desc``
^^^^^^^^^^^^^^^^^^^^^^^^

By default Datasette tables are sorted by primary key. You can set a default sort order for a specific table using the ``sort`` or ``sort_desc`` properties:

.. [[[cog
    from metadata_doc import config_example
    import textwrap
    config_example(cog, textwrap.dedent(
      """
        databases:
          mydatabase:
            tables:
              example_table:
                sort: created
      """).strip()
    )
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        databases:
          mydatabase:
            tables:
              example_table:
                sort: created

.. tab:: datasette.json

    .. code-block:: json

        {
          "databases": {
            "mydatabase": {
              "tables": {
                "example_table": {
                  "sort": "created"
                }
              }
            }
          }
        }
.. [[[end]]]

Or use ``sort_desc`` to sort in descending order:

.. [[[cog
    config_example(cog, textwrap.dedent(
      """
        databases:
          mydatabase:
            tables:
              example_table:
                sort_desc: created
      """).strip()
    )
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        databases:
          mydatabase:
            tables:
              example_table:
                sort_desc: created

.. tab:: datasette.json

    .. code-block:: json

        {
          "databases": {
            "mydatabase": {
              "tables": {
                "example_table": {
                  "sort_desc": "created"
                }
              }
            }
          }
        }
.. [[[end]]]

.. _table_configuration_size:

``size``
^^^^^^^^

Datasette defaults to displaying 100 rows per page, for both tables and views. You can change this on a per-table or per-view basis using the ``size`` key:

.. [[[cog
    config_example(cog, textwrap.dedent(
      """
        databases:
          mydatabase:
            tables:
              example_table:
                size: 10
      """).strip()
    )
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        databases:
          mydatabase:
            tables:
              example_table:
                size: 10

.. tab:: datasette.json

    .. code-block:: json

        {
          "databases": {
            "mydatabase": {
              "tables": {
                "example_table": {
                  "size": 10
                }
              }
            }
          }
        }
.. [[[end]]]

This size can still be over-ridden by passing e.g. ``?_size=50`` in the query string.

.. _table_configuration_sortable_columns:

``sortable_columns``
^^^^^^^^^^^^^^^^^^^^

Datasette allows any column to be used for sorting by default. If you need to control which columns are available for sorting you can do so using ``sortable_columns``:

.. [[[cog
    config_example(cog, textwrap.dedent(
      """
        databases:
          mydatabase:
            tables:
              example_table:
                sortable_columns:
                - height
                - weight
      """).strip()
    )
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        databases:
          mydatabase:
            tables:
              example_table:
                sortable_columns:
                - height
                - weight

.. tab:: datasette.json

    .. code-block:: json

        {
          "databases": {
            "mydatabase": {
              "tables": {
                "example_table": {
                  "sortable_columns": [
                    "height",
                    "weight"
                  ]
                }
              }
            }
          }
        }
.. [[[end]]]

This will restrict sorting of ``example_table`` to just the ``height`` and ``weight`` columns.

You can also disable sorting entirely by setting ``"sortable_columns": []``

You can use ``sortable_columns`` to enable specific sort orders for a view called ``name_of_view`` in the database ``my_database`` like so:

.. [[[cog
    config_example(cog, textwrap.dedent(
      """
        databases:
          my_database:
            tables:
              name_of_view:
                sortable_columns:
                - clicks
                - impressions
      """).strip()
    )
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        databases:
          my_database:
            tables:
              name_of_view:
                sortable_columns:
                - clicks
                - impressions

.. tab:: datasette.json

    .. code-block:: json

        {
          "databases": {
            "my_database": {
              "tables": {
                "name_of_view": {
                  "sortable_columns": [
                    "clicks",
                    "impressions"
                  ]
                }
              }
            }
          }
        }
.. [[[end]]]

.. _table_configuration_label_column:

``label_column``
^^^^^^^^^^^^^^^^

Datasette's HTML interface attempts to display foreign key references as labelled hyperlinks. By default, it automatically detects a label column using the following rules (in order):

1. If there is exactly one unique text column, use that.
2. If there is a column called ``name`` or ``title`` (case-insensitive), use that.
3. If the table has only two columns - a primary key and one other - use the non-primary-key column.

You can override this automatic detection by specifying which column should be used for the link label with the ``label_column`` property:

.. [[[cog
    config_example(cog, textwrap.dedent(
      """
        databases:
          mydatabase:
            tables:
              example_table:
                label_column: title
      """).strip()
    )
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        databases:
          mydatabase:
            tables:
              example_table:
                label_column: title

.. tab:: datasette.json

    .. code-block:: json

        {
          "databases": {
            "mydatabase": {
              "tables": {
                "example_table": {
                  "label_column": "title"
                }
              }
            }
          }
        }
.. [[[end]]]

.. _table_configuration_hidden:

``hidden``
^^^^^^^^^^

You can hide tables from the database listing view (in the same way that FTS and SpatiaLite tables are automatically hidden) using ``"hidden": true``:

.. [[[cog
    config_example(cog, textwrap.dedent(
      """
        databases:
          mydatabase:
            tables:
              example_table:
                hidden: true
      """).strip()
    )
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        databases:
          mydatabase:
            tables:
              example_table:
                hidden: true

.. tab:: datasette.json

    .. code-block:: json

        {
          "databases": {
            "mydatabase": {
              "tables": {
                "example_table": {
                  "hidden": true
                }
              }
            }
          }
        }
.. [[[end]]]

.. _table_configuration_facets:

``facets`` / ``facet_size``
^^^^^^^^^^^^^^^^^^^^^^^^^^^

You can turn on facets by default for specific tables. ``facet_size`` controls how many unique values are shown for each facet on that table (the default is controlled by the :ref:`setting_default_facet_size` setting). See :ref:`facets_metadata` for full details.

.. [[[cog
    config_example(cog, textwrap.dedent(
      """
        databases:
          sf-trees:
            tables:
              Street_Tree_List:
                facets:
                - qLegalStatus
                facet_size: 10
      """).strip()
    )
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        databases:
          sf-trees:
            tables:
              Street_Tree_List:
                facets:
                - qLegalStatus
                facet_size: 10

.. tab:: datasette.json

    .. code-block:: json

        {
          "databases": {
            "sf-trees": {
              "tables": {
                "Street_Tree_List": {
                  "facets": [
                    "qLegalStatus"
                  ],
                  "facet_size": 10
                }
              }
            }
          }
        }
.. [[[end]]]

You can also specify :ref:`array <facet_by_json_array>` or :ref:`date <facet_by_date>` facets using JSON objects with a single key of ``array`` or ``date``:

.. code-block:: yaml

    facets:
    - array: tags
    - date: created

.. _table_configuration_fts:

``fts_table`` / ``fts_pk`` / ``searchmode``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

These configure :ref:`full-text search <full_text_search>` for a table or view. See :ref:`full_text_search_table_or_view` for full details.

``fts_table`` specifies which FTS table to use for search. ``fts_pk`` sets the primary key column if it is something other than ``rowid``. ``searchmode`` can be set to ``"raw"`` to enable `SQLite advanced search operators <https://www.sqlite.org/fts5.html#full_text_query_syntax>`__.

.. [[[cog
    config_example(cog, textwrap.dedent(
      """
        databases:
          russian-ads:
            tables:
              display_ads:
                fts_table: ads_fts
                fts_pk: id
                searchmode: raw
      """).strip()
    )
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        databases:
          russian-ads:
            tables:
              display_ads:
                fts_table: ads_fts
                fts_pk: id
                searchmode: raw

.. tab:: datasette.json

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
.. [[[end]]]

.. _table_configuration_columns:

``columns``
^^^^^^^^^^^

You can include descriptions for your columns by adding a ``columns`` mapping of column names to descriptions. These will be displayed at the top of the table page, and will also show in the cog menu for each column.

.. [[[cog
    config_example(cog, textwrap.dedent(
      """
        databases:
          mydatabase:
            tables:
              example_table:
                columns:
                  column1: Description of column 1
                  column2: Description of column 2
      """).strip()
    )
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        databases:
          mydatabase:
            tables:
              example_table:
                columns:
                  column1: Description of column 1
                  column2: Description of column 2

.. tab:: datasette.json

    .. code-block:: json

        {
          "databases": {
            "mydatabase": {
              "tables": {
                "example_table": {
                  "columns": {
                    "column1": "Description of column 1",
                    "column2": "Description of column 2"
                  }
                }
              }
            }
          }
        }
.. [[[end]]]

You can see an example of how these look at `latest.datasette.io/fixtures/roadside_attractions <https://latest.datasette.io/fixtures/roadside_attractions>`__.

.. _table_configuration_column_types:

``column_types``
^^^^^^^^^^^^^^^^

You can assign semantic column types to columns, which affect how values are rendered, validated, and transformed. Built-in column types include ``url``, ``email``, and ``json``. Plugins can register additional column types using the ``register_column_types`` plugin hook.

The simplest form maps column names to type name strings:

.. [[[cog
    config_example(cog, textwrap.dedent(
      """
        databases:
          mydatabase:
            tables:
              example_table:
                column_types:
                  website: url
                  contact: email
                  extra_data: json
      """).strip()
    )
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        databases:
          mydatabase:
            tables:
              example_table:
                column_types:
                  website: url
                  contact: email
                  extra_data: json

.. tab:: datasette.json

    .. code-block:: json

        {
          "databases": {
            "mydatabase": {
              "tables": {
                "example_table": {
                  "column_types": {
                    "website": "url",
                    "contact": "email",
                    "extra_data": "json"
                  }
                }
              }
            }
          }
        }
.. [[[end]]]

For column types that accept additional configuration, use an object with ``type`` and ``config`` keys:

.. [[[cog
    config_example(cog, textwrap.dedent(
      """
        databases:
          mydatabase:
            tables:
              example_table:
                column_types:
                  website:
                    type: url
                    config:
                      prefix: "https://"
      """).strip()
    )
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        databases:
          mydatabase:
            tables:
              example_table:
                column_types:
                  website:
                    type: url
                    config:
                      prefix: "https://"

.. tab:: datasette.json

    .. code-block:: json

        {
          "databases": {
            "mydatabase": {
              "tables": {
                "example_table": {
                  "column_types": {
                    "website": {
                      "type": "url",
                      "config": {
                        "prefix": "https://"
                      }
                    }
                  }
                }
              }
            }
          }
        }
.. [[[end]]]


