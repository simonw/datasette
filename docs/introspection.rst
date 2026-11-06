.. _introspection:

Introspection
=============

Datasette includes some pages and JSON API endpoints for introspecting the current instance. These can be used to understand some of the internals of Datasette and to see how a particular instance has been configured.

Each of these pages can be viewed in your browser. Add ``.json`` to the URL to get back the contents as JSON.

JSON responses that return an object include an ``"ok": true`` key, consistent with the rest of the :ref:`JSON API <json_api>`.

The introspection endpoints documented on this page are covered by the :ref:`JSON API stability promise <json_api_stability>`, with the exception of the debug endpoints ``/-/threads``, ``/-/tasks`` and ``/-/actions`` and the new ``/-/imports`` endpoint, whose shapes may change in future releases.

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

Shows a list of currently installed plugins and their versions. `Plugins example <https://datasette.io/-/plugins>`_:

.. code-block:: json

    [
        {
            "name": "datasette_cluster_map",
            "static": true,
            "templates": false,
            "version": "0.10",
            "hooks": ["extra_css_urls", "extra_js_urls", "extra_body_script"]
        }
    ]

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

Shows details of threads and ``asyncio`` tasks. This endpoint requires the ``permissions-debug`` permission, since it exposes runtime internals. `Threads example <https://latest.datasette.io/-/threads>`_:

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

.. _JsonDataView_tasks:

/-/tasks
--------

Shows the state of every supervised background task registered with :ref:`datasette.add_background_task() <datasette_add_background_task>`; see also :ref:`BackgroundTask <BackgroundTask>` for what each field below means, and :ref:`datasette_lifecycle` for when tasks are launched. This endpoint requires the ``permissions-debug`` permission, since a crashed task's ``exception`` field can reveal internals such as file paths or query text:

.. code-block:: json

    {
        "ok": true,
        "tasks": [
            {
                "name": "my_plugin.poll_for_updates",
                "state": "running",
                "function": "my_plugin.poll_for_updates",
                "started_at": "2026-07-30T12:00:00+00:00",
                "exception": null
            },
            {
                "name": "my_plugin.broken_task",
                "state": "crashed",
                "function": "my_plugin.broken_task",
                "started_at": "2026-07-30T12:00:00+00:00",
                "exception": "ValueError('something went wrong')"
            }
        ],
        "launched": true
    }

Each entry's ``function`` identifies the callable by its dotted module and qualified name.

Each entry's ``state`` is one of ``registered`` (added but not yet launched), ``running``, ``completed``, ``crashed`` or ``cancelled``. ``exception`` is a one-line ``repr()`` of the exception for a ``crashed`` task, or ``null`` otherwise - the full traceback is written to the ``datasette.background_tasks`` logger instead, to keep this payload skimmable.

