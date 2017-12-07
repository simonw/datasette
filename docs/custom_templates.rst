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

The database template (/dbname) gets this::

    <body class="db db-dbname">

The custom SQL template (/dbname?sql=...) gets this::

    <body class="query db-dbname">

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

    Custom query page (/mydatabase?sql=...):
        query-mydatabase.html
        query.html

    Table page (/mydatabase/mytable):
        table-mydatabase-mytable.html
        table.html

    Row page (/mydatabase/mytable/id):
        row-mydatabase-mytable.html
        row.html

    Rows and columns include on table page:
        _rows_and_columns-table-mydatabase-mytable.html
        _rows_and_columns-mydatabase-mytable.html
        _rows_and_columns.html

    Rows and columns include on row page:
        _rows_and_columns-row-mydatabase-mytable.html
        _rows_and_columns-mydatabase-mytable.html
        _rows_and_columns.html

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

Note the ``default:row.html`` template name, which ensures Jinja will inherit
from the default template.

The `_rows_and_columns.html` template is included on both the row and the table
page, and displays the content of the row. The default template looks like this::

    <table>
        <thead>
            <tr>
                {% for column in display_columns %}
                    <th scope="col">{{ column }}</th>
                {% endfor %}
            </tr>
        </thead>
        <tbody>
        {% for row in display_rows %}
            <tr>
                {% for cell in row %}
                    <td>{{ cell.value }}</td>
                {% endfor %}
            </tr>
        {% endfor %}
        </tbody>
    </table>

You can provide a custom template that applies to all of your databases and
tables, or you can provide custom templates for specific tables using the
template naming scheme described above.

Say for example you want to output a certain column as unescaped HTML. You could
provide a custom ``_rows_and_columns.html`` template like this::

    <table>
        <thead>
            <tr>
                {% for column in display_columns %}
                    <th scope="col">{{ column }}</th>
                {% endfor %}
            </tr>
        </thead>
        <tbody>
        {% for row in display_rows %}
            <tr>
                {% for cell in row %}
                    <td>
                        {% if cell.column == 'description' %}
                            !!{{ cell.value|safe }}
                        {% else %}
                            {{ cell.value }}
                        {% endif %}
                    </td>
                {% endfor %}
            </tr>
        {% endfor %}
        </tbody>
    </table>
