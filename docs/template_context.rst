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
    The current Request object, or None

``crumb_items``
    Async function returning breadcrumb navigation items for the current page

``urls``
    Object with methods for constructing URLs to pages within Datasette - see datasette.urls in the internals documentation

``actor``
    The currently authenticated actor dictionary, or None

``menu_links``
    Async function returning links for the Datasette application menu, including those added by plugins

``display_actor``
    Function returning a display string for an actor dictionary

``show_logout``
    True if the logout link should be shown in the navigation menu

``app_css_hash``
    Hash of Datasette's app.css contents, used for cache busting

``zip``
    Python's zip() builtin, made available to template logic

``body_scripts``
    List of script blocks for the page body contributed by plugins

``format_bytes``
    Function that formats a number of bytes as a human-readable size

``show_messages``
    Function returning any messages set for the current user, clearing them in the process

``extra_css_urls``
    List of {url, sri} dictionaries of extra CSS stylesheets to include on the page, from plugins and configuration

``extra_js_urls``
    List of {url, sri, module} dictionaries of extra JavaScript URLs to include on the page

``base_url``
    The configured base_url setting

``csrftoken``
    Function returning the CSRF token for the current request

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

``count_limit`` - ``int``
    The maximum number of rows to count

``database`` - ``str``
    The name of the database

``database_actions`` - ``callable``
    Callable returning list of action links for the database menu

``database_color`` - ``str``
    The color assigned to the database

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
    List of table objects in the database

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
    List of table objects in the database

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

``actions``
    Table or view actions made available by plugin hooks

``all_columns``
    All columns in the table, regardless of _col/_nocol filtering

``allow_execute_sql``
    True if the current actor can execute custom SQL against this database

``alternate_url_json``
    URL for the JSON version of this page

``append_querystring``
    Function that appends additional querystring arguments to a URL

``columns``
    Column names returned by this query

``count``
    Total count of rows matching these filters

``count_limit``
    The maximum number of rows Datasette will count before showing an approximation

``count_sql``
    SQL query used to calculate the total count

``custom_table_templates``
    Custom template names considered for this table

``database``
    Database name

``database_color``
    Color assigned to the database

``datasette_allow_facet``
    The string "true" or "false" reflecting the allow_facet setting

``display_columns``
    Column metadata used by the HTML table display

``display_rows``
    Row data formatted for the HTML table display

``expandable_columns``
    Foreign key columns that can be expanded with labels

``extra_wheres_for_ui``
    Extra where clauses from ?_where=, with links to remove them

``facet_results``
    Results of facets calculated against this data

``facets_timed_out``
    Facet calculations that timed out

``filter_columns``
    List of columns offered by the filter interface

``filters``
    Filters object used by the HTML table interface

``fix_path``
    Function that applies the base_url prefix to a path

``form_hidden_args``
    Hidden form arguments used by the HTML table interface

``human_description_en``
    Human-readable description of the filters

``is_sortable``
    True if any of the displayed columns can be used to sort

``is_view``
    Whether this resource is a view instead of a table

``metadata``
    Metadata about the table, database or stored query

``next``
    Pagination token for the next page, or None

``next_url``
    Full URL for the next page of results

``ok``
    True if the data for this page was retrieved without errors

``path_with_replaced_args``
    Function for building the current path with modified querystring arguments

``primary_keys``
    Primary keys for this table

``private``
    Whether this resource is private to the current actor

``query``
    Details of the underlying SQL query

``query_ms``
    Time taken by the SQL queries for this page, in milliseconds

``renderers``
    Alternative output renderers available for this table

``rows``
    The rows for this page, as a list of dictionaries mapping column name to value

``select_templates``
    List of template names that were considered for this page, the one used marked with an asterisk

``set_column_type_ui``
    Column type UI metadata for this table

``settings``
    Dictionary of Datasette's current settings

``sort``
    Column the page is sorted by, or None

``sort_desc``
    Column the page is sorted by in descending order, or None

``sorted_facet_results``
    Facet results sorted for display

``suggested_facets``
    Suggestions for facets that might return interesting results

``supports_search``
    True if this table has full-text search configured

``table``
    Table name

``table_definition``
    SQL definition for this table

``top_table``
    Async function rendering the top_table plugin slot

``url_csv``
    URL for the CSV export of this page

``url_csv_hidden_args``
    (name, value) pairs for hidden form fields used by the CSV export form

``url_csv_path``
    Path portion of the CSV export URL

``view_definition``
    SQL definition for this view

Row page
--------

The page showing an individual row, e.g. /fixtures/facetable/1. Rendered using the ``row.html`` template.

Many of these keys are shared with the :ref:`JSON API <json_api>` for this page.

``alternate_url_json``
    URL for the JSON version of this page

``columns``
    Column names returned by this query

``custom_table_templates``
    Custom template names that were considered for displaying this table

``database``
    Database name

``database_color``
    Color assigned to the database

``display_columns``
    Column objects formatted for the HTML table display

``display_rows``
    Row data formatted for the HTML table display

``foreign_key_tables``
    Tables that link to this row using foreign keys

``metadata``
    Metadata about the table, database or stored query

``ok``
    True if the data for this page was retrieved without errors

``primary_key_values``
    Values of the primary keys for this row, from the URL

``primary_keys``
    Primary keys for this table

``private``
    Whether this resource is private to the current actor

``query_ms``
    Time taken by the SQL queries for this page, in milliseconds

``renderers``
    Dictionary mapping output format names (e.g. json) to their URLs for this page

``row_actions``
    Row actions made available by plugin hooks

``rows``
    The rows for this page, as a list of dictionaries mapping column name to value

``select_templates``
    List of template names that were considered for this page, the one used marked with an asterisk

``settings``
    Dictionary of Datasette's current settings

``table``
    Table name

``top_row``
    Async function rendering the top_row plugin slot

``url_csv``
    URL for the CSV export of this page

``url_csv_hidden_args``
    (name, value) pairs for hidden form fields used by the CSV export form

``url_csv_path``
    Path portion of the CSV export URL

.. [[[end]]]
