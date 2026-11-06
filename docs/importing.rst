.. _importing:

Loading CSV, TSV and JSON files
===============================

Datasette serves SQLite databases, but you don't need a SQLite database to get started. Pass Datasette a CSV, TSV, JSON or newline-delimited JSON file, a URL or data piped to standard input and it will load that data into SQLite tables for you to explore.

.. _importing_quick_start:

Quick start
-----------

If you have `uv <https://docs.astral.sh/uv/>`__ installed you can explore a CSV file without installing anything else first::

    uvx datasette data.csv

If you have already :ref:`installed Datasette <installation>`, run this instead::

    datasette data.csv

Datasette starts serving at http://127.0.0.1:8001/ straight away and loads the file in the background into a table called ``data``, in a temporary database called ``scratch``. You can browse the rows at http://127.0.0.1:8001/scratch/data while the rest of the file loads.

You can load several files at once. Each one becomes a table in the same ``scratch`` database::

    datasette events.csv venues.json

Since they are in the same database you can join them using SQL on the ``scratch`` database page:

.. code-block:: sql

    select events.title, venues.name, venues.city
    from events join venues on events.venue_id = venues.id

The ``scratch`` database is deleted when Datasette stops. Add ``--save`` to keep the data in a SQLite file instead (see :ref:`importing_save`)::

    datasette events.csv venues.json --save events.db

Data files can be mixed with SQLite database files. The database files are served as usual and the data files are loaded into ``scratch``::

    datasette fixtures.db events.csv

To join across the two, start Datasette with :ref:`--crossdb <cross_database_queries>` and use ``scratch.events`` and ``fixtures.tablename`` in a query on the ``/_memory`` page.

.. _importing_inputs:

What you can load
-----------------

Each argument that is not a SQLite database can be one of the following:

- A CSV, TSV, JSON or newline-delimited JSON file: ``datasette data.csv``
- An ``http://`` or ``https://`` URL: ``datasette https://example.com/data.csv``. Datasette downloads it to a temporary file first, following redirects.
- ``-``, to read standard input: ``curl -s https://example.com/data.json | datasette -``
- A named pipe or a shell process substitution, which is copied to a temporary file first: ``datasette <(gunzip -c data.csv.gz)``

Datasette picks a table name for each input:

