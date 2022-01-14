.. _cli_reference:

===============
 CLI reference
===============

This page lists the ``--help`` for every ``datasette`` CLI command.

.. [[[cog
    from datasette import cli
    from click.testing import CliRunner
    import textwrap
    commands = [
        ["--help"],
        ["serve", "--help"],
        ["serve", "--help-settings"],
        ["plugins", "--help"],
        ["publish", "--help"],
        ["publish", "cloudrun", "--help"],
        ["publish", "heroku", "--help"],
        ["package", "--help"],
        ["inspect", "--help"],
        ["install", "--help"],
        ["uninstall", "--help"],
    ]
    for command in commands:
        title = "datasette " + " ".join(command)
        cog.out(title + "\n")
        cog.out(("=" * len(title)) + "\n\n")
        cog.out("::\n\n")
        result = CliRunner().invoke(cli.cli, command)
        output = result.output.replace("Usage: cli ", "Usage: datasette ")
        cog.out(textwrap.indent(output, '    '))
        cog.out("\n\n")
.. ]]]
datasette --help
================

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
      package    Package specified SQLite files into a new datasette Docker...
      plugins    List currently installed plugins
      publish    Publish specified SQLite database files to the internet along...
      uninstall  Uninstall plugins and Python packages from the Datasette...


datasette serve --help
======================

::

    Usage: datasette serve [OPTIONS] [FILES]...

      Serve up specified SQLite database files with a web UI

    Options:
      -i, --immutable PATH      Database files to open in immutable mode
      -h, --host TEXT           Host for server. Defaults to 127.0.0.1 which means
                                only connections from the local machine will be
                                allowed. Use 0.0.0.0 to listen to all IPs and allow
                                access from other machines.
      -p, --port INTEGER RANGE  Port for server, defaults to 8001. Use -p 0 to
                                automatically assign an available port.
                                [0<=x<=65535]
      --uds TEXT                Bind to a Unix domain socket
      --reload                  Automatically reload if code or metadata change
                                detected - useful for development
      --cors                    Enable CORS by serving Access-Control-Allow-Origin:
                                *
      --load-extension TEXT     Path to a SQLite extension to load
      --inspect-file TEXT       Path to JSON file created using "datasette inspect"
      -m, --metadata FILENAME   Path to JSON/YAML file containing license/source
                                metadata
      --template-dir DIRECTORY  Path to directory containing custom templates
      --plugins-dir DIRECTORY   Path to directory containing custom plugins
      --static MOUNT:DIRECTORY  Serve static files from this directory at /MOUNT/...
      --memory                  Make /_memory database available
      --config CONFIG           Deprecated: set config option using
                                configname:value. Use --setting instead.
      --setting SETTING...      Setting, see docs.datasette.io/en/stable/config.html
      --secret TEXT             Secret used for signing secure values, such as
                                signed cookies
      --root                    Output URL that sets a cookie authenticating the
                                root user
      --get TEXT                Run an HTTP GET request against this path, print
                                results and exit
      --version-note TEXT       Additional note to show on /-/versions
      --help-settings           Show available settings
      --pdb                     Launch debugger on any errors
      -o, --open                Open Datasette in your web browser
      --create                  Create database files if they do not exist
      --crossdb                 Enable cross-database joins using the /_memory
                                database
      --ssl-keyfile TEXT        SSL key file
      --ssl-certfile TEXT       SSL certificate file
      --help                    Show this message and exit.


datasette serve --help-settings
===============================

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
      hash_urls                    Include DB file contents hash in URLs, for far-
                                   future caching (default=False)
      allow_facet                  Allow users to specify columns to facet using
                                   ?_facet= parameter (default=True)
      allow_download               Allow users to download the original SQLite
                                   database files (default=True)
      suggest_facets               Calculate and display suggested facets
                                   (default=True)
      default_cache_ttl            Default HTTP cache TTL (used in Cache-Control:
                                   max-age= header) (default=5)
      default_cache_ttl_hashed     Default HTTP cache TTL for hashed URL pages
                                   (default=31536000)
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



datasette plugins --help
========================

::

    Usage: datasette plugins [OPTIONS]

      List currently installed plugins

    Options:
      --all                    Include built-in default plugins
      --plugins-dir DIRECTORY  Path to directory containing custom plugins
      --help                   Show this message and exit.


datasette publish --help
========================

::

    Usage: datasette publish [OPTIONS] COMMAND [ARGS]...

      Publish specified SQLite database files to the internet along with a
      Datasette-powered interface and API

    Options:
      --help  Show this message and exit.

    Commands:
      cloudrun  Publish databases to Datasette running on Cloud Run
      heroku    Publish databases to Datasette running on Heroku


datasette publish cloudrun --help
=================================

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
      --apt-get-install TEXT          Additional packages to apt-get install
      --help                          Show this message and exit.


datasette publish heroku --help
===============================

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
      --help                          Show this message and exit.


datasette package --help
========================

::

    Usage: datasette package [OPTIONS] FILES...

      Package specified SQLite files into a new datasette Docker container

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


datasette inspect --help
========================

::

    Usage: datasette inspect [OPTIONS] [FILES]...

      Generate JSON summary of provided database files

      This can then be passed to "datasette --inspect-file" to speed up count
      operations against immutable database files.

    Options:
      --inspect-file TEXT
      --load-extension TEXT  Path to a SQLite extension to load
      --help                 Show this message and exit.


datasette install --help
========================

::

    Usage: datasette install [OPTIONS] PACKAGES...

      Install plugins and packages from PyPI into the same environment as Datasette

    Options:
      -U, --upgrade  Upgrade packages to latest version
      --help         Show this message and exit.


datasette uninstall --help
==========================

::

    Usage: datasette uninstall [OPTIONS] PACKAGES...

      Uninstall plugins and Python packages from the Datasette environment

    Options:
      -y, --yes  Don't ask for confirmation
      --help     Show this message and exit.


.. [[[end]]]
