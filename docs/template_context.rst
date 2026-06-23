.. _template_context:

Template context
================

This page documents the variables that are available to custom templates
for each of Datasette's core pages. See :ref:`customization_custom_templates`
for how to provide your own templates.

The variables documented here are a stable contract: custom templates that
use them will continue to work across Datasette releases, up until the next
major version (Datasette 2.0). Anything present in the template context but
not documented on this page is not part of that contract and may change or
be removed in any release.

You can inspect the full context for any page by starting Datasette with
``--setting template_debug 1`` and adding ``?_context=1`` to the page URL.

.. [[[cog
    from template_context_doc import template_context
    template_context(cog)
.. ]]]

Base context
------------

These variables are available on every page rendered by Datasette, including pages rendered by plugins that use :ref:`datasette.render_template() <datasette_render_template>`. Plugins can add additional variables using the :ref:`plugin_hook_extra_template_vars` hook.

``request``
    The current :ref:`Request object <internals_request>`, or None. Common properties include ``request.path``, ``request.args``, ``request.actor``, ``request.url_vars`` and ``request.host``.

``crumb_items``
    Async function returning breadcrumb navigation items for the current page. Call it with ``request=request`` plus optional ``database=`` and ``table=`` arguments; it returns a list of ``{"href": url, "label": label}`` dictionaries.

``urls``
    Object with methods for constructing URLs within Datasette. Common methods include ``urls.instance()``, ``urls.database(database)``, ``urls.table(database, table)``, ``urls.query(database, query)``, ``urls.row(database, table, row_path)`` and ``urls.static(path)`` - see :ref:`internals_datasette_urls`.

``actor``
    The currently authenticated actor dictionary, or None. Actors usually include an ``id`` key and may include any other keys supplied by authentication plugins.

``menu_links``
    Async function returning links for the Datasette application menu, including links added by plugins. Each item is a link dictionary with ``href`` and ``label`` keys. See :ref:`plugin_hook_menu_links`; for page action menus that can also include JavaScript-backed buttons, see :ref:`plugin_actions`.

``display_actor``
    Function that accepts an actor dictionary and returns the display string used in the navigation menu.

``show_logout``
    True if the logout link should be shown in the navigation menu

``app_css_hash``
    Hash of Datasette's app.css contents, used for cache busting

``edit_tools_js_hash``
    Hash of Datasette's edit-tools.js contents, used for cache busting

``table_js_hash``
    Hash of Datasette's table.js contents, used for cache busting

``zip``
    Python's ``zip()`` builtin, made available to template logic

``body_scripts``
    List of JavaScript snippets contributed by plugins using :ref:`plugin_hook_extra_body_script`. Each item is a dictionary with ``script`` containing JavaScript source and ``module`` indicating whether Datasette will wrap it in ``<script type="module">``; otherwise Datasette wraps it in a regular ``<script>`` block.

``format_bytes``
    Function that accepts a byte count integer and returns a human-readable string such as ``1.2 MB``.

``show_messages``
    Function returning any messages set for the current user, clearing them in the process. Returns a list of ``(message, type)`` pairs, where ``type`` is one of Datasette's ``INFO``, ``WARNING`` or ``ERROR`` constants.

``extra_css_urls``
    List of extra CSS stylesheets to include on the page. Each item is a dictionary with ``url`` and optional ``sri`` keys, from plugins and configuration.

``extra_js_urls``
    List of extra JavaScript URLs to include on the page. Each item is a dictionary with ``url`` plus optional ``sri`` and ``module`` keys, from plugins and configuration.

``base_url``
    The configured :ref:`setting_base_url` setting

``datasette_version``
    The version of Datasette that is running

Database page
-------------

The page listing the tables, views and queries in a database, e.g. /fixtures. Rendered using the ``database.html`` template.

``allow_download`` - ``bool``
    Boolean indicating if database download is allowed

``allow_execute_sql`` - ``bool``
    Boolean indicating if custom SQL can be executed

``alternate_url_json`` - ``str``
    URL for the alternate JSON version of this page

``attached_databases`` - ``list``
    List of names of attached databases

``database`` - ``str``
    The name of the database

``database_actions`` - ``callable``
    Callable returning list of action links for the database menu

``database_color`` - ``str``
    The color assigned to the database

``database_page_data`` - ``dict``
    JSON data used by JavaScript on the database page

``editable`` - ``bool``
    Boolean indicating if the database is editable

``hidden_count`` - ``int``
    Count of hidden tables

``metadata`` - ``dict``
    Metadata for the database

``path`` - ``str``
    The URL path to this database

``private`` - ``bool``
    Boolean indicating if this is a private database

``queries`` - ``list``
    List of stored query objects

