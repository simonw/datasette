.. _introspection:

Introspection
=============

Datasette includes some pages and JSON API endpoints for introspecting the current instance. These can be used to understand some of the internals of Datasette and to see how a particular instance has been configured.

Each of these pages can be viewed in your browser. Add ``.json`` to the URL to get back the contents as JSON.

JSON responses that return an object include an ``"ok": true`` key, consistent with the rest of the :ref:`JSON API <json_api>`.

.. _JsonDataView_metadata:

/-/metadata
-----------

Shows the contents of the ``metadata.json`` file that was passed to ``datasette serve``, if any. `Metadata example <https://fivethirtyeight.datasettes.com/-/metadata>`_:

.. code-block:: json

    {
        "license": "CC Attribution 4.0 License",
        "license_url": "http://creativecommons.org/licenses/by/4.0/",
        "source": "fivethirtyeight/data on GitHub",
        "source_url": "https://github.com/fivethirtyeight/data",
        "title": "Five Thirty Eight",
        "databases": {

        }
    }

.. _JsonDataView_versions:

/-/versions
-----------

Shows the version of Datasette, Python and SQLite. `Versions example <https://latest.datasette.io/-/versions>`_:

.. code-block:: json

    {
        "ok": true,
        "datasette": {
            "version": "0.60"
        },
        "python": {
            "full": "3.8.12 (default, Dec 21 2021, 10:45:09) \n[GCC 10.2.1 20210110]",
            "version": "3.8.12"
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
            "version": "3.37.0"
        }
    }

.. _JsonDataView_plugins:

/-/plugins
----------

Shows a list of currently installed plugins and their versions. `Plugins example <https://san-francisco.datasettes.com/-/plugins>`_:

.. code-block:: json

    {
        "ok": true,
        "plugins": [
            {
                "name": "datasette_cluster_map",
                "static": true,
                "templates": false,
                "version": "0.10",
                "hooks": ["extra_css_urls", "extra_js_urls", "extra_body_script"]
            }
        ]
    }

Add ``?all=1`` to include details of the default plugins baked into Datasette.

.. _JsonDataView_settings:

/-/settings
-----------

Shows the :ref:`settings` for this instance of Datasette. `Settings example <https://fivethirtyeight.datasettes.com/-/settings>`_:

.. code-block:: json

    {
        "ok": true,
        "default_facet_size": 30,
        "default_page_size": 100,
        "facet_suggest_time_limit_ms": 50,
        "facet_time_limit_ms": 1000,
        "max_returned_rows": 1000,
        "sql_time_limit_ms": 1000
    }

.. _JsonDataView_config:

/-/config
---------

Shows the :ref:`configuration <configuration>` for this instance of Datasette. This is generally the contents of the :ref:`datasette.yaml or datasette.json <configuration_reference>` file, which can include plugin configuration as well. `Config example <https://latest.datasette.io/-/config>`_:

.. code-block:: json

    {
        "ok": true,
        "settings": {
            "template_debug": true,
            "trace_debug": true,
            "force_https_urls": true
        }
    }

Any keys that include the one of the following substrings in their names will be returned as redacted ``***`` output, to help avoid accidentally leaking private configuration information: ``secret``, ``key``, ``password``, ``token``, ``hash``, ``dsn``.

.. _JsonDataView_databases:

/-/databases
------------

Shows currently attached databases that the current actor is allowed to view, based on the ``view-database`` permission. `Databases example <https://latest.datasette.io/-/databases>`_:

.. code-block:: json

    {
        "ok": true,
        "databases": [
            {
                "hash": null,
                "is_memory": false,
                "is_mutable": true,
                "name": "fixtures",
                "path": "fixtures.db",
                "size": 225280
            }
        ]
    }

.. _JsonDataView_actions:

/-/actions
----------

Shows all actions registered with the permission system, including those added by plugins. Requires the ``permissions-debug`` permission.

.. code-block:: json

    {
        "ok": true,
        "actions": [
            {
                "name": "view-instance",
                "abbr": "vi",
                "description": "View Datasette instance",
                "takes_parent": false,
                "takes_child": false,
                "resource_class": null,
                "also_requires": null
            }
        ]
    }

.. _JumpView:

/-/jump
-------

Returns a JSON list of items that the current actor has permission to view for Datasette's jump menu. By default this includes visible databases, tables, views and stored queries, and plugins can contribute additional items.

Each item includes a ``type`` string used as a category label in the menu. Items can also include an optional ``description`` with longer text describing that individual result.

The endpoint supports a ``?q=`` query parameter for filtering items by name.

`Jump example <https://latest.datasette.io/-/jump>`_:

.. code-block:: json

    {
        "ok": true,
        "matches": [
            {
                "name": "fixtures",
                "url": "/fixtures",
                "type": "database",
                "description": null
            },
            {
                "name": "fixtures: facetable",
                "url": "/fixtures/facetable",
                "type": "table",
                "description": null
            },
            {
                "name": "fixtures: recent_releases",
                "url": "/fixtures/recent_releases",
                "type": "query",
                "description": null
            }
        ],
        "truncated": false
    }

Search example with ``?q=facet`` returns only items matching ``.*facet.*``:

.. code-block:: json

    {
        "ok": true,
        "matches": [
            {
                "name": "fixtures: facetable",
                "url": "/fixtures/facetable",
                "type": "table",
                "description": null
            }
        ],
        "truncated": false
    }

When multiple search terms are provided (e.g., ``?q=user+profile``), items must match the pattern ``.*user.*profile.*``. Results are ordered by relevance, then by item type and shortest display name.

.. _AutocompleteDebugView:

/-/debug/autocomplete
---------------------

The debug tool at ``/-/debug/autocomplete`` can be used to try out the autocomplete component against a specific table. Pass ``?database=db&table=table`` to display an autocomplete field backed by that table's ``/-/autocomplete`` endpoint.

Without those query string arguments, the page lists up to five tables with detected label columns, scanning at most 100 tables.

.. _JsonDataView_threads:

/-/threads
----------

Shows details of threads and ``asyncio`` tasks. `Threads example <https://latest.datasette.io/-/threads>`_:

.. code-block:: json

    {
        "ok": true,
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
        ],
        "num_tasks": 3,
        "tasks": [
            "<Task pending coro=<RequestResponseCycle.run_asgi() running at uvicorn/protocols/http/httptools_impl.py:385> cb=[set.discard()]>",
            "<Task pending coro=<Server.serve() running at uvicorn/main.py:361> wait_for=<Future pending cb=[<TaskWakeupMethWrapper object at 0x10365c3d0>()]> cb=[run_until_complete.<locals>.<lambda>()]>",
            "<Task pending coro=<LifespanOn.main() running at uvicorn/lifespan/on.py:48> wait_for=<Future pending cb=[<TaskWakeupMethWrapper object at 0x10364f050>()]>>"
        ]
    }

.. _JsonDataView_actor:

/-/actor
--------

Shows the currently authenticated actor. Useful for debugging Datasette authentication plugins.

.. code-block:: json

    {
        "ok": true,
        "actor": {
            "id": 1,
            "username": "some-user"
        }
    }


.. _MessagesDebugView:

/-/messages
-----------

The debug tool at ``/-/messages`` can be used to set flash messages to try out that feature. See :ref:`datasette_add_message` for details of this feature.
