.. _settings:

Settings
========

Using \-\-setting
-----------------

Datasette supports a number of settings. These can be set using the ``--setting name value`` option to ``datasette serve``.

You can set multiple settings at once like this::

    datasette mydatabase.db \
        --setting default_page_size 50 \
        --setting sql_time_limit_ms 3500 \
        --setting max_returned_rows 2000

.. _config_dir:

Configuration directory mode
----------------------------

Normally you configure Datasette using command-line options. For a Datasette instance with custom templates, custom plugins, a static directory and several databases this can get quite verbose::

    $ datasette one.db two.db \
        --metadata=metadata.json \
        --template-dir=templates/ \
        --plugins-dir=plugins \
        --static css:css

As an alternative to this, you can run Datasette in *configuration directory* mode. Create a directory with the following structure::

    # In a directory called my-app:
    my-app/one.db
    my-app/two.db
    my-app/metadata.json
    my-app/templates/index.html
    my-app/plugins/my_plugin.py
    my-app/static/my.css

Now start Datasette by providing the path to that directory::

    $ datasette my-app/

Datasette will detect the files in that directory and automatically configure itself using them. It will serve all ``*.db`` files that it finds, will load ``metadata.json`` if it exists, and will load the ``templates``, ``plugins`` and ``static`` folders if they are present.

The files that can be included in this directory are as follows. All are optional.

* ``*.db`` (or ``*.sqlite3`` or ``*.sqlite``) - SQLite database files that will be served by Datasette
* ``metadata.json`` - :ref:`metadata` for those databases - ``metadata.yaml`` or ``metadata.yml`` can be used as well
* ``inspect-data.json`` - the result of running ``datasette inspect *.db --inspect-file=inspect-data.json`` from the configuration directory - any database files listed here will be treated as immutable, so they should not be changed while Datasette is running
* ``settings.json`` - settings that would normally be passed using ``--setting`` - here they should be stored as a JSON object of key/value pairs
* ``templates/`` - a directory containing :ref:`customization_custom_templates`
* ``plugins/`` - a directory containing plugins, see :ref:`writing_plugins_one_off`
* ``static/`` - a directory containing static files - these will be served from ``/static/filename.txt``, see :ref:`customization_static_files`

Settings
--------

The following options can be set using ``--setting name value``, or by storing them in the ``settings.json`` file for use with :ref:`config_dir`.

.. _setting_default_allow_sql:

default_allow_sql
~~~~~~~~~~~~~~~~~

Should users be able to execute arbitrary SQL queries by default?

Setting this to ``off`` causes permission checks for :ref:`permissions_execute_sql` to fail by default.

::

    datasette mydatabase.db --setting default_allow_sql off

There are two ways to achieve this: the other is to add ``"allow_sql": false`` to your ``metadata.json`` file, as described in :ref:`authentication_permissions_execute_sql`. This setting offers a more convenient way to do this.

.. _setting_default_page_size:

default_page_size
~~~~~~~~~~~~~~~~~

The default number of rows returned by the table page. You can over-ride this on a per-page basis using the ``?_size=80`` query string parameter, provided you do not specify a value higher than the ``max_returned_rows`` setting. You can set this default using ``--setting`` like so::

    datasette mydatabase.db --setting default_page_size 50

.. _setting_sql_time_limit_ms:

sql_time_limit_ms
~~~~~~~~~~~~~~~~~

By default, queries have a time limit of one second. If a query takes longer than this to run Datasette will terminate the query and return an error.

If this time limit is too short for you, you can customize it using the ``sql_time_limit_ms`` limit - for example, to increase it to 3.5 seconds::

    datasette mydatabase.db --setting sql_time_limit_ms 3500

You can optionally set a lower time limit for an individual query using the ``?_timelimit=100`` query string argument::

    /my-database/my-table?qSpecies=44&_timelimit=100

This would set the time limit to 100ms for that specific query. This feature is useful if you are working with databases of unknown size and complexity - a query that might make perfect sense for a smaller table could take too long to execute on a table with millions of rows. By setting custom time limits you can execute queries "optimistically" - e.g. give me an exact count of rows matching this query but only if it takes less than 100ms to calculate.

