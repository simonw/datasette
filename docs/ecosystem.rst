.. _ecosystem:

=======================
The Datasette Ecosystem
=======================

Datasette sits at the center of a growing ecosystem of open source tools aimed at making it as easy as possible to gather, analyze and publish interesting data.

These tools are divided into two main groups: tools for building SQLite databases (for use with Datasette) and plugins that extend Datasette's functionality.

Tools for creating SQLite databases
===================================

csvs-to-sqlite
--------------

`csvs-to-sqlite <https://github.com/simonw/csvs-to-sqlite>`__ lets you take one or more CSV files and load them into a SQLite database. It can also extract repeated columns out into a separate table and configure SQLite full-text search against the contents of specific columns.

sqlite-utils
------------

`sqlite-utils <https://github.com/simonw/sqlite-utils>`__ is a Python library and CLI tool that provides shortcuts for loading data into SQLite. It can be used programmatically (e.g. in a `Jupyter notebook <https://jupyter.org/>`__) to load data, and will automatically create SQLite tables with the necessary schema.

The CLI tool can consume JSON streams directly and use them to create tables. It can also be used to query SQLite databases and output the results as CSV or JSON.

See `sqlite-utils: a Python library and CLI tool for building SQLite databases <https://simonwillison.net/2019/Feb/25/sqlite-utils/>`__ for more.

db-to-sqlite
------------

`db-to-sqlite <https://github.com/simonw/db-to-sqlite>`__ is a CLI tool that builds on top of `SQLAlchemy <https://www.google.com/search?client=firefox-b-1-ab&q=sqlalchemy>`__ and allows you to connect to any database supported by that library (including MySQL, oracle and PostgreSQL), run a SQL query and save the results to a new table in a SQLite database. 

You can mirror an entire database (including copying foreign key relationships) with the ``--all`` option::

    $ db-to-sqlite --all "postgresql://simonw@localhost/myblog" blog.db

dbf-to-sqlite
-------------

`dbf-to-sqlite <https://github.com/simonw/dbf-to-sqlite>`__ works with `dBase files <https://en.wikipedia.org/wiki/.dbf>`__ such as those produced by Visual FoxPro. It is a command-line tool that can convert one or more ``.dbf`` file to tables in a SQLite database.

markdown-to-sqlite
------------------

`markdown-to-sqlite <https://github.com/simonw/markdown-to-sqlite>`__ reads Markdown files with embedded YAML metadata (e.g. for `Jekyll Front Matter <https://jekyllrb.com/docs/front-matter/>`__) and creates a SQLite table with a schema matching the metadata. This is useful if you want to keep structured data in text form in a GitHub repository and use that to build a SQLite database.

geojson-to-sqlite
-----------------

`geojson-to-sqlite <https://github.com/simonw/geojson-to-sqlite>`__ converts GeoJSON files to SQLite, optionally using SpatiaLite to create geospatial indexes for fast geometric queries.

shapefile-to-sqlite
-------------------

`shapefile-to-sqlite <https://github.com/simonw/shapefile-to-sqlite>`__ converts ESRI shapefiles to SQLite, optionally using SpatiaLite .

socrata2sql
-----------

`socrata2sql <https://github.com/DallasMorningNews/socrata2sql>`__ is a tool by Andrew Chavez at the Dallas Morning News. It works with Socrata, a widely used platform for local and national government open data portals. It uses the Socrata API to pull down government datasets and store them in a local SQLite database (it can also export data to PostgreSQL, MySQL and other SQLAlchemy-supported databases).

For example, to create a SQLite database of the `City of Dallas Payment Register <https://www.dallasopendata.com/Budget-Finance/City-of-Dallas-Payment-Register/64pp-jeba>`__ you would run the following command::

    $ socrata2sql insert www.dallasopendata.com 64pp-jeba

.. _ecosystem_plugins:

Datasette Plugins
=================

Datasette's :ref:`plugin system <plugins>` allows developers to enhance Datasette with additional functionality.

datasette-graphql
-----------------

`datasette-graphql <https://github.com/simonw/datasette-graphql>`__ provides a GraphQL interface for querying the data contained in your Datasette instance.

datasette-cluster-map
---------------------

`datasette-cluster-map <https://github.com/simonw/datasette-cluster-map>`__ is the original Datasette plugin, described in `Datasette plugins, and building a clustered map visualization <https://simonwillison.net/2018/Apr/20/datasette-plugins/>`__.

