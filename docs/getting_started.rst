Getting started
===============

Play with a live demo
---------------------

The best way to experience Datasette for the first time is with a demo:

* `fivethirtyeight.datasettes.com <https://fivethirtyeight.datasettes.com/fivethirtyeight>`__ shows Datasette running against over 400 datasets imported from the `FiveThirtyEight GitHub repository <https://github.com/fivethirtyeight/data>`__.
* `sf-trees.datasettes.com <https://sf-trees.datasettes.com/trees/Street_Tree_List>`__ demonstrates the `datasette-cluster-map <https://github.com/simonw/datasette-cluster-map>`__ plugin running against 190,000 trees imported from `data.sfgov.org <https://data.sfgov.org/City-Infrastructure/Street-Tree-List/tkzw-k3nq>`__.

.. _glitch:

Try Datasette without installing anything using Glitch
------------------------------------------------------

`Glitch <https://glitch.com/>`__ is a free online tool for building web apps directly from your web browser. You can use Glitch to try out Datasette without needing to install any software on your own computer.

Here's a demo project on Glitch which you can use as the basis for your own experiments:

`glitch.com/~datasette-csvs <https://glitch.com/~datasette-csvs>`__

Glitch allows you to "remix" any project to create your own copy and start editing it in your browser. You can also remix the ``datasette-csvs`` project by clicking this button:

.. image:: https://cdn.glitch.com/2703baf2-b643-4da7-ab91-7ee2a2d00b5b%2Fremix-button.svg
   :target: https://glitch.com/edit/#!/remix/datasette-csvs

Find a CSV file and drag it onto the Glitch file explorer panel - ``datasette-csvs`` will automatically convert it to a SQLite database (using `csvs-to-sqlite <https://github.com/simonw/csvs-to-sqlite>`__) and allow you to start exploring it using Datasette.

If your CSV file has a ``latitude`` and ``longitude`` column you can visualize it on a map by uncomminting the ``datasette-cluster-map`` line in the ``requirements.txt`` file using the Glitch file editor.

Need some data? Try this `Public Art Data <https://data.seattle.gov/Community/Public-Art-Data/j7sn-tdzk>`__ for the city of Seattle - hit "Export" and select "CSV" to download it as a CSV file.

For more on how this works, see `Running Datasette on Glitch <https://simonwillison.net/2019/Apr/23/datasette-glitch/>`__.

Using Datasette on your own computer
------------------------------------

First, follow the :ref:`installation` instructions. Now you can run Datasette against a SQLite file on your computer using the following command:

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

.. literalinclude:: datasette-serve-help.txt
