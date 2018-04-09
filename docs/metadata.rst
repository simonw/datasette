Metadata
========

Data loves metadata. Any time you run Datasette you can optionally include a
JSON file with metadata about your databases and tables. Datasette will then
display that information in the web UI.

Run Datasette like this::

    datasette database1.db database2.db --metadata metadata.json

Your ``metadata.json`` file can look something like this::

    {
        "title": "Custom title for your index page",
        "description": "Some description text can go here",
        "license": "ODbL",
        "license_url": "https://opendatacommons.org/licenses/odbl/",
        "source": "Original Data Source",
        "source_url": "http://example.com/"
    }

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

You can also provide metadata at the per-database or per-table level, like this::

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

Setting which columns can be used for sorting
---------------------------------------------

Datasette allows any column to be used for sorting by default. If you need to
control which columns are available for sorting you can do so using the optional
``sortable_columns`` key::

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

Generating a metadata skeleton
------------------------------

Tracking down the names of all of your databases and tables and formatting them
as JSON can be a little tedious, so Datasette provides a tool to help you
generate a "skeleton" JSON file::

    datasette skeleton database1.db database2.db

This will create a ``metadata.json`` file looking something like this::

    {
        "title": null,
        "description": null,
        "description_html": null,
        "license": null,
        "license_url": null,
        "source": null,
        "source_url": null,
        "databases": {
            "database1": {
                "title": null,
                "description": null,
                "description_html": null,
                "license": null,
                "license_url": null,
                "source": null,
                "source_url": null,
                "queries": {},
                "tables": {
                    "example_table": {
                        "title": null,
                        "description": null,
                        "description_html": null,
                        "license": null,
                        "license_url": null,
                        "source": null,
                        "source_url": null
                    }
                }
            },
            "database2": ...
        }
    }

You can replace any of the ``null`` values with a JSON string to populate that
piece of metadata.
