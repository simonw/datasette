.. _facets:

Facets
======

This feature is currently under development, see `#255 <https://github.com/simonw/datasette/issues/255>`_

Datasette facets can be used to add a faceted browse interface to any Datasette table. With facets, tables are displayed along with a summary showing the most common values in specified columns. These values can be selected to further filter the table.

Facets can be specified in two ways: using queryset parameters, or in ``metadata.json`` configuration for the table.

Facets in querystrings
----------------------

To turn on faceting for specific columns on a Datasette table view, add one or more ``_facet=COLUMN`` parameters to the URL. For example, if you want to turn on facets for the ``city`` and ``state`` columns, construct a URL that looks like this::

    /dbname/tablename?_facet=state&_facet=city

This works for both the HTML interface and the ``.json`` view. When enabled, facets will cause a ``facet_results`` block to be added to the JSON output, looking something like this::

    "facet_results": {
      "state": [
        {
          "value": "CA",
          "count": 10,
          "toggle_url": "http://...&state=CA"
        },
        {
          "value": "MI",
          "count": 4,
          "toggle_url": "http://...&state=MI"
        }
      ],
      "city": [
        {
          "value": "San Francisco",
          "count": 6,
          "toggle_url": "http://...=San+Francisco"
        },
        {
          "value": "Detroit",
          "count": 4,
          "toggle_url": "http://...&city=Detroit"
        },
        {
          "value": "Los Angeles",
          "count": 4,
          "toggle_url": "http://...=Los+Angeles"
        }
      ]
    }

Facets in metadata.json
-----------------------

You can turn facets on by default for specific tables by adding them to a ``"facets"`` key in a Datasette :ref:`metadata` file.

Here's an example that turns on faceting by default for the ``qLegalStatus`` column in the ``Street_Tree_List`` table in the ``sf-trees`` database::

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
