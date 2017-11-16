Datasette
=========

*An instant JSON API for your SQLite databases*

Datasette provides an instant, read-only JSON API for any SQLite
database. It also provides tools for packaging the database up as a
Docker container and deploying that container to hosting providers such
as `Zeit Now`_.

Some examples: https://github.com/simonw/datasette/wiki/Datasettes

Contents
--------

.. toctree::
   :maxdepth: 2

   changelog

Getting started
---------------

::

    pip3 install datasette

Datasette requires Python 3.5 or higher.

Basic usage
-----------

::

    datasette serve path/to/database.db

This will start a web server on port 8001 - visit http://localhost:8001/
to access the web interface.

``serve`` is the default subcommand, you can omit it if you like.

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
        "table_rows": 576,
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

http://localhost:8001/History/downloads.jsono will return that data as
JSON in a more convenient but less efficient format:

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

datasette serve options
-----------------------

::

    $ datasette serve --help
    Usage: datasette serve [OPTIONS] [FILES]...

      Serve up specified SQLite database files with a web UI

    Options:
      -h, --host TEXT              host for server, defaults to 127.0.0.1
      -p, --port INTEGER           port for server, defaults to 8001
      --debug                      Enable debug mode - useful for development
      --reload                     Automatically reload if code change detected -
                                   useful for development
      --cors                       Enable CORS by

.. _Zeit Now: https://zeit.co/now
