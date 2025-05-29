Getting started
===============

.. _getting_started_demo:

Play with a live demo
---------------------

The best way to experience Datasette for the first time is with a demo:

* `global-power-plants.datasettes.com <https://global-power-plants.datasettes.com/global-power-plants/global-power-plants>`__ provides a searchable database of power plants around the world, using data from the `World Resources Institude <https://www.wri.org/publication/global-power-plant-database>`__ rendered using the `datasette-cluster-map <https://github.com/simonw/datasette-cluster-map>`__ plugin.
* `fivethirtyeight.datasettes.com <https://fivethirtyeight.datasettes.com/fivethirtyeight>`__ shows Datasette running against over 400 datasets imported from the `FiveThirtyEight GitHub repository <https://github.com/fivethirtyeight/data>`__.

.. _getting_started_tutorial:

Follow a tutorial
-----------------

Datasette has several `tutorials <https://datasette.io/tutorials>`__ to help you get started with the tool. Try one of the following:

- `Exploring a database with Datasette <https://datasette.io/tutorials/explore>`__ shows how to use the Datasette web interface to explore a new database.
- `Learn SQL with Datasette <https://datasette.io/tutorials/learn-sql>`__ introduces SQL, and shows how to use that query language to ask questions of your data.
- `Cleaning data with sqlite-utils and Datasette <https://datasette.io/tutorials/clean-data>`__ guides you through using `sqlite-utils <https://sqlite-utils.datasette.io/>`__ to turn a CSV file into a database that you can explore using Datasette.

.. _getting_started_datasette_lite:

Datasette in your browser with Datasette Lite
---------------------------------------------

`Datasette Lite <https://lite.datasette.io/>`__ is Datasette packaged using WebAssembly so that it runs entirely in your browser, no Python web application server required.

You can pass a URL to a CSV, SQLite or raw SQL file directly to Datasette Lite to explore that data in your browser.

This `example link <https://lite.datasette.io/?url=https%3A%2F%2Fraw.githubusercontent.com%2FNUKnightLab%2Fsql-mysteries%2Fmaster%2Fsql-murder-mystery.db#/sql-murder-mystery>`__ opens Datasette Lite and loads the SQL Murder Mystery example database from `Northwestern University Knight Lab <https://github.com/NUKnightLab/sql-mysteries>`__. 

.. _getting_started_codespaces:

Try Datasette without installing anything with Codespaces
---------------------------------------------------------

`GitHub Codespaces <https://github.com/features/codespaces/>`__ offers a free browser-based development environment that lets you run a development server without installing any local software.

Here's a demo project on GitHub which you can use as the basis for your own experiments:

`github.com/datasette/datasette-studio <https://github.com/datasette/datasette-studio>`__

The README file in that repository has instructions on how to get started.

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

     datasette ~/Library/Application\ Support/Google/Chrome/Default/History --nolock

The ``--nolock`` option ignores any file locks. This is safe as Datasette will open the file in read-only mode.

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