.. _setting_max_returned_rows:

max_returned_rows
~~~~~~~~~~~~~~~~~

Datasette returns a maximum of 1,000 rows of data at a time. If you execute a query that returns more than 1,000 rows, Datasette will return the first 1,000 and include a warning that the result set has been truncated. You can use OFFSET/LIMIT or other methods in your SQL to implement pagination if you need to return more than 1,000 rows.

You can increase or decrease this limit like so::

    datasette mydatabase.db --setting max_returned_rows 2000

.. _setting_num_sql_threads:

num_sql_threads
~~~~~~~~~~~~~~~

Maximum number of threads in the thread pool Datasette uses to execute SQLite queries. Defaults to 3.

::

    datasette mydatabase.db --setting num_sql_threads 10

Setting this to 0 turns off threaded SQL queries entirely - useful for environments that do not support threading such as `Pyodide <https://pyodide.org/>`__.

.. _setting_allow_facet:

allow_facet
~~~~~~~~~~~

Allow users to specify columns they would like to facet on using the ``?_facet=COLNAME`` URL parameter to the table view.

This is enabled by default. If disabled, facets will still be displayed if they have been specifically enabled in ``metadata.json`` configuration for the table.

Here's how to disable this feature::

    datasette mydatabase.db --setting allow_facet off

.. _setting_default_facet_size:

default_facet_size
~~~~~~~~~~~~~~~~~~

The default number of unique rows returned by :ref:`facets` is 30. You can customize it like this::

    datasette mydatabase.db --setting default_facet_size 50

.. _setting_facet_time_limit_ms:

facet_time_limit_ms
~~~~~~~~~~~~~~~~~~~

This is the time limit Datasette allows for calculating a facet, which defaults to 200ms::

    datasette mydatabase.db --setting facet_time_limit_ms 1000

.. _setting_facet_suggest_time_limit_ms:

facet_suggest_time_limit_ms
~~~~~~~~~~~~~~~~~~~~~~~~~~~

When Datasette calculates suggested facets it needs to run a SQL query for every column in your table. The default for this time limit is 50ms to account for the fact that it needs to run once for every column. If the time limit is exceeded the column will not be suggested as a facet.

You can increase this time limit like so::

    datasette mydatabase.db --setting facet_suggest_time_limit_ms 500

.. _setting_suggest_facets:

suggest_facets
~~~~~~~~~~~~~~

Should Datasette calculate suggested facets? On by default, turn this off like so::

    datasette mydatabase.db --setting suggest_facets off

.. _setting_allow_download:

allow_download
~~~~~~~~~~~~~~

Should users be able to download the original SQLite database using a link on the database index page? This is turned on by default. However, databases can only be downloaded if they are served in immutable mode and not in-memory. If downloading is unavailable for either of these reasons, the download link is hidden even if ``allow_download`` is on. To disable database downloads, use the following::

    datasette mydatabase.db --setting allow_download off

.. _setting_default_cache_ttl:

default_cache_ttl
~~~~~~~~~~~~~~~~~

Default HTTP caching max-age header in seconds, used for ``Cache-Control: max-age=X``. Can be over-ridden on a per-request basis using the ``?_ttl=`` query string parameter. Set this to ``0`` to disable HTTP caching entirely. Defaults to 5 seconds.

::

    datasette mydatabase.db --setting default_cache_ttl 60

.. _setting_cache_size_kb:

cache_size_kb
~~~~~~~~~~~~~

Sets the amount of memory SQLite uses for its `per-connection cache <https://www.sqlite.org/pragma.html#pragma_cache_size>`_, in KB.

::

    datasette mydatabase.db --setting cache_size_kb 5000

.. _setting_allow_csv_stream:

allow_csv_stream
~~~~~~~~~~~~~~~~

Enables :ref:`the CSV export feature <csv_export>` where an entire table
(potentially hundreds of thousands of rows) can be exported as a single CSV
file. This is turned on by default - you can turn it off like this:

