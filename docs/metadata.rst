.. _metadata:

Metadata
========

Data loves metadata. Any time you run Datasette you can optionally include a
JSON file with metadata about your databases and tables. Datasette will then
display that information in the web UI.

Run Datasette like this::

    datasette database1.db database2.db --metadata metadata.json

Your ``metadata.json`` file can look something like this:

.. code-block:: json

    {
        "title": "Custom title for your index page",
        "description": "Some description text can go here",
        "license": "ODbL",
        "license_url": "https://opendatacommons.org/licenses/odbl/",
        "source": "Original Data Source",
        "source_url": "http://example.com/"
    }

You can optionally use YAML instead of JSON, see :ref:`metadata_yaml`.

The above metadata will be displayed on the index page of your Datasette-powered
site. The source and license information will also be included in the footer of
every page served by Datasette.

Any special HTML characters in ``description`` will be escaped. If you want to
include HTML in your description, you can use a ``description_html`` property
instead.

Per-database and per-table metadata
-----------------------------------

Metadata at the top level of the JSON will be shown on the index page and in the
footer on every page of the site. The license and source is expected to apply to
all of your data.

You can also provide metadata at the per-database or per-table level, like this:

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

Each of the top-level metadata fields can be used at the database and table level.

.. _metadata_source_license_about:

Source, license and about
-------------------------

The three visible metadata fields you can apply to everything, specific databases or specific tables are source, license and about. All three are optional.

**source** and **source_url** should be used to indicate where the underlying data came from.

**license** and **license_url** should be used to indicate the license under which the data can be used.

**about** and **about_url** can be used to link to further information about the project - an accompanying blog entry for example.

For each of these you can provide just the ``*_url`` field and Datasette will treat that as the default link label text and display the URL directly on the page.

.. _metadata_column_descriptions:

Column descriptions
-------------------

You can include descriptions for your columns by adding a ``"columns": {"name-of-column": "description-of-column"}`` block to your table metadata:

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

These will be displayed at the top of the table page, and will also show in the cog menu for each column.

You can see an example of how these look at `latest.datasette.io/fixtures/roadside_attractions <https://latest.datasette.io/fixtures/roadside_attractions>`__.

Specifying units for a column
-----------------------------

Datasette supports attaching units to a column, which will be used when displaying
values from that column. SI prefixes will be used where appropriate.

Column units are configured in the metadata like so:

.. code-block:: json

    {
        "databases": {
            "database1": {
                "tables": {
                    "example_table": {
                        "units": {
                            "column1": "metres",
                            "column2": "Hz"
                        }
                    }
                }
            }
        }
    }

Units are interpreted using Pint_, and you can see the full list of available units in
Pint's `unit registry`_. You can also add `custom units`_ to the metadata, which will be
registered with Pint:

.. code-block:: json

    {
        "custom_units": [
            "decibel = [] = dB"
        ]
    }

.. _Pint: https://pint.readthedocs.io/
.. _unit registry: https://github.com/hgrecco/pint/blob/master/pint/default_en.txt
.. _custom units: http://pint.readthedocs.io/en/latest/defining.html

.. _metadata_default_sort:

Setting a default sort order
----------------------------

By default Datasette tables are sorted by primary key. You can over-ride this default for a specific table using the ``"sort"`` or ``"sort_desc"`` metadata properties:

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

Or use ``"sort_desc"`` to sort in descending order:

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

.. _metadata_page_size:

Setting a custom page size
--------------------------

Datasette defaults to displaying 100 rows per page, for both tables and views. You can change this default page size on a per-table or per-view basis using the ``"size"`` key in ``metadata.json``:

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

This size can still be over-ridden by passing e.g. ``?_size=50`` in the query string.

.. _metadata_sortable_columns:

Setting which columns can be used for sorting
---------------------------------------------

Datasette allows any column to be used for sorting by default. If you need to
control which columns are available for sorting you can do so using the optional
``sortable_columns`` key:

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

This will restrict sorting of ``example_table`` to just the ``height`` and
``weight`` columns.

You can also disable sorting entirely by setting ``"sortable_columns": []``

You can use ``sortable_columns`` to enable specific sort orders for a view called ``name_of_view`` in the database ``my_database`` like so:

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

.. _label_columns:

Specifying the label column for a table
---------------------------------------

Datasette's HTML interface attempts to display foreign key references as
labelled hyperlinks. By default, it looks for referenced tables that only have
two columns: a primary key column and one other. It assumes that the second
column should be used as the link label.

If your table has more than two columns you can specify which column should be
used for the link label with the ``label_column`` property:

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

.. _metadata_hiding_tables:

Hiding tables
-------------

You can hide tables from the database listing view (in the same way that FTS and
SpatiaLite tables are automatically hidden) using ``"hidden": true``:

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

.. _metadata_yaml:

Using YAML for metadata
-----------------------

Datasette accepts YAML as an alternative to JSON for your metadata configuration file. YAML is particularly useful for including multiline HTML and SQL strings.

Here's an example of a ``metadata.yml`` file, re-using an example from :ref:`canned_queries`.

.. code-block:: yaml

    title: Demonstrating Metadata from YAML
    description_html: |-
      <p>This description includes a long HTML string</p>
      <ul>
        <li>YAML is better for embedding HTML strings than JSON!</li>
      </ul>
    license: ODbL
    license_url: https://opendatacommons.org/licenses/odbl/
    databases:
      fixtures:
        tables:
          no_primary_key:
            hidden: true
        queries:
          neighborhood_search:
            sql: |-
              select neighborhood, facet_cities.name, state
              from facetable join facet_cities on facetable.city_id = facet_cities.id
              where neighborhood like '%' || :text || '%' order by neighborhood;
            title: Search neighborhoods
            description_html: |-
              <p>This demonstrates <em>basic</em> LIKE search

The ``metadata.yml`` file is passed to Datasette using the same ``--metadata`` option::

    datasette fixtures.db --metadata metadata.yml
