.. _cli_reference:

===============
 CLI reference
===============

The ``datasette`` CLI tool provides a number of commands.

Running ``datasette`` without specifying a command runs the default command, ``datasette serve``.  See :ref:`cli_help_serve___help` for the full list of options for that command.

.. [[[cog
    from datasette import cli
    from click.testing import CliRunner
    import textwrap
    def help(args):
        title = "datasette " + " ".join(args)
        cog.out("\n::\n\n")
        result = CliRunner().invoke(cli.cli, args)
        output = result.output.replace("Usage: cli ", "Usage: datasette ")
        cog.out(textwrap.indent(output, '    '))
        cog.out("\n\n")
.. ]]]
.. [[[end]]]

.. _cli_help___help:

datasette --help
================

Running ``datasette --help`` shows a list of all of the available commands.

.. [[[cog
    help(["--help"])
.. ]]]

::

    Usage: datasette [OPTIONS] COMMAND [ARGS]...

      Datasette is an open source multi-tool for exploring and publishing data

      About Datasette: https://datasette.io/
      Full documentation: https://docs.datasette.io/

    Options:
      --version  Show the version and exit.
      --help     Show this message and exit.

    Commands:
      serve*     Serve up specified SQLite database files with a web UI
      inspect    Generate JSON summary of provided database files
      install    Install plugins and packages from PyPI into the same...
      package    Package SQLite files into a Datasette Docker container
      plugins    List currently installed plugins
      publish    Publish specified SQLite database files to the internet along...
      uninstall  Uninstall plugins and Python packages from the Datasette...


.. [[[end]]]

Additional commands added by plugins that use the :ref:`plugin_hook_register_commands` hook will be listed here as well.

.. _cli_help_serve___help:

datasette serve
===============

This command starts the Datasette web application running on your machine::

    datasette serve mydatabase.db

Or since this is the default command you can run this instead::

    datasette mydatabase.db

Once started you can access it at ``http://localhost:8001``

.. [[[cog
    help(["serve", "--help"])
.. ]]]

::

    Usage: datasette serve [OPTIONS] [FILES]...

      Serve up specified SQLite database files with a web UI

    Options:
      -i, --immutable PATH            Database files to open in immutable mode
      -h, --host TEXT                 Host for server. Defaults to 127.0.0.1 which
                                      means only connections from the local machine
                                      will be allowed. Use 0.0.0.0 to listen to all
                                      IPs and allow access from other machines.
      -p, --port INTEGER RANGE        Port for server, defaults to 8001. Use -p 0 to
                                      automatically assign an available port.
                                      [0<=x<=65535]
      --uds TEXT                      Bind to a Unix domain socket
      --reload                        Automatically reload if code or metadata
                                      change detected - useful for development
      --cors                          Enable CORS by serving Access-Control-Allow-
                                      Origin: *
      --load-extension PATH:ENTRYPOINT?
                                      Path to a SQLite extension to load, and
                                      optional entrypoint
      --inspect-file TEXT             Path to JSON file created using "datasette
                                      inspect"
      -m, --metadata FILENAME         Path to JSON/YAML file containing
                                      license/source metadata
      --template-dir DIRECTORY        Path to directory containing custom templates
      --plugins-dir DIRECTORY         Path to directory containing custom plugins
      --static MOUNT:DIRECTORY        Serve static files from this directory at
                                      /MOUNT/...
      --memory                        Make /_memory database available
      --config CONFIG                 Deprecated: set config option using
                                      configname:value. Use --setting instead.
      --setting SETTING...            Setting, see
                                      docs.datasette.io/en/stable/settings.html
      --secret TEXT                   Secret used for signing secure values, such as
                                      signed cookies
      --root                          Output URL that sets a cookie authenticating
                                      the root user
      --get TEXT                      Run an HTTP GET request against this path,
                                      print results and exit
      --version-note TEXT             Additional note to show on /-/versions
      --help-settings                 Show available settings
      --pdb                           Launch debugger on any errors
      -o, --open                      Open Datasette in your web browser
      --create                        Create database files if they do not exist
      --crossdb                       Enable cross-database joins using the /_memory
                                      database
      --nolock                        Ignore locking, open locked files in read-only
                                      mode
      --ssl-keyfile TEXT              SSL key file
      --ssl-certfile TEXT             SSL certificate file
      --help                          Show this message and exit.