::

    datasette mydatabase.db --setting allow_csv_stream off

.. _setting_max_csv_mb:

max_csv_mb
~~~~~~~~~~

The maximum size of CSV that can be exported, in megabytes. Defaults to 100MB.
You can disable the limit entirely by settings this to 0:

::

    datasette mydatabase.db --setting max_csv_mb 0

.. _setting_truncate_cells_html:

truncate_cells_html
~~~~~~~~~~~~~~~~~~~

In the HTML table view, truncate any strings that are longer than this value.
The full value will still be available in CSV, JSON and on the individual row
HTML page. Set this to 0 to disable truncation.

::

    datasette mydatabase.db --setting truncate_cells_html 0

.. _setting_force_https_urls:

force_https_urls
~~~~~~~~~~~~~~~~

Forces self-referential URLs in the JSON output to always use the ``https://``
protocol. This is useful for cases where the application itself is hosted using
HTTP but is served to the outside world via a proxy that enables HTTPS.

::

    datasette mydatabase.db --setting force_https_urls 1

.. _setting_template_debug:

template_debug
~~~~~~~~~~~~~~

This setting enables template context debug mode, which is useful to help understand what variables are available to custom templates when you are writing them.

Enable it like this::

    datasette mydatabase.db --setting template_debug 1

Now you can add ``?_context=1`` or ``&_context=1`` to any Datasette page to see the context that was passed to that template.

Some examples:

* https://latest.datasette.io/?_context=1
* https://latest.datasette.io/fixtures?_context=1
* https://latest.datasette.io/fixtures/roadside_attractions?_context=1

.. _setting_trace_debug:

trace_debug
~~~~~~~~~~~

This setting enables appending ``?_trace=1`` to any page in order to see the SQL queries and other trace information that was used to generate that page.

Enable it like this::

    datasette mydatabase.db --setting trace_debug 1

Some examples:

* https://latest.datasette.io/?_trace=1
* https://latest.datasette.io/fixtures/roadside_attractions?_trace=1

See :ref:`internals_tracer` for details on how to hook into this mechanism as a plugin author.

.. _setting_base_url:

base_url
~~~~~~~~

If you are running Datasette behind a proxy, it may be useful to change the root path used for the Datasette instance.

For example, if you are sending traffic from ``https://www.example.com/tools/datasette/`` through to a proxied Datasette instance you may wish Datasette to use ``/tools/datasette/`` as its root URL.

You can do that like so::

    datasette mydatabase.db --setting base_url /tools/datasette/

.. _setting_secret:

Configuring the secret
----------------------

Datasette uses a secret string to sign secure values such as cookies.

If you do not provide a secret, Datasette will create one when it starts up. This secret will reset every time the Datasette server restarts though, so things like authentication cookies will not stay valid between restarts.

You can pass a secret to Datasette in two ways: with the ``--secret`` command-line option or by setting a ``DATASETTE_SECRET`` environment variable.

::

    $ datasette mydb.db --secret=SECRET_VALUE_HERE

Or::

    $ export DATASETTE_SECRET=SECRET_VALUE_HERE
    $ datasette mydb.db

One way to generate a secure random secret is to use Python like this::

    $ python3 -c 'import secrets; print(secrets.token_hex(32))'
    cdb19e94283a20f9d42cca50c5a4871c0aa07392db308755d60a1a5b9bb0fa52

Plugin authors make use of this signing mechanism in their plugins using :ref:`datasette_sign` and :ref:`datasette_unsign`.

.. _setting_publish_secrets:

Using secrets with datasette publish
------------------------------------

The :ref:`cli_publish` and :ref:`cli_package` commands both generate a secret for you automatically when Datasette is deployed.

This means that every time you deploy a new version of a Datasette project, a new secret will be generated. This will cause signed cookies to become invalid on every fresh deploy.

You can fix this by creating a secret that will be used for multiple deploys and passing it using the ``--secret`` option::

    datasette publish cloudrun mydb.db --service=my-service --secret=cdb19e94283a20f9d42cca5