The plugin works against any table with latitude and longitude columns. It can load over 100,000 points onto a map to visualize the geographical distribution of the underlying data.

datasette-vega
--------------

`datasette-vega <https://github.com/simonw/datasette-vega>`__ exposes the powerful  `Vega <https://vega.github.io/vega/>`__ charting library, allowing you to construct line, bar and scatter charts against your data and share links to your visualizations.

datasette-auth-github
---------------------

`datasette-auth-github <https://github.com/simonw/datasette-auth-github>`__ adds an authentication layer to Datasette. Users will have to sign in using their GitHub account before they can view data or interact with Datasette. You can also use it to restrict access to specific GitHub users, or to members of specified GitHub `organizations <https://help.github.com/en/articles/about-organizations>`__ or `teams <https://help.github.com/en/articles/organizing-members-into-teams>`__.

datasette-auth-tokens
---------------------

`datasette-auth-tokens <https://tokens.com/simonw/datasette-auth-tokens>`__ provides a mechanism for creating secret API tokens that can then be used with Datasette's :ref:`authentication` system. These tokens can be hard-coded into the plugin configuration or the plugin can be configured to access tokens stored in a SQLite database table.

datasette-permissions-sql
-------------------------

`datasette-permissions-sql <https://tokens.com/simonw/datasette-permissions-sql>`__ lets you configure Datasette permissions checks to use custom SQL queries, which means you can make permisison decisions based on data contained within your databases.

datasette-upload-csvs
---------------------

`datasette-upload-csvs <https://github.com/simonw/datasette-upload-csvs>`__ allows users to upload CSV files directly into a Datasette instance through their web browser.

datasette-json-html
-------------------

`datasette-json-html <https://github.com/simonw/datasette-json-html>`__ renders HTML in Datasette's table view driven by JSON returned from your SQL queries. This provides a way to embed images, links and lists of links directly in Datasette's main interface, defined using custom SQL statements.

datasette-atom
--------------

`datasette-atom <https://github.com/simonw/datasette-atom>`__ can output Datasette query results as Atom feeds, suitable for subscribing to using a feed reader application.

datasette-ics
-------------

`datasette-ics <https://github.com/simonw/datasette-ics>`__ can output query results as an iCalendar feed, suitable for subscribing to from calendar software such as Google Calendar or Apple Calendar.

datasette-init
--------------

`datasette-init <https://github.com/simonw/datasette-init>`__ allows you to define tables and views in your metadata file that should be created on startup if they do not already exist.

datasette-write
---------------

`datasette-write <https://github.com/simonw/datasette-write>`__ provides an interface at ``/-/write`` allowing users to execute SQL write queries against a selected database.

datasette-media
---------------

`datasette-media <https://github.com/simonw/datasette-media>`__ adds the ability to serve media files such as images directly, configured through a SQL query that maps a URL parameter to a path to a file on disk. It can also serve resized image thumbnails.

datasette-jellyfish
-------------------

`datasette-jellyfish <https://github.com/simonw/datasette-jellyfish>`__ exposes custom SQL functions for a range of common fuzzy string matching functions, including soundex, porter stemming and levenshtein distance. It builds on top of the `Jellyfish Python library <https://jellyfish.readthedocs.io/>`__.

datasette-doublemetaphone
-------------------------

`datasette-doublemetaphone <https://github.com/dracos/datasette-doublemetaphone>`__ by Matthew Somerville adds custom SQL functions for applying the Double Metaphone fuzzy "sounds like" algorithm.

datasette-jq
------------

`datasette-jq <https://github.com/simonw/datasette-jq>`__ adds a custom SQL function for filtering and transforming values from JSON columns using the `jq <https://stedolan.github.io/jq/>`__ expression language.

datasette-rure
--------------

`datasette-rure <https://github.com/simonw/datasette-rure>`__ adds SQL support for matching values against regular expressions, built on top of `a Python binding <https://github.com/davidblewett/rure-python>`__ for the safe Rust regular expression library.

datasette-render-images
-----------------------

`datasette-render-images <https://github.com/simonw/datasette-render-images>`__ works with SQLite tables that contain binary image data in BLOB columns. It converts any images it finds into ``data-uri`` image elements, allowing you to view them directly in the Datasette interface.

datasette-render-binary
-----------------------