``queries_count`` - ``int``
    Count of visible stored queries

``queries_more`` - ``bool``
    Boolean indicating if more stored queries are available

``select_templates`` - ``list``
    List of templates that were considered for rendering this page

``show_hidden`` - ``str``
    Value of _show_hidden query parameter

``size`` - ``int``
    The size of the database in bytes

``table_columns`` - ``dict``
    Dictionary mapping table names to their column lists

``tables`` - ``list``
    List of table objects in the database. Each item includes a ``count_truncated`` key that is true if ``count`` is a capped lower bound rather than an exact total.

``top_database`` - ``callable``
    Callable to render the top_database slot

``views`` - ``list``
    List of view objects in the database

Query page
----------

The page for arbitrary SQL queries (/database/-/query?sql=...) and stored queries (/database/query-name). Rendered using the ``query.html`` template.

``allow_execute_sql`` - ``bool``
    Boolean indicating if custom SQL can be executed

``alternate_url_json`` - ``str``
    URL for alternate JSON version of this page

``columns`` - ``list``
    List of column names

``database`` - ``str``
    The name of the database being queried

``database_color`` - ``str``
    The color of the database

``db_is_immutable`` - ``bool``
    Boolean indicating if this database is immutable

``display_rows`` - ``list``
    List of result rows to display

``edit_sql_url`` - ``str``
    URL to edit the SQL for a stored query

``editable`` - ``bool``
    Boolean indicating if the SQL can be edited

``error`` - ``str``
    Any query error message

``hide_sql`` - ``bool``
    Boolean indicating if the SQL should be hidden

``metadata`` - ``dict``
    Metadata about the database or the stored query

``named_parameter_values`` - ``dict``
    Dictionary of parameter names/values

``private`` - ``bool``
    Boolean indicating if this is a private database

``query`` - ``dict``
    The SQL query object containing the `sql` string

``query_actions`` - ``callable``
    Callable returning a list of links for the query action menu

``renderers`` - ``dict``
    Dictionary of renderer name to URL

``save_query_url`` - ``str``
    URL to save the current arbitrary SQL as a query

``select_templates`` - ``list``
    List of templates that were considered for rendering this page

``show_hide_hidden`` - ``str``
    Hidden input field for the _show_sql parameter

``show_hide_link`` - ``str``
    The URL to toggle showing/hiding the SQL

``show_hide_text`` - ``str``
    The text for the show/hide SQL link

``stored_query`` - ``str``
    The name of the stored query if this is a stored query

``stored_query_write`` - ``bool``
    Boolean indicating if this is a stored query that allows writes

``table_columns`` - ``dict``
    Dictionary of table name to list of column names

``tables`` - ``list``
    List of table objects in the database. Each item includes a ``count_truncated`` key that is true if ``count`` is a capped lower bound rather than an exact total.

``top_query`` - ``callable``
    Callable to render the top_query slot

``top_stored_query`` - ``callable``
    Callable to render the top_stored_query slot

``url_csv`` - ``str``
    URL for CSV export

Table page
----------

The page showing the rows in a table or SQL view, e.g. /fixtures/facetable. Rendered using the ``table.html`` template.

Many of these keys are shared with the :ref:`JSON API <json_api>` for this page.

``actions`` - ``callable``
    Table or view actions made available by plugin hooks

``all_columns`` - ``list``
    All columns in the table, regardless of _col/_nocol filtering

``allow_execute_sql`` - ``bool``
    True if the current actor can execute custom SQL against this database

``alternate_url_json`` - ``str``
    URL for the JSON version of this page

``append_querystring`` - ``callable``
    Function that appends additional querystring arguments to a URL

``columns`` - ``list``
    Column names returned by this query

``count`` - ``int``
    Total count of rows matching these filters

``count_sql`` - ``str``
    SQL query used to calculate the total count

``count_truncated`` - ``bool``
    True if ``count`` is a capped lower bound rather than an exact total, because Datasette stopped counting after its configured row-count limit.

``custom_table_templates`` - ``list``
    Custom template names considered for this table

``database`` - ``str``
    Database name

``database_color`` - ``str``
    Color assigned to the database

``datasette_allow_facet`` - ``str``
    The string "true" or "false" reflecting the allow_facet setting

``display_columns`` - ``list``
    Column metadata used by the HTML table display

``display_rows`` - ``list``
    Row data formatted for the HTML table display

``expandable_columns`` - ``list``
    Foreign key columns that can be expanded with labels

``extra_wheres_for_ui`` - ``list``
    Extra where clauses from ?_where=, with links to remove them

``facet_results`` - ``dict``
    Results of facets calculated against this data

``facets_timed_out`` - ``list``
    Facet calculations that timed out