.. [[[end]]]


.. _cli_datasette_get:

datasette --get
---------------

The ``--get`` option to ``datasette serve`` (or just ``datasette``) specifies the path to a page within Datasette and causes Datasette to output the content from that path without starting the web server.

This means that all of Datasette's functionality can be accessed directly from the command-line.

For example::

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

The exit code will be 0 if the request succeeds and 1 if the request produced an HTTP status code other than 200 - e.g. a 404 or 500 error.

This lets you use ``datasette --get /`` to run tests against a Datasette application in a continuous integration environment such as GitHub Actions.

.. _cli_help_serve___help_settings:

datasette serve --help-settings
-------------------------------

This command outputs all of the available Datasette :ref:`settings <settings>`.

These can be passed to ``datasette serve`` using ``datasette serve --setting name value``.

.. [[[cog
    help(["--help-settings"])
.. ]]]

::

    Settings:
      default_page_size            Default page size for the table view
                                   (default=100)
      max_returned_rows            Maximum rows that can be returned from a table or
                                   custom query (default=1000)
      num_sql_threads              Number of threads in the thread pool for
                                   executing SQLite queries (default=3)
      sql_time_limit_ms            Time limit for a SQL query in milliseconds
                                   (default=1000)
      default_facet_size           Number of values to return for requested facets
                                   (default=30)
      facet_time_limit_ms          Time limit for calculating a requested facet
                                   (default=200)
      facet_suggest_time_limit_ms  Time limit for calculating a suggested facet
                                   (default=50)
      allow_facet                  Allow users to specify columns to facet using
                                   ?_facet= parameter (default=True)
      default_allow_sql            Allow anyone to run arbitrary SQL queries
                                   (default=True)
      allow_download               Allow users to download the original SQLite
                                   database files (default=True)
      suggest_facets               Calculate and display suggested facets
                                   (default=True)
      default_cache_ttl            Default HTTP cache TTL (used in Cache-Control:
                                   max-age= header) (default=5)
      cache_size_kb                SQLite cache size in KB (0 == use SQLite default)
                                   (default=0)
      allow_csv_stream             Allow .csv?_stream=1 to download all rows
                                   (ignoring max_returned_rows) (default=True)
      max_csv_mb                   Maximum size allowed for CSV export in MB - set 0
                                   to disable this limit (default=100)
      truncate_cells_html          Truncate cells longer than this in HTML table
                                   view - set 0 to disable (default=2048)
      force_https_urls             Force URLs in API output to always use https://
                                   protocol (default=False)
      template_debug               Allow display of template debug information with
                                   ?_context=1 (default=False)
      trace_debug                  Allow display of SQL trace debug information with
                                   ?_trace=1 (default=False)
      base_url                     Datasette URLs should use this base path
                                   (default=/)



.. [[[end]]]

.. _cli_help_plugins___help:

datasette plugins
=================

Output JSON showing all currently installed plugins, their versions, whether they include static files or templates and which :ref:`plugin_hooks` they use.

.. [[[cog
    help(["plugins", "--help"])
.. ]]]

::

    Usage: datasette plugins [OPTIONS]

      List currently installed plugins

    Options:
      --all                    Include built-in default plugins
      --plugins-dir DIRECTORY  Path to directory containing custom plugins
      --help                   Show this message and exit.


.. [[[end]]]

Example output:

.. code-block:: json

    [
        {
            "name": "datasette-geojson",
            "static": false,
            "templates": false,
            "version": "0.3.1",
            "hooks": [
                "register_output_renderer"
            ]
        },
        {
            "name": "datasette-geojson-map",
            "static": true,
            "templates": false,
            "version": "0.4.0",
            "hooks": [
                "extra_body_script",
                "extra_css_urls",
                "extra_js_urls"
            ]
        },
        {
            "name": "datasette-leaflet",
            "static": true,
            "templates": false,
            "version": "0.2.2",
            "hooks": [
                "extra_body_script",
                "extra_template_vars"
            ]
        }
    ]


.. _cli_help_install___help:

datasette install
=================

