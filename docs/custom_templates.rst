.. _customization:

Customization
=============

Datasette provides a number of ways of customizing the way data is displayed.

Custom CSS and JavaScript
-------------------------

When you launch Datasette, you can specify a custom metadata file like this::

    datasette mydb.db --metadata metadata.json

Your ``metadata.json`` file can include links that look like this::

    {
        "extra_css_urls": [
            "https://simonwillison.net/static/css/all.bf8cd891642c.css"
        ],
        "extra_js_urls": [
            "https://code.jquery.com/jquery-3.2.1.slim.min.js"
        ]
    }

The extra CSS and JavaScript files will be linked in the ``<head>`` of every page.

You can also specify a SRI (subresource integrity hash) for these assets::

    {
        "extra_css_urls": [
            {
                "url": "https://simonwillison.net/static/css/all.bf8cd891642c.css",
                "sri": "sha384-9qIZekWUyjCyDIf2YK1FRoKiPJq4PHt6tp/ulnuuyRBvazd0hG7pWbE99zvwSznI"
            }
        ],
        "extra_js_urls": [
            {
                "url": "https://code.jquery.com/jquery-3.2.1.slim.min.js",
                "sri": "sha256-k2WSCIexGzOj3Euiig+TlR8gA0EmPjuc79OEeY5L45g="
            }
        ]
    }

Modern browsers will only execute the stylesheet or JavaScript if the SRI hash
matches the content served. You can generate hashes using `www.srihash.org <https://www.srihash.org/>`_

CSS classes on the <body>
~~~~~~~~~~~~~~~~~~~~~~~~~

Every default template includes CSS classes in the body designed to support
custom styling.

The index template (the top level page at ``/``) gets this::

    <body class="index">

The database template (``/dbname``) gets this::

    <body class="db db-dbname">

The custom SQL template (``/dbname?sql=...``) gets this::

    <body class="query db-dbname">

A canned query template (``/dbname/queryname``) gets this::

    <body class="query db-dbname query-queryname">

The table template (``/dbname/tablename``) gets::

    <body class="table db-dbname table-tablename">

The row template (``/dbname/tablename/rowid``) gets::

    <body class="row db-dbname table-tablename">

The ``db-x`` and ``table-x`` classes use the database or table names themselves if
they are valid CSS identifiers. If they aren't, we strip any invalid
characters out and append a 6 character md5 digest of the original name, in
order to ensure that multiple tables which resolve to the same stripped
character version still have different CSS classes.

Some examples::

    "simple" => "simple"
    "MixedCase" => "MixedCase"
    "-no-leading-hyphens" => "no-leading-hyphens-65bea6"
    "_no-leading-underscores" => "no-leading-underscores-b921bc"
    "no spaces" => "no-spaces-7088d7"
    "-" => "336d5e"
    "no $ characters" => "no--characters-59e024"

``<td>`` and ``<th>`` elements also get custom CSS classes reflecting the
database column they are representing, for example::

    <table>
        <thead>
            <tr>
                <th class="col-id" scope="col">id</th>
                <th class="col-name" scope="col">name</th>
            </tr>
        </thead>
        <tbody>
            <tr>
                <td class="col-id"><a href="...">1</a></td>
                <td class="col-name">SMITH</td>
            </tr>
        </tbody>
    </table>

.. _customization_static_files:

Serving static files
~~~~~~~~~~~~~~~~~~~~

Datasette can serve static files for you, using the ``--static`` option.
Consider the following directory structure::

    metadata.json
    static/styles.css
    static/app.js

You can start Datasette using ``--static static:static/`` to serve those
files from the ``/static/`` mount point::

    $ datasette -m metadata.json --static static:static/ --memory

The following URLs will now serve the content from those CSS and JS files::

    http://localhost:8001/static/styles.css
    http://localhost:8001/static/app.js

You can reference those files from ``metadata.json`` like so::

    {
        "extra_css_urls": [
            "/static/styles.css"
        ],
        "extra_js_urls": [
            "/static/app.js"
        ]
    }

Publishing static assets
~~~~~~~~~~~~~~~~~~~~~~~~

The :ref:`cli_publish` command can be used to publish your static assets,
using the same syntax as above::

    $ datasette publish cloudrun mydb.db --static static:static/

This will upload the contents of the ``static/`` directory as part of the
deployment, and configure Datasette to correctly serve the assets.

.. _customization_custom_templates:

Custom templates
----------------

By default, Datasette uses default templates that ship with the package.

You can over-ride these templates by specifying a custom ``--template-dir`` like
this::

    datasette mydb.db --template-dir=mytemplates/

Datasette will now first look for templates in that directory, and fall back on
the defaults if no matches are found.

It is also possible to over-ride templates on a per-database, per-row or per-
table basis.

The lookup rules Datasette uses are as follows::

    Index page (/):
        index.html

    Database page (/mydatabase):
        database-mydatabase.html
        database.html

    Custom query page (/mydatabase?sql=...):
        query-mydatabase.html
        query.html

    Canned query page (/mydatabase/canned-query):
        query-mydatabase-canned-query.html
        query-mydatabase.html
        query.html

    Table page (/mydatabase/mytable):
        table-mydatabase-mytable.html
        table.html

    Row page (/mydatabase/mytable/id):
        row-mydatabase-mytable.html
        row.html

    Table of rows and columns include on table page:
        _table-table-mydatabase-mytable.html
        _table-mydatabase-mytable.html
        _table.html

    Table of rows and columns include on row page:
        _table-row-mydatabase-mytable.html
        _table-mydatabase-mytable.html
        _table.html

