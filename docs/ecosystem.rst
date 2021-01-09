.. _ecosystem:

=======================
The Datasette Ecosystem
=======================

Datasette sits at the center of a growing ecosystem of open source tools aimed at making it as easy as possible to gather, analyze and publish interesting data.

These tools are divided into two main groups: tools for building SQLite databases (for use with Datasette) and plugins that extend Datasette's functionality.

The `Datasette project website <https://datasette.io/>`__ includes a directory of plugins and a directory of tools:

- `Plugins directory on datasette.io <https://datasette.io/plugins>`__
- `Tools directory on datasette.io <https://datasette.io/tools>`__

sqlite-utils
============

`sqlite-utils <https://sqlite-utils.datasette.io/>`__ is a key building block for the wider Datasette ecosystem. It provides a collection of utilities for manipulating SQLite databases, both as a Python library and a command-line utility. Features include:

- Insert data into a SQLite database from JSON, CSV or TSV, automatically creating tables with the correct schema or altering existing tables to add missing columns.
- Configure tables for use with SQLite full-text search, including creating triggers needed to keep the search index up-to-date.
- Modify tables in ways that are not supported by SQLite's default ``ALTER TABLE`` syntax - for example changing the types of columns or selecting a new primary key for a table.
- Adding foreign keys to existing database tables.
- Extracting columns of data into a separate lookup table.

Dogsheep
========

`Dogsheep <https://dogsheep.github.io/>`__ is a collection of tools for personal analytics using SQLite and Datasette. The project provides tools like `github-to-sqlite <https://datasette.io/tools/github-to-sqlite>`__ and `twitter-to-sqlite <https://datasette.io/tools/twitter-to-sqlite>`__ that can import data from different sources in order to create a personal data warehouse. `Personal Data Warehouses: Reclaiming Your Data <https://simonwillison.net/2020/Nov/14/personal-data-warehouses/>`__ is a talk that explains Dogsheep and demonstrates it in action.