Install new Datasette plugins. This command works like ``pip install`` but ensures that your plugins will be installed into the same environment as Datasette.

This command::

    datasette install datasette-cluster-map

Would install the `datasette-cluster-map <https://datasette.io/plugins/datasette-cluster-map>`__ plugin.

.. [[[cog
    help(["install", "--help"])
.. ]]]

::

    Usage: datasette install [OPTIONS] PACKAGES...

      Install plugins and packages from PyPI into the same environment as Datasette

    Options:
      -U, --upgrade  Upgrade packages to latest version
      --help         Show this message and exit.


.. [[[end]]]

.. _cli_help_uninstall___help:

datasette uninstall
===================

Uninstall one or more plugins.

.. [[[cog
    help(["uninstall", "--help"])
.. ]]]

::

    Usage: datasette uninstall [OPTIONS] PACKAGES...

      Uninstall plugins and Python packages from the Datasette environment

    Options:
      -y, --yes  Don't ask for confirmation
      --help     Show this message and exit.


.. [[[end]]]

.. _cli_help_publish___help:

datasette publish
=================

Shows a list of available deployment targets for :ref:`publishing data <publishing>` with Datasette.

Additional deployment targets can be added by plugins that use the :ref:`plugin_hook_publish_subcommand` hook.

.. [[[cog
    help(["publish", "--help"])
.. ]]]

::

    Usage: datasette publish [OPTIONS] COMMAND [ARGS]...

      Publish specified SQLite database files to the internet along with a
      Datasette-powered interface and API

    Options:
      --help  Show this message and exit.

    Commands:
      cloudrun  Publish databases to Datasette running on Cloud Run
      heroku    Publish databases to Datasette running on Heroku


.. [[[end]]]


.. _cli_help_publish_cloudrun___help:

datasette publish cloudrun
==========================

See :ref:`publish_cloud_run`.

.. [[[cog
    help(["publish", "cloudrun", "--help"])
.. ]]]

::

    Usage: datasette publish cloudrun [OPTIONS] [FILES]...

      Publish databases to Datasette running on Cloud Run

    Options:
      -m, --metadata FILENAME         Path to JSON/YAML file containing metadata to
                                      publish
      --extra-options TEXT            Extra options to pass to datasette serve
      --branch TEXT                   Install datasette from a GitHub branch e.g.
                                      main
      --template-dir DIRECTORY        Path to directory containing custom templates
      --plugins-dir DIRECTORY         Path to directory containing custom plugins
      --static MOUNT:DIRECTORY        Serve static files from this directory at
                                      /MOUNT/...
      --install TEXT                  Additional packages (e.g. plugins) to install
      --plugin-secret <TEXT TEXT TEXT>...
                                      Secrets to pass to plugins, e.g. --plugin-
                                      secret datasette-auth-github client_id xxx
      --version-note TEXT             Additional note to show on /-/versions
      --secret TEXT                   Secret used for signing secure values, such as
                                      signed cookies
      --title TEXT                    Title for metadata
      --license TEXT                  License label for metadata
      --license_url TEXT              License URL for metadata
      --source TEXT                   Source label for metadata
      --source_url TEXT               Source URL for metadata
      --about TEXT                    About label for metadata
      --about_url TEXT                About URL for metadata
      -n, --name TEXT                 Application name to use when building
      --service TEXT                  Cloud Run service to deploy (or over-write)
      --spatialite                    Enable SpatialLite extension
      --show-files                    Output the generated Dockerfile and
                                      metadata.json
      --memory TEXT                   Memory to allocate in Cloud Run, e.g. 1Gi
      --cpu [1|2|4]                   Number of vCPUs to allocate in Cloud Run
      --timeout INTEGER               Build timeout in seconds
      --apt-get-install TEXT          Additional packages to apt-get install
      --max-instances INTEGER         Maximum Cloud Run instances
      --min-instances INTEGER         Minimum Cloud Run instances
      --help                          Show this message and exit.


.. [[[end]]]


.. _cli_help_publish_heroku___help:

datasette publish heroku
========================

See :ref:`publish_heroku`.

.. [[[cog
    help(["publish", "heroku", "--help"])
.. ]]]