``filter_columns`` - ``list``
    List of columns offered by the filter interface

``filters`` - ``Filters``
    Filters object used by the HTML table interface

``fix_path`` - ``callable``
    Function that applies the base_url prefix to a path

``form_hidden_args`` - ``list``
    Hidden form arguments used by the HTML table interface

``human_description_en`` - ``str``
    Human-readable description of the filters

``is_sortable`` - ``bool``
    True if any of the displayed columns can be used to sort

``is_view`` - ``bool``
    Whether this resource is a view instead of a table

``metadata`` - ``dict``
    Metadata about the table, database or stored query

``next`` - ``str``
    Pagination token for the next page, or None

``next_url`` - ``str``
    Full URL for the next page of results

``ok`` - ``bool``
    True if the data for this page was retrieved without errors

``path_with_replaced_args`` - ``callable``
    Function for building the current path with modified querystring arguments

``primary_keys`` - ``list``
    Primary keys for this table

``private`` - ``bool``
    Whether this resource is private to the current actor

``query`` - ``dict``
    Details of the underlying SQL query

``query_ms`` - ``float``
    Time taken by the SQL queries for this page, in milliseconds

``renderers`` - ``dict``
    Alternative output renderers available for this table

``rows`` - ``list``
    The rows for this page, as a list of dictionaries mapping column name to value

``select_templates`` - ``list``
    List of template names that were considered for this page, the one used marked with an asterisk

``set_column_type_ui`` - ``dict``
    Information needed to build an interface for assigning column types

``settings`` - ``dict``
    Dictionary of Datasette's current settings

``sort`` - ``str``
    Column the page is sorted by, or None

``sort_desc`` - ``str``
    Column the page is sorted by in descending order, or None

``sorted_facet_results`` - ``list``
    Facet results sorted for display

``suggested_facets`` - ``list``
    Suggestions for facets that might return interesting results

``supports_search`` - ``bool``
    True if this table has full-text search configured

``table`` - ``str``
    Table name

``table_definition`` - ``str``
    SQL definition for this table

``table_page_data`` - ``dict``
    JSON data used by JavaScript on the table page

``top_table`` - ``callable``
    Async function rendering the top_table plugin slot

``url_csv`` - ``str``
    URL for the CSV export of this page

``url_csv_hidden_args`` - ``list``
    (name, value) pairs for hidden form fields used by the CSV export form

``url_csv_path`` - ``str``
    Path portion of the CSV export URL

``view_definition`` - ``str``
    SQL definition for this view

Row page
--------

The page showing an individual row, e.g. /fixtures/facetable/1. Rendered using the ``row.html`` template.

Many of these keys are shared with the :ref:`JSON API <json_api>` for this page.

``alternate_url_json`` - ``str``
    URL for the JSON version of this page

``columns`` - ``list``
    Column names returned by this query

``custom_table_templates`` - ``list``
    Custom template names that were considered for displaying this table

``database`` - ``str``
    Database name

``database_color`` - ``str``
    Color assigned to the database

``display_columns`` - ``list``
    Column objects formatted for the HTML table display

``display_rows`` - ``list``
    Row data formatted for the HTML table display

``foreign_key_tables`` - ``list``
    Tables that link to this row using foreign keys

``metadata`` - ``dict``
    Metadata about the table, database or stored query

``ok`` - ``bool``
    True if the data for this page was retrieved without errors

``primary_key_values`` - ``list``
    Values of the primary keys for this row, from the URL

``primary_keys`` - ``list``
    Primary keys for this table

``private`` - ``bool``
    Whether this resource is private to the current actor

``query_ms`` - ``float``
    Time taken by the SQL queries for this page, in milliseconds

``renderers`` - ``dict``
    Dictionary mapping output format names (e.g. json) to their URLs for this page

``row_actions`` - ``list``
    Row actions made available by plugin hooks

``row_mutation_ui`` - ``bool``
    True if the row edit/delete JavaScript UI should be enabled

``rows`` - ``list``
    The rows for this page, as a list of dictionaries mapping column name to value

``select_templates`` - ``list``
    List of template names that were considered for this page, the one used marked with an asterisk

``settings`` - ``dict``
    Dictionary of Datasette's current settings

``table`` - ``str``
    Table name

``table_page_data`` - ``dict``
    JSON data used by JavaScript on the row page

``top_row`` - ``callable``
    Async function rendering the top_row plugin slot

``url_csv`` - ``str``
    URL for the CSV export of this page

``url_csv_hidden_args`` - ``list``
    (name, value) pairs for hidden form fields used by the CSV export form

``url_csv_path`` - ``str``
    Path portion of the CSV export URL

.. [[[end]]]
