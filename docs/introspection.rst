Introspection
=============

Datasette includes some pages and JSON API endpoints for introspecting the current instance. These can be used to understand some of the internals of Datasette and to see how a particular instance has been configured.

Each of these pages can be viewed in your browser. Add ``.json`` to the URL to get back the contents as JSON.

.. _JsonDataView_metadata:

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

.. _JsonDataView_versions:

/-/versions
-----------

Shows the version of Datasette, Python and SQLite. `Versions example <https://latest.datasette.io/-/versions>`_::

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
                "FTS5",
                "FTS4",
                "FTS3"
            ],
            "compile_options": [
                "COMPILER=gcc-6.3.0 20170516",
                "ENABLE_FTS3",
                "ENABLE_FTS4",
                "ENABLE_FTS5",
                "ENABLE_JSON1",
                "ENABLE_RTREE",
                "THREADSAFE=1"
            ],
            "version": "3.16.2"
        }
    }

.. _JsonDataView_plugins:

/-/plugins
----------

Shows a list of currently installed plugins and their versions. `Plugins example <https://san-francisco.datasettes.com/-/plugins>`_::

    [
        {
            "name": "datasette_cluster_map",
            "static": true,
            "templates": false,
            "version": "0.4"
        }
    ]

.. _JsonDataView_config:

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

.. _JsonDataView_databases:

/-/databases
------------

Shows currently attached databases. `Databases example <https://latest.datasette.io/-/config>`_::

    [
        {
            "hash": null,
            "is_memory": false,
            "is_mutable": true,
            "name": "fixtures",
            "path": "fixtures.db",
            "size": 225280
        }
    ]

.. _JsonDataView_threads:

/-/threads
----------

Shows details of threads. `Threads example <https://latest.datasette.io/-/threads>`_::

    {
        "num_threads": 2,
        "threads": [
            {
                "daemon": false,
                "ident": 4759197120,
                "name": "MainThread"
            },
            {
                "daemon": true,
                "ident": 123145319682048,
                "name": "Thread-1"
            },
        ]
    }
