.. _facets:

Facets
======

Datasette facets can be used to add a faceted browse interface to any database table.
With facets, tables are displayed along with a summary showing the most common values in specified columns.
These values can be selected to further filter the table.

Here's `an example <https://congress-legislators.datasettes.com/legislators/legislator_terms?_facet=type&_facet=party&_facet=state&_facet_size=10>`__:

.. image:: https://raw.githubusercontent.com/simonw/datasette-screenshots/0.62/non-retina/faceting-details.png
   :alt: Screenshot showing facets against a table of congressional legislators. Suggested facets include state_rank and start and end dates, and the displayed facets are state, party and type. Each facet lists values along with a count of rows for each value.

Facets can be specified in two ways: using query string parameters, or in ``metadata.json`` configuration for the table.

Facets in query strings
-----------------------

To turn on faceting for specific columns on a Datasette table view, add one or more ``_facet=COLUMN`` parameters to the URL.
For example, if you want to turn on facets for the ``city_id`` and ``state`` columns, construct a URL that looks like this::

    /dbname/tablename?_facet=state&_facet=city_id

This works for both the HTML interface and the ``.json`` view.
When enabled, facets will cause a ``facet_results`` block to be added to the JSON output, looking something like this:

.. code-block:: json

    {
      "state": {
        "name": "state",
        "results": [
          {
            "value": "CA",
            "label": "CA",
            "count": 10,
            "toggle_url": "http://...?_facet=city_id&_facet=state&state=CA",
            "selected": false
          },
          {
            "value": "MI",
            "label": "MI",
            "count": 4,
            "toggle_url": "http://...?_facet=city_id&_facet=state&state=MI",
            "selected": false
          },
          {
            "value": "MC",
            "label": "MC",
            "count": 1,
            "toggle_url": "http://...?_facet=city_id&_facet=state&state=MC",
            "selected": false
          }
        ],
        "truncated": false
      }
      "city_id": {
        "name": "city_id",
        "results": [
          {
            "value": 1,
            "label": "San Francisco",
            "count": 6,
            "toggle_url": "http://...?_facet=city_id&_facet=state&city_id=1",
            "selected": false
          },
          {
            "value": 2,
            "label": "Los Angeles",
            "count": 4,
            "toggle_url": "http://...?_facet=city_id&_facet=state&city_id=2",
            "selected": false
          },
          {
            "value": 3,
            "label": "Detroit",
            "count": 4,
            "toggle_url": "http://...?_facet=city_id&_facet=state&city_id=3",
            "selected": false
          },
          {
            "value": 4,
            "label": "Memnonia",
            "count": 1,
            "toggle_url": "http://...?_facet=city_id&_facet=state&city_id=4",
            "selected": false
          }
        ],
        "truncated": false
      }
    }

If Datasette detects that a column is a foreign key, the ``"label"`` property will be automatically derived from the detected label column on the referenced table.

The default number of facet results returned is 30, controlled by the :ref:`setting_default_facet_size` setting.
You can increase this on an individual page by adding ``?_facet_size=100`` to the query string, up to a maximum of :ref:`setting_max_returned_rows` (which defaults to 1000).

.. _facets_metadata:

Facets in metadata.json
-----------------------

You can turn facets on by default for specific tables by adding them to a ``"facets"`` key in a Datasette :ref:`metadata` file.

Here's an example that turns on faceting by default for the ``qLegalStatus`` column in the ``Street_Tree_List`` table in the ``sf-trees`` database:

.. code-block:: json

    {
      "databases": {
        "sf-trees": {
          "tables": {
            "Street_Tree_List": {
              "facets": ["qLegalStatus"]
            }
          }
        }
      }
    }

Facets defined in this way will always be shown in the interface and returned in the API, regardless of the ``_facet`` arguments passed to the view.

You can specify :ref:`array <facet_by_json_array>` or :ref:`date <facet_by_date>` facets in metadata using JSON objects with a single key of ``array`` or ``date`` and a value specifying the column, like this:

.. code-block:: json

  {
    "facets": [
      {"array": "tags"},
      {"date": "created"}
    ]
  }

You can change the default facet size (the number of results shown for each facet) for a table using ``facet_size``:

.. code-block:: json

    {
      "databases": {
        "sf-trees": {
          "tables": {
            "Street_Tree_List": {
              "facets": ["qLegalStatus"],
              "facet_size": 10
            }
          }
        }
      }
    }

Suggested facets
----------------

Datasette's table UI will suggest facets for the user to apply, based on the following criteria:

For the currently filtered data are there any columns which, if applied as a facet...

* Will return 30 or less unique options
* Will return more than one unique option
* Will return less unique options than the total number of filtered rows
* And the query used to evaluate this criteria can be completed in under 50ms

That last point is particularly important: Datasette runs a query for every column that is displayed on a page, which could get expensive - so to avoid slow load times it sets a time limit of just 50ms for each of those queries.
This means suggested facets are unlikely to appear for tables with millions of records in them.

Speeding up facets with indexes
-------------------------------

The performance of facets can be greatly improved by adding indexes on the columns you wish to facet by.
Adding indexes can be performed using the ``sqlite3`` command-line utility. Here's how to add an index on the ``state`` column in a table called ``Food_Trucks``::

    $ sqlite3 mydatabase.db
    SQLite version 3.19.3 2017-06-27 16:48:08
    Enter ".help" for usage hints.
    sqlite> CREATE INDEX Food_Trucks_state ON Food_Trucks("state");

Or using the `sqlite-utils <https://sqlite-utils.datasette.io/en/stable/cli.html#creating-indexes>`__ command-line utility::

    $ sqlite-utils create-index mydatabase.db Food_Trucks state

.. _facet_by_json_array:

Facet by JSON array
-------------------

If your SQLite installation provides the ``json1`` extension (you can check using :ref:`JsonDataView_versions`) Datasette will automatically detect columns that contain JSON arrays of values and offer a faceting interface against those columns.

This is useful for modelling things like tags without needing to break them out into a new table.

Example here: `latest.datasette.io/fixtures/facetable?_facet_array=tags <https://latest.datasette.io/fixtures/facetable?_facet_array=tags>`__

.. _facet_by_date:

Facet by date
-------------

If Datasette finds any columns that contain dates in the first 100 values, it will offer a faceting interface against the dates of those values.
This works especially well against timestamp values such as ``2019-03-01 12:44:00``.

Example here: `latest.datasette.io/fixtures/facetable?_facet_date=created <https://latest.datasette.io/fixtures/facetable?_facet_date=created>`__
