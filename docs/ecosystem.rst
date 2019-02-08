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

db-to-sqlite
------------

`db-to-sqlite <https://github.com/simonw/db-to-sqlite>`__ is a CLI tool that builds on top of `SQLAlchemy <https://www.google.com/search?client=firefox-b-1-ab&q=sqlalchemy>`__ and allows you to connect to any database supported by that library (including MySQL, oracle and PostgreSQL), run a SQL query and save the results to a new table in a SQLite database. 

You can mirror an entire database (including copying foreign key relationships) with the ``--all`` option::

    $ db-to-sqlite --connection="postgresql://simonw@localhost/myblog" --all blog.db

dbf-to-sqlite
-------------

`dbf-to-sqlite <https://github.com/simonw/dbf-to-sqlite>`__ works with `dBase files <https://en.wikipedia.org/wiki/.dbf>`__ such as those produced by Visual FoxPro. It is a command-line tool that can convert one or more ``.dbf`` file to tables in a SQLite database.

markdown-to-sqlite
------------------

`markdown-to-sqlite <https://github.com/simonw/markdown-to-sqlite>`__ reads Markdown files with embedded YAML metadata (e.g. for `Jekyll Front Matter <https://jekyllrb.com/docs/front-matter/>`__) and creates a SQLite table with a schema matching the metadata. This is useful if you want to keep structured data in text form in a GitHub repository and use that to build a SQLite database.

socrata2sql
-----------

`socrata2sql <https://github.com/DallasMorningNews/socrata2sql>`__ is a tool by Andrew Chavez at the Dallas Morning News. It works with Socrata, a widely used platform for local and national government open data portals. It uses the Socrata API to pull down government datasets and store them in a local SQLite database (it can also export data to PostgreSQL, MySQL and other SQLAlchemy-supported databases).

For example, to create a SQLite database of the `City of Dallas Payment Register <https://www.dallasopendata.com/Budget-Finance/City-of-Dallas-Payment-Register/64pp-jeba>`__ you would run the following command::

    $ socrata2sql insert www.dallasopendata.com 64pp-jeba

Datasette Plugins
=================

Datasette's :ref:`plugin system <plugins>` makes it easy to enhance Datasette with additional functionality.

datasette-cluster-map
---------------------

`datasette-cluster-map <https://github.com/simonw/datasette-cluster-map>`__ is the original Datasette plugin, described in `Datasette plugins, and building a clustered map visualization <https://simonwillison.net/2018/Apr/20/datasette-plugins/>`__.

The plugin works against any table with latitude and longitude columns. It can load over 100,000 points onto a map to visualize the geographical distribution of the underlying data.

datasette-vega
--------------

`datasette-vega <https://github.com/simonw/datasette-vega>`__ exposes the powerful  `Vega <https://vega.github.io/vega/>`__ charting library, allowing you to construct line, bar and scatter charts against your data and share links to your visualizations.

datasette-json-html
-------------------

`datasette-json-html <https://github.com/simonw/datasette-json-html>`__ renders HTML in Datasette's table view driven by JSON returned from your SQL queries. This provides a way to embed images, links and lists of links directly in Datasette's main interface, defined using custom SQL statements.

datasette-render-images
-----------------------

`datasette-render-images <https://github.com/simonw/datasette-render-images>`__ works with SQLite tables that contain binary image data in BLOB columns. It converts any images it finds into ``data-uri`` image elements, allowing you to view them directly in the Datasette interface.

datasette-pretty-json
---------------------

`datasette-pretty-json <https://github.com/simonw/datasette-pretty-json>`__ seeks out JSON values in Datasette's table browsing interface and pretty-prints them, making them easier to read.

datasette-sqlite-fts4
---------------------

`datasette-sqlite-fts4 <https://github.com/simonw/datasette-sqlite-fts4>`__ provides search relevance ranking algorithms that can be used with SQLite's FTS4 search module. You can read more about it in `Exploring search relevance algorithms with SQLite <https://simonwillison.net/2019/Jan/7/exploring-search-relevance-algorithms-sqlite/>`__.