::

    Usage: datasette publish heroku [OPTIONS] [FILES]...

      Publish databases to Datasette running on Heroku

    Options:
      -m, --metadata FILENAME         Path to JSON/YAML file containing metadata to
                                      publish
      --extra-options TEXT            Extra options to pass to datasette serve
      --branch TEXT                   Install datasette from a GitHub branch e.g.
                                      main
      --template-dir DIRECTORY        Path to directory containing custom templates
      --plugins-dir DIRECTORY         Path to directory containing custom plugins
      --static MOUNT:DIRECTORY        Serve static files from this directory at
                                      /MOUNT/...
      --install TEXT                  Additional packages (e.g. plugins) to install
      --plugin-secret <TEXT TEXT TEXT>...
                                      Secrets to pass to plugins, e.g. --plugin-
                                      secret datasette-auth-github client_id xxx
      --version-note TEXT             Additional note to show on /-/versions
      --secret TEXT                   Secret used for signing secure values, such as
                                      signed cookies
      --title TEXT                    Title for metadata
      --license TEXT                  License label for metadata
      --license_url TEXT              License URL for metadata
      --source TEXT                   Source label for metadata
      --source_url TEXT               Source URL for metadata
      --about TEXT                    About label for metadata
      --about_url TEXT                About URL for metadata
      -n, --name TEXT                 Application name to use when deploying
      --tar TEXT                      --tar option to pass to Heroku, e.g.
                                      --tar=/usr/local/bin/gtar
      --generate-dir DIRECTORY        Output generated application files and stop
                                      without deploying
      --help                          Show this message and exit.


.. [[[end]]]

.. _cli_help_package___help:

datasette package
=================

Package SQLite files into a Datasette Docker container, see :ref:`cli_package`.

.. [[[cog
    help(["package", "--help"])
.. ]]]

::

    Usage: datasette package [OPTIONS] FILES...

      Package SQLite files into a Datasette Docker container

    Options:
      -t, --tag TEXT            Name for the resulting Docker container, can
                                optionally use name:tag format
      -m, --metadata FILENAME   Path to JSON/YAML file containing metadata to
                                publish
      --extra-options TEXT      Extra options to pass to datasette serve
      --branch TEXT             Install datasette from a GitHub branch e.g. main
      --template-dir DIRECTORY  Path to directory containing custom templates
      --plugins-dir DIRECTORY   Path to directory containing custom plugins
      --static MOUNT:DIRECTORY  Serve static files from this directory at /MOUNT/...
      --install TEXT            Additional packages (e.g. plugins) to install
      --spatialite              Enable SpatialLite extension
      --version-note TEXT       Additional note to show on /-/versions
      --secret TEXT             Secret used for signing secure values, such as
                                signed cookies
      -p, --port INTEGER RANGE  Port to run the server on, defaults to 8001
                                [1<=x<=65535]
      --title TEXT              Title for metadata
      --license TEXT            License label for metadata
      --license_url TEXT        License URL for metadata
      --source TEXT             Source label for metadata
      --source_url TEXT         Source URL for metadata
      --about TEXT              About label for metadata
      --about_url TEXT          About URL for metadata
      --help                    Show this message and exit.


.. [[[end]]]


.. _cli_help_inspect___help:

datasette inspect
=================

Outputs JSON representing introspected data about one or more SQLite database files.

If you are opening an immutable database, you can pass this file to the ``--inspect-data`` option to improve Datasette's performance by allowing it to skip running row counts against the database when it first starts running::

    datasette inspect mydatabase.db > inspect-data.json
    datasette serve -i mydatabase.db --inspect-file inspect-data.json

This performance optimization is used automatically by some of the ``datasette publish`` commands. You are unlikely to need to apply this optimization manually.

.. [[[cog
    help(["inspect", "--help"])
.. ]]]

::

    Usage: datasette inspect [OPTIONS] [FILES]...

      Generate JSON summary of provided database files

      This can then be passed to "datasette --inspect-file" to speed up count
      operations against immutable database files.

    Options:
      --inspect-file TEXT
      --load-extension PATH:ENTRYPOINT?
                                      Path to a SQLite extension to load, and
                                      optional entrypoint
      --help                          Show this message and exit.


.. [[[end]]]
