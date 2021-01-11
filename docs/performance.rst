.. _performance:

Performance and caching
=======================

Datasette runs on top of SQLite, and SQLite has excellent performance.  For small databases almost any query should return in just a few milliseconds, and larger databases (100s of MBs or even GBs of data) should perform extremely well provided your queries make sensible use of database indexes.

That said, there are a number of tricks you can use to improve Datasette's performance.

.. _performance_immutable_mode:

Immutable mode
--------------

If you can be certain that a SQLite database file will not be changed by another process you can tell Datasette to open that file in *immutable mode*.

Doing so will disable all locking and change detection, which can result in improved query performance.

This also enables further optimizations relating to HTTP caching, described below.

To open a file in immutable mode pass it to the datasette command using the ``-i`` option::

    datasette -i data.db

When you open a file in immutable mode like this Datasette will also calculate and cache the row counts for each table in that database when it first starts up, further improving performance.

Using "datasette inspect"
-------------------------

Counting the rows in a table can be a very expensive operation on larger databases. In immutable mode Datasette performs this count only once and caches the results, but this can still cause server startup time to increase by several seconds or more.

If you know that a database is never going to change you can precalculate the table row counts once and store then in a JSON file, then use that file when you later start the server.

To create a JSON file containing the calculated row counts for a database, use the following::

    datasette inspect data.db --inspect-file=counts.json

Then later you can start Datasette against the ``counts.json`` file and use it to skip the row counting step and speed up server startup::

    datasette -i data.db --inspect-file=counts.json

You need to use the ``-i`` immutable mode against the databse file here or the counts from the JSON file will be ignored.

You will rarely need to use this optimization in every-day use, but several of the ``datasette publish`` commands described in :ref:`publishing` use this optimization for better performance when deploying a database file to a hosting provider.

HTTP caching
------------

If your database is immutable and guaranteed not to change, you can gain major performance improvements from Datasette by enabling HTTP caching.

This can work at two different levels. First, it can tell browsers to cache the results of queries and serve future requests from the browser cache.

More significantly, it allows you to run Datasette behind a caching proxy such as `Varnish <https://varnish-cache.org/>`__ or use a cache provided by a hosted service such as `Fastly <https://www.fastly.com/>`__ or `Cloudflare <https://www.cloudflare.com/>`__. This can provide incredible speed-ups since a query only needs to be executed by Datasette the first time it is accessed - all subsequent hits can then be served by the cache.

Using a caching proxy in this way could enable a Datasette-backed visualization to serve thousands of hits a second while running Datasette itself on extremely inexpensive hosting.

Datasette's integration with HTTP caches can be enabled using a combination of configuration options and query string arguments.

The :ref:`setting_default_cache_ttl` setting sets the default HTTP cache TTL for all Datasette pages. This is 5 seconds unless you change it - you can set it to 0 if you wish to disable HTTP caching entirely.

You can also change the cache timeout on a per-request basis using the ``?_ttl=10`` query string parameter. This can be useful when you are working with the Datasette JSON API - you may decide that a specific query can be cached for a longer time, or maybe you need to set ``?_ttl=0`` for some requests for example if you are running a SQL ``order by random()`` query.

Hashed URL mode
---------------

When you open a database file in immutable mode using the ``-i`` option, Datasette calculates a SHA-256 hash of the contents of that file on startup. This content hash can then optionally be used to create URLs that are guaranteed to change if the contents of the file changes in the future. This results in URLs that can then be cached indefinitely by both browsers and caching proxies - an enormous potential performance optimization.

You can enable these hashed URLs in two ways: using the :ref:`setting_hash_urls` configuration setting (which affects all requests to Datasette) or via the ``?_hash=1`` query string parameter (which only applies to the current request).

With hashed URLs enabled, any request to e.g. ``/mydatabase/mytable`` will 302 redirect to ``mydatabase-455fe3a/mytable``. The URL containing the hash will be served with a very long cache expire header - configured using :ref:`setting_default_cache_ttl_hashed` which defaults to 365 days.

Since these responses are cached for a long time, you may wish to build API clients against the non-hashed version of these URLs. These 302 redirects are served extremely quickly, so this should still be a performant way to work against the Datasette API.

If you run Datasette behind an `HTTP/2 server push <https://en.wikipedia.org/wiki/HTTP/2_Server_Push>`__ aware proxy such as Cloudflare Datasette will serve the 302 redirects in such a way that the redirected page will be efficiently "pushed" to the browser as part of the response, without the browser needing to make a second HTTP request to fetch the redirected resource.

.. note::
    Prior to Datasette 0.28 hashed URL mode was the default behaviour for Datasette, since all database files were assumed to be immutable and unchanging. From 0.28 onwards the default has been to treat database files as mutable unless explicitly configured otherwise.
