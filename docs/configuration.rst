.. _configuration:

Configuration
=============

Datasette offers several ways to configure your Datasette instances: server settings, plugin configuration, authentication, and more.

To facilitate this, You can provide a ``datasette.yaml`` configuration file to datasette with the ``--config``/ ``-c`` flag:

.. code-block:: bash

    datasette mydatabase.db --config datasette.yaml

.. _configuration_reference:

``datasette.yaml`` reference
----------------------------

Here's a full example of all the valid configuration options that can exist inside ``datasette.yaml``.

.. tab:: YAML

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
                # plugin configuration for the your_table_name table
                # inside your_db_name database
                plugins:
                  datasette-my-plugin:
                    key: valueB

.. _configuration_reference_settings:
Settings configuration
~~~~~~~~~~~~~~~~~~~~~~

:ref:`settings` can be configured in ``datasette.yaml`` with the ``settings`` key.

.. tab:: YAML

    .. code-block:: yaml

        # inside datasette.yaml
        settings:
           default_allow_sql: off
           default_page_size: 50


.. _configuration_reference_plugins:
Plugin configuration
~~~~~~~~~~~~~~~~~~~~

Configuration for plugins can be defined inside ``datasette.yaml``. For top-level plugin configuration, use the ``plugins`` key.

.. tab:: YAML

    .. code-block:: yaml

        # inside datasette.yaml
        plugins:
          datasette-my-plugin:
            key: my_value

For database level or table level plugin configuration, nest it under the appropriate place under ``databases``.

.. tab:: YAML

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
