Customizing Datasette's appearance
==================================

Datasette provides a number of ways of customizing the way data is displayed.

Custom CSS and JavaScript
-------------------------

When you launch Datasette, you can specify a custom metadata file like this::

    datasette mydb.db --metadata metadata.json

Your ``metadata.json`` file can include linke that look like this::

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
matches the content served. You can generate hashes using www.srihash.org

Every default template includes CSS classes in the body designed to support
custom styling.

The index template (the top level page at /) gets this::

    <body class="index">

The database template (/dbname/) gets this::

    <body class="db db-dbname">

The table template (/dbname/tablename) gets::

    <body class="table db-dbname table-tablename">

The row template (/dbname/tablename/rowid) gets::

    <body class="row db-dbname table-tablename">

The db-x and table-x classes use the database or table names themselves IF
they are valid CSS identifiers. If they aren't, we strip any invalid
characters out and append a 6 character md5 digest of the original name, in
order to ensure that multiple tables which resolve to the same stripped
character version still have different CSS classes.

Some examples:

    "simple" => "simple"
    "MixedCase" => "MixedCase"
    "-no-leading-hyphens" => "no-leading-hyphens-65bea6"
    "_no-leading-underscores" => "no-leading-underscores-b921bc"
    "no spaces" => "no-spaces-7088d7"
    "-" => "336d5e"
    "no $ characters" => "no--characters-59e024"


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

    Table page (/mydatabase/mytable):
        table-mydatabase-mytable.html
        table.html

    Row page (/mydatabase/mytable/id):
        row-mydatabase-mytable.html
        row.html

If a table name has spaces or other unexpected characters in it, the template
filename will follow the same rules as our custom ``<body>`` CSS classes - for
example, a table called "Food Trucks" will attempt to load the following
templates::

    table-mydatabase-Food-Trucks-399138.html
    table.html

It is possible to extend the default templates using Jinja template
inheritance. If you want to customize EVERY row template with some additional
content you can do so by creating a ``row.html`` template like this::

    {% extends "default:row.html" %}

    {% block content %}
    <h1>EXTRA HTML AT THE TOP OF THE CONTENT BLOCK</h1>
    <p>This line renders the original block:</p>
    {{ super() }}
    {% endblock %}

Note the ``default:row.html`` template name, which ensures Jinja will inherit from the
default template.
