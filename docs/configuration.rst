.. _configuration:

Configuration
=============

Datasette offers several ways to configure your Datasette instances: server settings, plugin configuration, authentication, and more.

Most configuration can be handled using a ``datasette.yaml`` configuration file, passed to datasette using the ``--config``/ ``-c`` flag:

.. code-block:: bash

    datasette mydatabase.db --config datasette.yaml

This file can also use JSON, as ``datasette.json``. YAML is recommended over JSON due to its support for comments and multi-line strings.

.. _configuration_reference:

``datasette.yaml`` reference
----------------------------

This example shows many of the valid configuration options that can exist inside ``datasette.yaml``.

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



