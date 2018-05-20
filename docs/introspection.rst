Introspection
=============

Datasette includes some pages and JSON API endpoints for introspecting the current index. These can be used to understand some of the internals of Datasette and to see how a particular instance has been configured.

Each of these pages can be viewed in your browser. Add ``.json`` to the URL to get back the contents as JSON.

/-/metadata
-----------

Shows the contents of the ``metadata.json`` file that was passed to ``datasette serve``, if any. `Metadata example <https://fivethirtyeight.datasettes.com/-/metadata>`_::

    {
        "license": "CC Attribution 4.0 License",
        "license_url": "http://creativecommons.org/licenses/by/4.0/",
        "source": "fivethirtyeight/data on GitHub",
        "source_url": "https://github.com/fivethirtyeight/data",
        "title": "Five Thirty Eight",
        "databases": {...}
    }

/-/inspect
----------

Shows the result of running ``datasette inspect`` on the currently loaded databases. This is run automatically when Datasette starts up, or can be run as a separate step and passed to ``datasette serve --inspect-file``.

This is an internal implementation detail of Datasette and the format should not be considered stable - it is likely to change in undocumented ways between different releases.

`Inspect example <https://fivethirtyeight.datasettes.com/-/inspect>`_::

    {
        "fivethirtyeight": {
            "file": "fivethirtyeight.db",
            "hash": "5de27e3eceb3f5ba817e0b2e066cea77832592b62d94690b5102a48f385b95fb",
            "tables": {
                "./index": {
                    "columns": [
                        "dataset_url",
                        "article_url",
                        "live"
                    ],
                    "count": 125,
                    "foreign_keys": {
                        "incoming": [],
                        "outgoing": []
                    },
                    "fts_table": null,
                    "hidden": false,
                    "label_column": null,
                    "name": "./index",
                    "primary_keys": []
                },
                ...


/-/versions
-----------

Shows the version of Datasette, Python and SQLite. `Versions example <https://fivethirtyeight.datasettes.com/-/versions>`_::

    {
        "datasette": {
            "version": "0.21"
        },
        "python": {
            "full": "3.6.5 (default, May  5 2018, 03:07:21) \n[GCC 6.3.0 20170516]",
            "version": "3.6.5"
        },
        "sqlite": {
            "extensions": {
                "json1": null
            },
            "fts_versions": [
                "FTS4",
                "FTS3"
            ],
            "version": "3.16.2"
        }
    }

/-/plugins
----------

Shows a list of currently installed plugins and their versions. Example::

    [
        {
            "name": "datasette_cluster_map",
            "static": true,
            "templates": false,
            "version": "0.4"
        },
        {
            "name": "datasette_plugin_demos",
            "static": false,
            "templates": true,
            "version": "0.3"
        }
    ]

/-/config
---------

Shows the :ref:`config` options for this instance of Datasette. `Config example <https://fivethirtyeight.datasettes.com/-/config>`_::

    {
        "default_facet_size": 30,
        "default_page_size": 100,
        "facet_suggest_time_limit_ms": 50,
        "facet_time_limit_ms": 1000,
        "max_returned_rows": 1000,
        "sql_time_limit_ms": 1000
    }