`datasette-render-binary <https://github.com/simonw/datasette-render-binary>`__ renders binary data in a slightly more readable fashion: it shows ASCII characters as they are, and shows all other data as monospace octets. Useful as a tool for exploring new unfamiliar databases as it makes it easier to spot if a binary column may contain a decipherable binary format.

datasette-render-markdown
-------------------------

`datasette-render-markdown <https://github.com/simonw/datasette-render-markdown>`__ adds tools for rendering Datasette rows that are formatted using Markdown.

datasette-render-html
---------------------

`datasette-render-html <https://github.com/simonw/datasette-render-html>`__ lets you configure columns that contain HTML from trusted sources such that the HTML is rendered correctly within the Datasette interface.

datasette-leaflet-geojson
-------------------------

`datasette-leaflet-geojson <https://github.com/simonw/datasette-leaflet-geojson>`__ looks out for columns containing GeoJSON formatted geographical information and displays them on a `Leaflet-powered <https://leafletjs.com/>`__ map.

datasette-pretty-json
---------------------

`datasette-pretty-json <https://github.com/simonw/datasette-pretty-json>`__ seeks out JSON values in Datasette's table browsing interface and pretty-prints them, making them easier to read.

datasette-saved-queries
-----------------------

`datasette-saved-queries <https://github.com/simonw/datasette-saved-queries>`__ lets users interactively save queries to a ``saved_queries`` table. They are then made available as additional :ref:`canned queries <canned_queries>`.

datasette-haversine
-------------------

`datasette-haversine <https://github.com/simonw/datasette-haversine>`__ provides a SQL ``haversine()`` function which can calculate the haversine distance between two geographical points. You can then sort by this distance to find records closest to a specified location.

::

    select haversine(lat1, lon1, lat2, lon2, 'mi');

datasette-sqlite-fts4
---------------------

`datasette-sqlite-fts4 <https://github.com/simonw/datasette-sqlite-fts4>`__ provides search relevance ranking algorithms that can be used with SQLite's FTS4 search module. You can read more about it in `Exploring search relevance algorithms with SQLite <https://simonwillison.net/2019/Jan/7/exploring-search-relevance-algorithms-sqlite/>`__.

datasette-bplist
----------------

`datasette-bplist <https://github.com/simonw/datasette-bplist>`__ provides tools for working with Apple's binary plist format embedded in SQLite database tables. If you use OS X you already have dozens of SQLite databases hidden away in your ``~/Library`` folder that include data in this format - this plugin allows you to view the decoded data and run SQL queries against embedded values using a ``bplist_to_json(value)`` custom SQL function.

datasette-cors
--------------

`datasette-cors <https://github.com/simonw/datasette-cors>`__ allows you to configure `CORS headers <https://developer.mozilla.org/en-US/docs/Web/HTTP/CORS>`__ for your Datasette instance. You can use this to enable JavaScript running on a whitelisted set of domains to make ``fetch()`` calls to the JSON API provided by your Datasette instance.

datasette-template-sql
----------------------

`datasette-template-sql <https://github.com/simonw/datasette-template-sql>`__ adds a custom template function that can be used to execute and loop through the results of SQL queries in your templates. See `this blog post <https://simonwillison.net/2019/Nov/18/datasette-template-sql/>`__ for background on the plugin.

datasette-mask-columns
----------------------

`datasette-mask-columns <https://github.com/simonw/datasette-mask-columns>`__ allows you to use ``metadata.json`` to configure specific table columns that should be masked - that should return null no matter what value is contained within the column. This is useful for things like hiding ``password`` columns from public display.

datasette-auth-existing-cookies
-------------------------------

`datasette-auth-existing-cookies <https://github.com/simonw/datasette-auth-existing-cookies>`__ allows you to configure Datasette to authenticate users based on existing cookies they may have for the current domain - useful for running Datasette on a subdomain of your main site, for example. See `this blog post <https://simonwillison.net/2020/Jan/29/weeknotes-datasette-cookies-sentry/>`__ for background on the plugin.

datasette-sentry
----------------

`datasette-sentry <https://github.com/simonw/datasette-sentry>`__ lets you configure Datasette to send any error reports to `Sentry <https://sentry.io/>`__.

datasette-publish-fly
---------------------

`datasette-publish-fly <https://github.com/simonw/datasette-publish-fly>`__ lets you publish Datasette instances using the `Fly <https://fly.io/>`__ hosting platform. See also :ref:`publish_fly`.