- For a file, the file name without its extension. Anything other than letters, numbers and underscores becomes ``_``, so ``Sales Report (2024).csv`` loads into ``Sales_Report_2024``.
- For a URL, the last part of the path if it looks like a file name (``https://example.com/data/events.csv`` loads into ``events``), otherwise the host name (``https://example.com/api/`` loads into ``example_com``).
- For standard input, ``stdin``. For process substitution, ``pipe``.
- If two inputs would get the same name, the second gets ``_2`` added to it, the third ``_3`` and so on: ``datasette 2023/events.csv 2024/events.csv`` loads ``events`` and ``events_2``.

.. _importing_table_names:

Choosing a table name
~~~~~~~~~~~~~~~~~~~~~

Put a table name and a colon in front of an input to choose its table name::

    datasette events:data/2024-export.csv venues:https://example.com/venues.json
    curl -s https://example.com/data.csv | datasette events:-

``stdin`` can be used in place of ``-``, so ``events:stdin`` works too.

Colons that are part of the input are left alone: a file that exists is always loaded from that path even if its name contains a colon, ``https://`` is never mistaken for a table name, and neither is a Windows drive letter, so ``datasette C:\data\events.csv`` loads ``C:\data\events.csv`` into a table called ``events``.

.. _importing_format_suffix:

Choosing a format
~~~~~~~~~~~~~~~~~

Datasette works out the format of each input from its contents (see :ref:`importing_formats`). To skip that and pick the format yourself, add a colon and the name of the format to the end of the input: ``csv``, ``tsv``, ``json`` or ``ndjson`` (``nl`` and ``jsonl`` mean ``ndjson`` too), or the name of a format added by a :ref:`plugin <importing_plugins>`::

    datasette export.txt:tsv
    datasette events:export.dat:csv
    cat log.txt | datasette stdin:ndjson

The suffix is only treated as a format if it is the name of one. A colon in a URL's query string is always part of the URL.

For standard input, use ``stdin`` and the format, as in ``stdin:ndjson`` above. ``-:csv`` works too, but because it starts with ``-`` the command line would read it as an option. To use it, put it after ``--`` and spell out the ``serve`` command, which can usually be left out::

    cat data.txt | datasette serve -- -:csv

.. _importing_formats:

How the format is detected
--------------------------

Datasette looks at the contents of each input, not at its file extension or the ``Content-Type`` a server sends. A ``.txt`` file containing CSV loads as CSV, and a ``.csv`` file that actually contains JSON loads as JSON. Datasette reads the first 64KB of the input, and up to 1MB if that was not enough to be sure.

The line Datasette prints for each input says which format it used, in brackets::

    Loaded 100,000 rows from events.csv (csv) into table events in 1.88s

The built-in formats are:

``csv``
    Comma-separated values with a header row. Files separated by semicolons or pipe characters (``|``) are read as CSV too.

``tsv``
    Tab-separated values with a header row.

``json``
    A JSON array of objects, one row per object. Datasette also accepts a single object, which becomes one row; an object with one key holding a list of objects, such as an API response like ``{"total": 2, "results": [...]}``, which loads the objects in that list; and objects printed one after another, as ``jq`` prints them.

``ndjson``
    Newline-delimited JSON, with one object on each line.

Text with no delimiters at all is read as CSV with a single column, as long as it has at least two lines: the first line is the column name.

If Datasette can't recognize an input it stops with an error before loading anything::

    Error: Could not load ragged.csv: it does not look like any format Datasette can read (csv, json, ndjson, tsv). ...

Adding the format, as in ``ragged.csv:csv``, often fixes this: see :ref:`importing_messy`.

.. _importing_scratch:

The scratch database
--------------------

Inputs that are not loaded into a ``--save`` file go into a database called ``scratch``.

``scratch`` is read-only for everyone, including the root user. Anything written to it would be lost when Datasette stops and would not be in the file that the ``--save`` command creates, so Datasette only lets the imports themselves write to it: insert, update, delete, create, alter and drop actions are denied, as are writable SQL queries. To edit imported data, load it into a file with ``--save``.

``scratch`` is a SQLite file in WAL mode in your system's temporary directory, and it is deleted when Datasette stops. It needs about as much free space as the data it holds. Where the temporary directory is a ``tmpfs`` file system, as ``/tmp`` is on some Linux distributions, that space is memory. To load large files on such a system, set the ``TMPDIR`` environment variable to a directory on disk, or use ``--save``. Downloads and copies of standard input are kept in the same temporary directory until they have been loaded.

If you are also serving a database file that is called ``scratch``, the temporary database gets another name, such as ``scratch_2``, and Datasette tells you which.

The ``scratch`` database page lists the tables that were loaded and shows the command that would load the same inputs into a SQLite file, like this::

    datasette events.csv venues.json --save data.db

The suggested file name is one that does not exist yet. Passwords, keys, tokens and signatures in URLs are shown as ``REDACTED`` in that command, since anyone who can see the page can see it: put the real values back before you run it. If one of the inputs was standard input you will need to pipe the same data into the command again.

.. _importing_save:

Saving to a SQLite file
-----------------------

Use ``--save FILE`` to load the inputs into a SQLite database file instead of ``scratch``::

    datasette events.csv venues.json --save events.db

If the file does not exist Datasette creates it, in SQLite's `WAL mode <https://www.sqlite.org/wal.html>`__ so that pages can be read while rows are being written (unless you also use ``--get``). If it is an existing SQLite database the new tables are added to it and its other tables, and its journal mode, are left alone. Unlike ``scratch``, the file is an ordinary writable database, subject to the usual :ref:`permissions <authentication_permissions>`. Next time you can serve it without loading anything::

    datasette events.db

Loading into a table that already exists is an error, reported before anything is loaded::

    Error: Table 'events' already exists in events.db, so events.csv cannot be loaded into it. Choose another table name with NAME:events.csv, use a different --save file or drop that table first.

To load the file into a different table, :ref:`choose a table name <importing_table_names>`::

    datasette events_2025:events.csv --save events.db

If an import into a ``--save`` file fails or is cancelled, the rows it had loaded are removed again, since a SQLite file has nowhere to record that a table is incomplete. A ``--save`` file that Datasette created in this run is deleted again if nothing ends up in it.

.. _importing_background:

Background imports
------------------

Datasette starts serving as soon as it has checked the inputs, then imports them in the background. Mistakes in local files, such as a file that does not exist or one Datasette cannot recognize, are reported before the server starts. URLs are downloaded and standard input is read in the background, so problems with those show up as failed imports.

While an import runs, Datasette shows its progress:

- The homepage, the database page and the page for the table show a progress bar for each import, updated every second.
- The table page shows the rows loaded so far, with a note that counts, facets and sorting will change as more rows arrive. It suggests reloading the page when the import has finished.
- Visiting a table that has not been created yet, because its import is still queued or downloading, shows a "will appear here soon" page with the progress, with a ``404`` status. If the import failed before it created the table, the page says so.
- ``/-/imports`` lists every import and ``/-/imports.json`` returns the same information as JSON: see :ref:`introspection_imports`.
- Each import is a :ref:`background task <datasette_add_background_task>` called ``import DATABASE.TABLE``, listed on the :ref:`/-/tasks <JsonDataView_tasks>` page.
- Datasette prints a line to the terminal (standard error) when each import starts, every ten seconds while it runs and when it finishes:

::

    Importing events.csv (csv, 14.5 MB) into table events
    Importing venues.json (json, 64.6 KB) into table venues
    Loaded 2,000 rows from venues.json (json) into table venues in 0.27s
    Loaded 100,000 rows from events.csv (csv) into table events in 1.88s
      column ticket_price changed from integer to float at row 5,001
    Imported 2 of 2 inputs (102,000 rows) in 1.9s

Each input is parsed by an import worker process of its own, and at most two of those run at a time. The other inputs wait their turn, in the order they were given on the command line, and are shown as "queued, waiting for an import worker". Downloads and standard input are read straight away, without waiting for a worker. A parser that crashes or runs out of memory takes down its worker process, not Datasette.

If an import into ``scratch`` fails part way through, the rows loaded before the problem are kept and the table is labelled as incomplete on its page, the database page and the homepage, along with the error and how many rows it has::

    import failed: Line 4 is not valid JSON: Expecting ',' delimiter at column 25 (the table has the first 3 rows)

.. _importing_cancel:

Cancelling an import
~~~~~~~~~~~~~~~~~~~~

Actors with the :ref:`cancel-import <actions_cancel_import>` permission see a "Cancel import" button next to each running import. Like a failed import, a cancelled import into ``scratch`` keeps the rows loaded so far and its table is labelled incomplete. A cancelled import into a ``--save`` file is removed.

Only the root user can cancel imports by default. Start Datasette with ``--root`` and sign in using the link it prints to see the buttons::

    datasette big.csv --root

To let other actors cancel imports, grant them ``cancel-import`` in your :ref:`configuration <authentication_permissions_config>`:

.. [[[cog
    from metadata_doc import config_example
    config_example(cog, """
        permissions:
          cancel-import:
            id: alice
    """)
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml


            permissions:
              cancel-import:
                id: alice


.. tab:: datasette.json

    .. code-block:: json

        {
          "permissions": {
            "cancel-import": {
              "id": "alice"
            }
          }
        }
.. [[[end]]]

It can also be granted for a single database or table, in the same way as :ref:`insert-row <authentication_permissions_table>`.

To cancel an import from a script, send a ``POST`` to ``/DATABASE/TABLE/-/cancel-import`` with an :ref:`API token <CreateTokenView>` for an actor who has that permission::

    curl -X POST http://127.0.0.1:8001/scratch/events/-/cancel-import \
      -H "Authorization: Bearer $TOKEN"

Datasette waits up to five seconds for the import to stop, then responds with the state of the import, in the same format as an item from :ref:`/-/imports.json <introspection_imports>`:

.. code-block:: json

    {
      "ok": true,
      "import": {
        "table": "events",
        "state": "cancelled",
        "status": "import cancelled (the table has the first 1,711,000 rows)",
        "rows_kept": 1711000,
        "...": "..."
      }
    }

The ``import`` key is only included if the actor is allowed to view the table. The response is a ``403`` error if the actor cannot cancel imports into that table, ``404`` if there is no import into that table (or the actor can neither cancel it nor see the table), and ``409`` if the import had already finished.

Stopping Datasette, for example with ``Ctrl+C``, stops any imports that are still running. ``scratch`` is deleted. Tables that had not finished loading into a ``--save`` file are removed, and a ``--save`` file created by this run is deleted if nothing was imported into it.

.. _importing_get_reload:

--get and --reload
------------------

With :ref:`--get <cli_datasette_get>`, Datasette loads every input before it handles the request, then prints the response and exits. Nothing runs in the background, so ``/-/imports.json`` has no imports in it. If any input fails to load, Datasette exits with an error and keeps nothing: tables already loaded into a ``--save`` file in that run are dropped again, and a ``--save`` file that the run created is deleted. This is useful for scripts and for trying things out::

    datasette events.csv --get '/scratch/events.json?_shape=array&_size=3'

With ``--reload``, Datasette restarts when code such as a plugin, the configuration or the metadata changes, as usual, and also when one of the input files changes. Within one ``--reload`` session:

- URLs are downloaded and standard input is read only once. Later restarts load the same copy. Stop and start Datasette to download a URL again.
- ``scratch`` lasts until you stop Datasette. A restart keeps the tables whose inputs have not changed, and loads just the inputs that changed again::

    Kept table venues from the previous load: venues.json has not changed

- The same goes for tables the session created in a ``--save`` file.

.. _importing_types:

How column types are detected
-----------------------------

Datasette creates each table with ``INTEGER``, ``REAL`` (float) and ``TEXT`` columns, based on the values in the first 1,000 rows of the input.

- A column only gets a numeric type if every value in it is a number, not counting empty values. One value that is not, such as ``N/A``, makes the whole column ``TEXT``.
- If a later row has a value that doesn't fit, the column changes type before that row is inserted: from integer to float, or from either to text. Columns never change back, and nothing is lost when a column becomes text - it holds the values exactly as they were written in the file. Datasette prints a line when this happens::

      column ticket_price changed from integer to float at row 5,001

  Rows are inserted in batches, so the row number is the first row of the batch that needed the change, not necessarily the row with the new value in it.

- Numbers with leading zeros, such as ``02134`` or ``007``, are text, so ZIP codes and other codes keep their zeros. So are numbers written with a decimal comma (``1,5``), thousands separators or currency symbols.
- Empty values become ``NULL`` in integer and float columns. In text columns they stay as empty strings.
- In JSON, ``true`` and ``false`` are stored as ``1`` and ``0``, and nested arrays and objects are stored as JSON text. You can use SQLite's `JSON functions <https://www.sqlite.org/json1.html>`__ to query them, for example ``select json_extract(owner, '$.name') from pets``.
- A column with no values at all is ``INTEGER``.

Tables are created without a primary key, so their rows are identified by SQLite's ``rowid``. To add a primary key or change column types, save the data with ``--save`` and use a tool such as `sqlite-utils transform <https://sqlite-utils.datasette.io/en/stable/cli.html#transforming-tables>`__.

A CSV or TSV file with a header row and no rows creates a table with ``TEXT`` columns and no rows. A file that is just a single column name is not recognized as CSV: add ``:csv`` to load it. A JSON file with no rows creates no table.

.. _importing_messy:

Messy data
----------

Datasette tries hard to load untidy files without losing anything.

- **Byte order marks**: a UTF-8 byte order mark at the start of a file is ignored.
- **Encodings**: files are read as UTF-8, and any bytes that are not valid UTF-8 are read as Windows-1252, which also covers Latin-1. Files exported from older spreadsheet software keep their accented characters, even when the file mixes the two encodings.
- **Delimiters**: commas, semicolons, pipes and tabs are detected from the contents. If a field starts with a ``"`` that is not meant as a quote, and treating it as one would swallow the rest of the file, quotes are read as ordinary characters.
- **Headers**: blank column names become ``column_1``, ``column_2`` and so on, by position, and repeated names get a suffix, so ``name,name`` becomes ``name`` and ``name_2``.
- **Ragged rows**: a row with fewer values than the header gets ``NULL`` for the missing columns. A row with more values keeps the extra ones, as a JSON list, in an ``_extra`` column. A file where many rows have more values than the header may not be recognized as CSV at all: add ``:csv`` to load it anyway.
- **Wrapped JSON**: if a JSON object has exactly one key holding a list of objects, those objects are the rows and the other keys are ignored.

Datasette prints a note in the terminal when it does something you might not expect, such as ignoring keys or putting values in ``_extra``::

    Import worker: Importing the 2 objects in 'results', ignoring 'total', 'next'

Some things are not supported yet:

- **UTF-16** files, such as those saved by Excel as "Unicode Text", are not recognized. Convert them to UTF-8 first::

      iconv -f UTF-16 -t UTF-8 data.txt | datasette data:-

- **Lines before the header**, such as a title or a note at the top of a report, are not skipped, and usually stop the file from being recognized at all. Remove them first, for example::

      tail -n +3 report.csv | datasette report:-

- **Compressed files**, such as ``data.csv.gz``, are not decompressed: use ``gunzip -c data.csv.gz | datasette data:-``.

.. _importing_large:

Large files
-----------

CSV, TSV and newline-delimited JSON files can be much bigger than your computer's memory. Some things to bear in mind when loading large files:

- You can start exploring straight away: rows can be queried as soon as they are loaded, a batch at a time, but counts, facets and sorted pages will change until the import has finished.
- A JSON array has to be read into memory all at once, which takes several times the size of the file: about three to four times for a file of simple objects. CSV, TSV and newline-delimited JSON are read a line at a time and need very little memory, so for large files prefer newline-delimited JSON. You can convert a JSON array using ``jq -c '.[]' data.json > data.ndjson``.
- The progress percentage is how far into the file Datasette has read. For JSON arrays that isn't known, so the progress shows the number of rows loaded so far instead.
- ``scratch`` needs free space in the temporary directory, see :ref:`importing_scratch`. Datasette suggests ``--save`` when a download is over 100MB, so that you don't lose the data when Datasette stops.
- If you started the wrong import, :ref:`cancel it <importing_cancel>`.

.. _importing_upload:

Uploading files
---------------

You can also create a table by uploading a file through Datasette's web interface. Choose a file in the "Create table" dialog in a database's actions menu, or drop one onto it, and it will be uploaded to Datasette, which imports it in the background in the same way as a file given on the command line.

Uploads need the :ref:`create-table <actions_create_table>` permission on the database. They are never accepted into ``scratch``, which is read-only. The largest file you can upload is set by :ref:`setting_max_upload_mb`, and the :ref:`upload API <json_api_upload>` lets you upload files from your own code.

Uploads are not available in `Datasette Lite <https://lite.datasette.io/>`__, which runs Datasette in the browser using Pyodide, where Datasette can't start import worker processes.

.. _importing_plugins:

Formats from plugins
--------------------

Plugins can teach Datasette to load other formats, such as GeoJSON or Parquet, using the :ref:`plugin_register_import_loaders` plugin hook. Once a plugin is installed its format is detected in the same way as the built-in ones, and you can :ref:`choose it by name <importing_format_suffix>`::

    datasette places.geojson
    datasette places.json:geojson

If you load a plugin using ``--plugins-dir``, the ``--save`` command shown on the ``scratch`` database page includes that option.