If a table name has spaces or other unexpected characters in it, the template
filename will follow the same rules as our custom ``<body>`` CSS classes - for
example, a table called "Food Trucks" will attempt to load the following
templates::

    table-mydatabase-Food-Trucks-399138.html
    table.html

You can find out which templates were considered for a specific page by viewing
source on that page and looking for an HTML comment at the bottom. The comment
will look something like this::

    <!-- Templates considered: *query-mydb-tz.html, query-mydb.html, query.html -->

This example is from the canned query page for a query called "tz" in the
database called "mydb". The asterisk shows which template was selected - so in
this case, Datasette found a template file called ``query-mydb-tz.html`` and
used that - but if that template had not been found, it would have tried for
``query-mydb.html`` or the default ``query.html``.

It is possible to extend the default templates using Jinja template
inheritance. If you want to customize EVERY row template with some additional
content you can do so by creating a ``row.html`` template like this::

    {% extends "default:row.html" %}

    {% block content %}
    <h1>EXTRA HTML AT THE TOP OF THE CONTENT BLOCK</h1>
    <p>This line renders the original block:</p>
    {{ super() }}
    {% endblock %}

Note the ``default:row.html`` template name, which ensures Jinja will inherit
from the default template.

The ``_table.html`` template is included by both the row and the table pages,
and a list of rows. The default ``_table.html`` template renders them as an
HTML template and `can be seen here <https://github.com/simonw/datasette/blob/master/datasette/templates/_table.html>`_.

You can provide a custom template that applies to all of your databases and
tables, or you can provide custom templates for specific tables using the
template naming scheme described above.

If you want to present your data in a format other than an HTML table, you
can do so by looping through ``display_rows`` in your own ``_table.html``
template. You can use ``{{ row["column_name"] }}`` to output the raw value
of a specific column.

If you want to output the rendered HTML version of a column, including any
links to foreign keys, you can use ``{{ row.display("column_name") }}``.

Here is an example of a custom ``_table.html`` template::

    {% for row in display_rows %}
        <div>
            <h2>{{ row["title"] }}</h2>
            <p>{{ row["description"] }}<lp>
            <p>Category: {{ row.display("category_id") }}</p>
        </div>
    {% endfor %}

.. _custom_pages:

Custom pages
------------

You can add templated pages to your Datasette instance by creating HTML files in a ``pages`` directory within your ``templates`` directory.

For example, to add a custom page that is served at ``http://localhost/about`` you would create a file in ``templates/pages/about.html``, then start Datasette like this::

    $ datasette mydb.db --template-dir=templates/

You can nest directories within pages to create a nested structure. To create a ``http://localhost:8001/about/map`` page you would create ``templates/pages/about/map.html``.

.. _custom_pages_parameters:

Path parameters for pages
~~~~~~~~~~~~~~~~~~~~~~~~~

You can define custom pages that match multiple paths by creating files with ``{variable}`` definitions in their filenames.

For example, to capture any request to a URL matching ``/about/*``, you would create a template in the following location::

    templates/pages/about/{slug}.html

A hit to ``/about/news`` would render that template and pass in a variable called ``slug`` with a value of ``"news"``.

If you use this mechanism don't forget to return a 404 status code if the page should not be considered a valid page. You can do this using ``{{ custom_status(404) }}`` described below.

Templates defined using custom page routes work particularly well with the ``sql()`` template function from `datasette-template-sql <https://github.com/simonw/datasette-template-sql>`__ or the ``graphql()`` template function from `datasette-graphql <https://github.com/simonw/datasette-graphql#the-graphql-template-function>`__.

.. _custom_pages_headers:

Custom headers and status codes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Custom pages default to being served with a content-type of ``text/html; charset=utf-8`` and a ``200`` status code. You can change these by calling a custom function from within your template.

For example, to serve a custom page with a ``418 I'm a teapot`` HTTP status code, create a file in ``pages/teapot.html`` containing the following::

    {{ custom_status(418) }}
    <html>
    <head><title>Teapot</title></head>
    <body>
    I'm a teapot
    </body>
    </html>

To serve a custom HTTP header, add a ``custom_header(name, value)`` function call. For example::

    {{ custom_status(418) }}
    {{ custom_header("x-teapot", "I am") }}
    <html>
    <head><title>Teapot</title></head>
    <body>
    I'm a teapot
    </body>
    </html>

You can verify this is working using ``curl`` like this::

    $ curl -I 'http://127.0.0.1:8001/teapot'
    HTTP/1.1 418
    date: Sun, 26 Apr 2020 18:38:30 GMT
    server: uvicorn
    x-teapot: I am
    content-type: text/html; charset=utf-8

.. _custom_pages_redirects:

Custom redirects
~~~~~~~~~~~~~~~~

You can use the ``custom_redirect(location)`` function to redirect users to another page, for example in a file called ``pages/datasette.html``::

    {{ custom_redirect("https://github.com/simonw/datasette") }}

Now requests to ``http://localhost:8001/datasette`` will result in a redirect.

These redirects are served with a ``301 Found`` status code by default. You can send a ``301 Moved Permanently`` code by passing ``301`` as the second argument to the function::

    {{ custom_redirect("https://github.com/simonw/datasette", 301) }}
