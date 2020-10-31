Getting started
===============

Play with a live demo
---------------------

The best way to experience Datasette for the first time is with a demo:

* `global-power-plants.datasettes.com <https://global-power-plants.datasettes.com/global-power-plants/global-power-plants>`__ provides a searchable database of power plants around the world, using data from the `World Resources Institude <https://www.wri.org/publication/global-power-plant-database>`__ rendered using the `datasette-cluster-map <https://github.com/simonw/datasette-cluster-map>`__ plugin.
* `fivethirtyeight.datasettes.com <https://fivethirtyeight.datasettes.com/fivethirtyeight>`__ shows Datasette running against over 400 datasets imported from the `FiveThirtyEight GitHub repository <https://github.com/fivethirtyeight/data>`__.

.. _getting_started_glitch:

Try Datasette without installing anything using Glitch
------------------------------------------------------

`Glitch <https://glitch.com/>`__ is a free online tool for building web apps directly from your web browser. You can use Glitch to try out Datasette without needing to install any software on your own computer.

Here's a demo project on Glitch which you can use as the basis for your own experiments:

`glitch.com/~datasette-csvs <https://glitch.com/~datasette-csvs>`__

Glitch allows you to "remix" any project to create your own copy and start editing it in your browser. You can remix the ``datasette-csvs`` project by clicking this button:

.. image:: https://cdn.glitch.com/2703baf2-b643-4da7-ab91-7ee2a2d00b5b%2Fremix-button.svg
   :target: https://glitch.com/edit/#!/remix/datasette-csvs

Find a CSV file and drag it onto the Glitch file explorer panel - ``datasette-csvs`` will automatically convert it to a SQLite database (using `sqlite-utils <https://github.com/simonw/sqlite-utils>`__) and allow you to start exploring it using Datasette.

If your CSV file has a ``latitude`` and ``longitude`` column you can visualize it on a map by uncommenting the ``datasette-cluster-map`` line in the ``requirements.txt`` file using the Glitch file editor.

Need some data? Try this `Public Art Data <https://data.seattle.gov/Community/Public-Art-Data/j7sn-tdzk>`__ for the city of Seattle - hit "Export" and select "CSV" to download it as a CSV file.

For more on how this works, see `Running Datasette on Glitch <https://simonwillison.net/2019/Apr/23/datasette-glitch/>`__.

.. _getting_started_your_computer:

Using Datasette on your own computer
------------------------------------

First, follow the :ref:`installation` instructions. Now you can run Datasette against a SQLite file on your computer using the following command:

::

    datasette path/to/database.db

This will start a web server on port 8001 - visit http://localhost:8001/
to access the web interface.

Add ``-o`` to open your browser automatically once Datasette has started::

    datasette path/to/database.db -o

Use Chrome on OS X? You can run datasette against your browser history
like so:

::

     datasette ~/Library/Application\ Support/Google/Chrome/Default/History

Now visiting http://localhost:8001/History/downloads will show you a web
interface to browse your downloads data:

.. figure:: https://static.simonwillison.net/static/2017/datasette-downloads.png
   :alt: Downloads table rendered by datasette

http://localhost:8001/History/downloads.json will return that data as
JSON:

::

    {
        "database": "History",
        "columns": [
            "id",
            "current_path",
            "target_path",
            "start_time",
            "received_bytes",
            "total_bytes",
            ...
        ],
        "rows": [
            [
                1,
                "/Users/simonw/Downloads/DropboxInstaller.dmg",
                "/Users/simonw/Downloads/DropboxInstaller.dmg",
                13097290269022132,
                626688,
                0,
                ...
            ]
        ]
    }

http://localhost:8001/History/downloads.json?_shape=objects will return that data as
JSON in a more convenient format:

::

    {
        ...
        "rows": [
            {
                "start_time": 13097290269022132,
                "interrupt_reason": 0,
                "hash": "",
                "id": 1,
                "site_url": "",
                "referrer": "https://www.dropbox.com/downloading?src=index",
                ...
            }
        ]
    }

.. _getting_started_datasette_get:

datasette --get
---------------

The ``--get`` option can specify the path to a page within Datasette and cause Datasette to output the content from that path without starting the web server. This means that all of Datasette's functionality can be accessed directly from the command-line. For example::

    $ datasette --get '/-/versions.json' | jq .
    {
      "python": {
        "version": "3.8.5",
        "full": "3.8.5 (default, Jul 21 2020, 10:48:26) \n[Clang 11.0.3 (clang-1103.0.32.62)]"
      },
      "datasette": {
        "version": "0.46+15.g222a84a.dirty"
      },
      "asgi": "3.0",
      "uvicorn": "0.11.8",
      "sqlite": {
        "version": "3.32.3",
        "fts_versions": [
          "FTS5",
          "FTS4",
          "FTS3"
        ],
        "extensions": {
          "json1": null
        },
        "compile_options": [
          "COMPILER=clang-11.0.3",
          "ENABLE_COLUMN_METADATA",
          "ENABLE_FTS3",
          "ENABLE_FTS3_PARENTHESIS",
          "ENABLE_FTS4",
          "ENABLE_FTS5",
          "ENABLE_GEOPOLY",
          "ENABLE_JSON1",
          "ENABLE_PREUPDATE_HOOK",
          "ENABLE_RTREE",
          "ENABLE_SESSION",
          "MAX_VARIABLE_NUMBER=250000",
          "THREADSAFE=1"
        ]
      }
    }

The exit code will be 0 if the request succeeds and 1 if the request produced an HTTP status code other than 200 - e.g. a 404 or 500 error. This means you can use ``datasette --get /`` to run tests against a Datasette application in a continuous integration environment such as GitHub Actions.

.. _getting_started_serve_help:

datasette serve --help
----------------------

Running ``datasette downloads.db`` executes the default ``serve`` sub-command, and is equivalent to running ``datasette serve downloads.db``. The full list of options to that command is shown below.

.. literalinclude:: datasette-serve-help.txt
