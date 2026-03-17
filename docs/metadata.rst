.. _metadata:

Metadata
========

Data loves metadata. Any time you run Datasette you can optionally include a
YAML or JSON file with metadata about your databases and tables. Datasette will then
display that information in the web UI.

Run Datasette like this::

    datasette database1.db database2.db --metadata metadata.yaml

Your ``metadata.yaml`` file can look something like this:


.. [[[cog
    from metadata_doc import metadata_example
    metadata_example(cog, {
        "title": "Custom title for your index page",
        "description": "Some description text can go here",
        "license": "ODbL",
        "license_url": "https://opendatacommons.org/licenses/odbl/",
        "source": "Original Data Source",
        "source_url": "http://example.com/"
    })
.. ]]]

.. tab:: metadata.yaml

    .. code-block:: yaml

        title: Custom title for your index page
        description: Some description text can go here
        license: ODbL
        license_url: https://opendatacommons.org/licenses/odbl/
        source: Original Data Source
        source_url: http://example.com/


.. tab:: metadata.json

    .. code-block:: json

        {
          "title": "Custom title for your index page",
          "description": "Some description text can go here",
          "license": "ODbL",
          "license_url": "https://opendatacommons.org/licenses/odbl/",
          "source": "Original Data Source",
          "source_url": "http://example.com/"
        }
.. [[[end]]]


Choosing YAML over JSON adds support for multi-line strings and comments.

The above metadata will be displayed on the index page of your Datasette-powered
site. The source and license information will also be included in the footer of
every page served by Datasette.

Any special HTML characters in ``description`` will be escaped. If you want to
include HTML in your description, you can use a ``description_html`` property
instead.

Per-database and per-table metadata
-----------------------------------

Metadata at the top level of the file will be shown on the index page and in the
footer on every page of the site. The license and source is expected to apply to
all of your data.

You can also provide metadata at the per-database or per-table level, like this:

.. [[[cog
    metadata_example(cog, {
        "databases": {
            "database1": {
                "source": "Alternative source",
                "source_url": "http://example.com/",
                "tables": {
                    "example_table": {
                        "description_html": "Custom <em>table</em> description",
                        "license": "CC BY 3.0 US",
                        "license_url": "https://creativecommons.org/licenses/by/3.0/us/"
                    }
                }
            }
        }
    })
.. ]]]

.. tab:: metadata.yaml

    .. code-block:: yaml

        databases:
          database1:
            source: Alternative source
            source_url: http://example.com/
            tables:
              example_table:
                description_html: Custom <em>table</em> description
                license: CC BY 3.0 US
                license_url: https://creativecommons.org/licenses/by/3.0/us/


.. tab:: metadata.json

    .. code-block:: json

        {
          "databases": {
            "database1": {
              "source": "Alternative source",
              "source_url": "http://example.com/",
              "tables": {
                "example_table": {
                  "description_html": "Custom <em>table</em> description",
                  "license": "CC BY 3.0 US",
                  "license_url": "https://creativecommons.org/licenses/by/3.0/us/"
                }
              }
            }
          }
        }
.. [[[end]]]


Each of the top-level metadata fields can be used at the database and table level.

.. _metadata_source_license_about:

Source, license and about
-------------------------

The three visible metadata fields you can apply to everything, specific databases or specific tables are source, license and about. All three are optional.

**source** and **source_url** should be used to indicate where the underlying data came from.

**license** and **license_url** should be used to indicate the license under which the data can be used.

**about** and **about_url** can be used to link to further information about the project - an accompanying blog entry for example.

For each of these you can provide just the ``*_url`` field and Datasette will treat that as the default link label text and display the URL directly on the page.

.. _metadata_table_config:

Table configuration
-------------------

Datasette supports a range of table-level configuration options including sort order, page size, facets, full-text search, column types, and more. These are now documented in the :ref:`table configuration <configuration_reference_table>` section of the configuration reference.

For backwards compatibility these options can be specified in either ``metadata.yaml`` or ``datasette.yaml``.

.. _metadata_reference:

Metadata reference
------------------


A full reference of every supported option in a ``metadata.json`` or ``metadata.yaml`` file.


Top-level metadata
~~~~~~~~~~~~~~~~~~

"Top-level" metadata refers to fields that can be specified at the root level  of a metadata file. These attributes are meant to describe the entire Datasette instance.

The following are the full list of allowed top-level metadata fields:

- ``title``
- ``description``
- ``description_html``
- ``license``
- ``license_url``
- ``source``
- ``source_url``

Database-level metadata
~~~~~~~~~~~~~~~~~~~~~~~

"Database-level" metadata refers to fields that can be specified for each database in a Datasette instance. These attributes should be listed under a database inside the `"databases"` field.

The following are the full list of allowed database-level metadata fields:

- ``source``
- ``source_url``
- ``license``
- ``license_url``
- ``about``
- ``about_url``

Table-level metadata
~~~~~~~~~~~~~~~~~~~~

"Table-level" metadata refers to fields that can be specified for each table in a Datasette instance. These attributes should be listed under a specific table using the `"tables"` field.

The following metadata fields are supported at the table level:

- ``source``
- ``source_url``
- ``license``
- ``license_url``
- ``about``
- ``about_url``

Additionally, tables support a number of configuration options (``sort``, ``sort_desc``, ``size``, ``sortable_columns``, ``label_column``, ``hidden``, ``facets``, ``facet_size``, ``fts_table``, ``fts_pk``, ``searchmode``, ``columns``, ``column_types``). See :ref:`table configuration <configuration_reference_table>` for full details.