The top-level ``launched`` flag reports whether the instance has run its one-time background task launch (after ``startup`` hooks finish, or via lifespan/first-request/:ref:`start_background_tasks() <datasette_start_background_tasks>`). It distinguishes "no tasks have been registered" (``tasks`` is empty either way) from "tasks are registered but nothing has armed the launch yet" (``launched`` is ``false`` and every task's ``state`` is still ``registered``) - useful when debugging a host that never triggers Datasette's lifespan events.

.. _JsonDataView_imports:

/-/imports
----------

Lists the :ref:`background imports <importing_background>` this Datasette instance is running or has run - for example the files passed to ``datasette data.csv`` - with their progress. Up to 200 finished imports are kept. ``/-/imports`` shows them as a page, with the same progress bars and "Cancel import" buttons as the progress notices on other pages, and ``/-/imports.json`` returns them as JSON.

This endpoint needs ``view-instance``, and only lists imports into tables the actor is allowed to view. Add ``?database=name`` to only list imports into that database, and ``?table=name`` to only list imports into tables of that name. Imports that ``--get`` runs before answering its request are not listed, since they have finished by the time any request is handled.

.. code-block:: json

    {
      "ok": true,
      "active": 1,
      "imports": [
        {
          "id": "1",
          "database": "scratch",
          "table": "events",
          "source": "events.csv",
          "kind": "file",
          "state": "importing",
          "waiting_for": null,
          "loader": "csv",
          "rows": 234000,
          "bytes": 34521088,
          "size": 1012252043,
          "downloaded": null,
          "download_size": null,
          "fraction": 0.0341032534720209,
          "percent": 3,
          "created_at": "2026-11-06T10:42:14.646362+00:00",
          "started_at": "2026-11-06T10:42:14.722063+00:00",
          "finished_at": null,
          "seconds": 2.976,
          "waited_seconds": 0.0,
          "error": null,
          "on_error": "keep",
          "complete": false,
          "table_exists": true,
          "created_table": true,
          "existing_table": false,
          "rows_kept": null,
          "widenings": [],
          "task": "import scratch.events",
          "status": "importing 3% (234,000 rows)",
          "description": "importing events: 3% (234,000 rows)",
          "table_url": "http://127.0.0.1:8001/scratch/events",
          "cancel_url": "http://127.0.0.1:8001/scratch/events/-/cancel-import",
          "can_cancel": false
        }
      ]
    }

``active`` is the number of imports still running. Imports are listed in the order they were added, and each one has these keys:

``id``
    An identifier for the import, unique within this Datasette instance.

``database`` and ``table``
    Where the rows are going.

``source`` and ``kind``
    What is being imported - a file path, a URL with any passwords, keys or tokens in it redacted, or ``standard input`` - and whether it is a ``file``, a ``url`` or ``stdin``.

``state``
    One of ``queued``, ``downloading`` (downloading a URL or reading standard input), ``importing``, ``done``, ``failed`` or ``cancelled``. While ``queued``, ``waiting_for`` is ``"worker"`` if the import is waiting for one of the import workers, or ``"start"`` if Datasette has not started its background tasks yet.

``loader``
    The format the input is being read as, such as ``csv``. ``null`` until it is known.

``rows``
    How many rows have been imported so far.

``bytes`` and ``size``
    How far into the file Datasette has read, and the size of the file, in bytes. ``downloaded`` and ``download_size`` are the same for a download, or for standard input; ``download_size`` is ``null`` if the server did not say how big the file is.

``fraction`` and ``percent``
    How far the import has got, from ``0.0`` to ``1.0`` and as a whole percentage, or ``null`` when that is not known - for a JSON array, for example. ``percent`` only reaches ``100`` once the import is ``done``.

``created_at``, ``started_at`` and ``finished_at``
    When the import was added, when its background task started - before any time spent downloading or waiting for an import worker - and when it finished, as ISO 8601 timestamps.

``seconds`` and ``waited_seconds``
    How long the import has spent working, and how long it spent waiting for an import worker.

``error``
    The error message, for a failed import.

``complete``
    ``true`` once every row of the input is in the table.

``table_exists``
    Whether the table that this import created is there. ``rows_kept`` is the number of rows that a failed or cancelled import left in the table: the table is incomplete if this is more than zero.

``widenings``
    The columns that changed type part way through the import, as ``{"column": ..., "from": ..., "to": ..., "at_row": ...}`` objects, where ``at_row`` is the number of rows imported before the batch that needed the change. See :ref:`importing_types`.

``status`` and ``description``
    A sentence describing the state of the import, without and with the table name.

``table_url``, ``cancel_url`` and ``can_cancel``
    The table's URL, the URL to ``POST`` to to :ref:`cancel the import <importing_cancel>`, and whether the actor making the request is allowed to cancel it right now.

The other keys - ``on_error``, ``created_table``, ``existing_table`` and ``task``, the name of the import's :ref:`background task <JsonDataView_tasks>` - are mostly useful for debugging.

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
