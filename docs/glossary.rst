.. _glossary:

Glossary
========

Datasette
    Datasette is a tool for exploring, analyzing, and publishing data as an interactive website.

Datasette Lite
    Datasette Lite is a browser-based version of Datasette that runs using WebAssembly (via Pyodide). It lets you query and explore SQLite databases without installing Python or running a server.

Datasette Desktop
    Datasette Desktop is a macOS application that bundles Datasette with Python. It provides a graphical way to run Datasette locally without using the command line.

Dogsheep
    Dogsheep is a collection of tools for personal analytics using SQLite and Datasette. It provides commands like github-to-sqlite and twitter-to-sqlite that import data from services like GitHub, Twitter, and Apple Health into SQLite databases that can be explored with Datasette.

extensions
    Extensions are compiled libraries (such as SpatiaLite) that add new capabilities to the SQLite database engine itself. They are loaded using the --load-extension option. Unlike plugins, which extend Datasette's web interface using Python, extensions modify the database engine directly.

facet
    A facet is a data exploration feature that shows the most common values in a database column, letting users filter data by clicking those values. Facets appear automatically or can be configured in metadata.json.

metadata
    Metadata in Datasette is an external JSON or YAML file that provides context about your databases and tables. It can specify titles, descriptions, source, and licensing, which Datasette displays in the web interface.

plugins
    Plugins are Python packages (and sometimes JavaScript) that extend Datasette's features. They can add visualizations, custom SQL functions, or new output formats. Plugins are installed using the datasette install command.

SpatiaLite
    SpatiaLite is a SQLite extension that adds support for geographic and spatial data. It enables Datasette to store, query, and analyze location-based data using specialized SQL functions. It is loaded using the --load-extension option.

sqlite-utils
    sqlite-utils is a Python library and command-line tool for manipulating SQLite databases. It can create databases, insert data from CSV or JSON, and run queries. It is designed as a companion tool to Datasette.