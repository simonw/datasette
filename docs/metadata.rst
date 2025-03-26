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

.. _metadata_description:

Descriptions
------------

You can apply a **description** or **description_html** field to the index page, specific databases or specific tables. If both are present, the HTML description wins.

Unlike the metadata fields above, descriptions do not cascade from the index to databases or from databases to their columns.

.. _metadata_column_descriptions:

Column descriptions
-------------------

You can include descriptions for your columns by adding a ``"columns": {"name-of-column": "description-of-column"}`` block to your table metadata:

.. [[[cog
    metadata_example(cog, {
        "databases": {
            "database1": {
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
    })
.. ]]]

.. tab:: metadata.yaml

    .. code-block:: yaml

        databases:
          database1:
            tables:
              example_table:
                columns:
                  column1: Description of column 1
                  column2: Description of column 2


.. tab:: metadata.json

    .. code-block:: json

        {
          "databases": {
            "database1": {
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

These will be displayed at the top of the table page, and will also show in the cog menu for each column.

You can see an example of how these look at `latest.datasette.io/fixtures/roadside_attractions <https://latest.datasette.io/fixtures/roadside_attractions>`__.

.. _metadata_default_sort:

Setting a default sort order
----------------------------

By default Datasette tables are sorted by primary key. You can over-ride this default for a specific table using the ``"sort"`` or ``"sort_desc"`` metadata properties:

.. [[[cog
    metadata_example(cog, {
        "databases": {
            "mydatabase": {
                "tables": {
                    "example_table": {
                        "sort": "created"
                    }
                }
            }
        }
    })
.. ]]]

.. tab:: metadata.yaml

    .. code-block:: yaml

        databases:
          mydatabase:
            tables:
              example_table:
                sort: created


.. tab:: metadata.json

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

Or use ``"sort_desc"`` to sort in descending order:

.. [[[cog
    metadata_example(cog, {
        "databases": {
            "mydatabase": {
                "tables": {
                    "example_table": {
                        "sort_desc": "created"
                    }
                }
            }
        }
    })
.. ]]]

.. tab:: metadata.yaml

    .. code-block:: yaml

        databases:
          mydatabase:
            tables:
              example_table:
                sort_desc: created


.. tab:: metadata.json

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

.. _metadata_page_size:

Setting a custom page size
--------------------------

Datasette defaults to displaying 100 rows per page, for both tables and views. You can change this default page size on a per-table or per-view basis using the ``"size"`` key in ``metadata.json``:

.. [[[cog
    metadata_example(cog, {
        "databases": {
            "mydatabase": {
                "tables": {
                    "example_table": {
                        "size": 10
                    }
                }
            }
        }
    })
.. ]]]

.. tab:: metadata.yaml

    .. code-block:: yaml

        databases:
          mydatabase:
            tables:
              example_table:
                size: 10


.. tab:: metadata.json

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

.. _metadata_sortable_columns:

Setting which columns can be used for sorting
---------------------------------------------

Datasette allows any column to be used for sorting by default. If you need to
control which columns are available for sorting you can do so using the optional
``sortable_columns`` key:

.. [[[cog
    metadata_example(cog, {
        "databases": {
            "database1": {
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
    })
.. ]]]

.. tab:: metadata.yaml

    .. code-block:: yaml

        databases:
          database1:
            tables:
              example_table:
                sortable_columns:
                - height
                - weight


.. tab:: metadata.json

    .. code-block:: json

        {
          "databases": {
            "database1": {
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

This will restrict sorting of ``example_table`` to just the ``height`` and
``weight`` columns.

You can also disable sorting entirely by setting ``"sortable_columns": []``

You can use ``sortable_columns`` to enable specific sort orders for a view called ``name_of_view`` in the database ``my_database`` like so:

.. [[[cog
    metadata_example(cog, {
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
    })
.. ]]]

.. tab:: metadata.yaml

    .. code-block:: yaml

        databases:
          my_database:
            tables:
              name_of_view:
                sortable_columns:
                - clicks
                - impressions


.. tab:: metadata.json

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

.. _label_columns:

Specifying the label column for a table
---------------------------------------

Datasette's HTML interface attempts to display foreign key references as
labelled hyperlinks. By default, it looks for referenced tables that only have
two columns: a primary key column and one other. It assumes that the second
column should be used as the link label.

If your table has more than two columns you can specify which column should be
used for the link label with the ``label_column`` property:

.. [[[cog
    metadata_example(cog, {
        "databases": {
            "database1": {
                "tables": {
                    "example_table": {
                        "label_column": "title"
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
            tables:
              example_table:
                label_column: title


.. tab:: metadata.json

    .. code-block:: json

        {
          "databases": {
            "database1": {
              "tables": {
                "example_table": {
                  "label_column": "title"
                }
              }
            }
          }
        }
.. [[[end]]]

.. _metadata_hiding_tables:

Hiding tables
-------------

You can hide tables from the database listing view (in the same way that FTS and
SpatiaLite tables are automatically hidden) using ``"hidden": true``:

.. [[[cog
    metadata_example(cog, {
        "databases": {
            "database1": {
                "tables": {
                    "example_table": {
                        "hidden": True
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
            tables:
              example_table:
                hidden: true


.. tab:: metadata.json

    .. code-block:: json

        {
          "databases": {
            "database1": {
              "tables": {
                "example_table": {
                  "hidden": true
                }
              }
            }
          }
        }
.. [[[end]]]

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

The following are the full list of allowed table-level metadata fields:

- ``source``
- ``source_url``
- ``license``
- ``license_url``
- ``about``
- ``about_url``
- ``hidden``
- ``sort/sort_desc``
- ``size``
- ``sortable_columns``
- ``label_column``
- ``facets``
- ``fts_table``
- ``fts_pk``
- ``searchmode``
- ``columns``
