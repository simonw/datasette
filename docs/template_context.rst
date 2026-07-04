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
    List of names of databases attached to this SQLite connection. This is only populated for the special ``/_memory`` database when Datasette is started with ``--crossdb`` for :ref:`cross_database_queries`.

``database`` - ``str``
    The name of the database

``database_actions`` - ``callable``
    Async callable returning action items for the database menu. Each item is either a link with ``href``, ``label`` and optional ``description`` keys, or a button with ``type: "button"``, ``label``, optional ``description`` and optional ``attrs``. See :ref:`plugin_actions` and :ref:`plugin_hook_database_actions`.

``database_color`` - ``str``
    The color assigned to the database

``database_page_data`` - ``dict``
    JSON data used by JavaScript on the database page. Currently ``{}`` or ``{"createTable": {...}}`` where ``createTable`` includes ``path``, ``foreignKeyTargetsPath``, ``databaseName``, ``columnTypes``, ``defaultExpressions``, ``canInsertRows`` and optional ``customColumnTypes``.

``editable`` - ``bool``
    Boolean indicating if the database is editable

``hidden_count`` - ``int``
    Count of hidden tables

``metadata`` - ``dict``
    Metadata dictionary for the database, such as ``title``, ``description``, ``license`` and ``source`` values from Datasette metadata.

``path`` - ``str``
    The URL path to this database

``private`` - ``bool``
    Boolean indicating if this is a private database

``queries`` - ``list[StoredQuery]``
    List of ``StoredQuery`` objects. Each has attributes including ``name``, ``sql``, ``title``, ``description``, ``description_html``, ``hide_sql``, ``fragment``, ``parameters``, ``is_write`` and ``private``.

``queries_count`` - ``int``
    Count of visible stored queries

``queries_more`` - ``bool``
    Boolean indicating if more stored queries are available

``select_templates`` - ``list``
    List of template names that were considered for this page, with the selected template prefixed by ``*``.

``show_hidden`` - ``str``
    Value of _show_hidden query parameter

``size`` - ``int``
    The size of the database in bytes

``table_columns`` - ``dict``
    Dictionary mapping table names to lists of column names, used to power SQL autocomplete.

``tables`` - ``list[DatabaseTable]``
    List of ``DatabaseTable`` objects describing tables in the database. Each item has ``name``, ``columns``, ``primary_keys``, ``count``, ``count_truncated``, ``hidden``, ``fts_table``, ``foreign_keys`` and ``private`` attributes. ``count_truncated`` is true if ``count`` is a capped lower bound rather than an exact total.

``top_database`` - ``callable``
    Async callable that renders the ``top_database`` plugin slot for this database and returns HTML.

``views`` - ``list[DatabaseViewInfo]``
    List of ``DatabaseViewInfo`` objects describing SQLite views in the database. Each item has ``name`` and ``private`` attributes.

Query page
----------

The page for arbitrary SQL queries (/database/-/query?sql=...) and stored queries (/database/query-name). Rendered using the ``query.html`` template.

``allow_execute_sql`` - ``bool``
    Boolean indicating if custom SQL can be executed

``alternate_url_json`` - ``str``
    URL for alternate JSON version of this page

``columns`` - ``list``
    List of result column names in the order they appear in ``display_rows`` and ``rows``.

``database`` - ``str``
    The name of the database being queried

``database_color`` - ``str``
    The color of the database

``db_is_immutable`` - ``bool``
    Boolean indicating if this database is immutable

``display_rows`` - ``list``
    List of result rows formatted for HTML display. Each row is a list of rendered cell values in the same order as ``columns``.

``edit_sql_url`` - ``str``
    URL to edit the SQL for a stored query

``editable`` - ``bool``
    Boolean indicating if the SQL can be edited

``error`` - ``str``
    Any query error message

``hide_sql`` - ``bool``
    Boolean indicating if the SQL should be hidden

``metadata`` - ``dict``
    Metadata dictionary for the database or stored query. Stored query metadata may include options such as ``hide_sql``, ``on_success_message`` and ``on_error_redirect``.

``named_parameter_values`` - ``dict``
    Dictionary of named SQL parameter values, keyed by parameter name without the leading ``:``.

``private`` - ``bool``
    Boolean indicating if this is a private database

``query`` - ``dict``
    Dictionary describing the SQL query being executed, with ``sql`` and ``params`` keys.

``query_actions`` - ``callable``
    Async callable returning action items for the query menu. Each item is either a link with ``href``, ``label`` and optional ``description`` keys, or a button with ``type: "button"``, ``label``, optional ``description`` and optional ``attrs``. See :ref:`plugin_actions` and :ref:`plugin_hook_query_actions`.

``renderers`` - ``dict``
    Dictionary mapping output format names such as ``json`` to URLs for this query in that format.

``save_query_url`` - ``str``
    URL to save the current arbitrary SQL as a query

``select_templates`` - ``list``
    List of template names that were considered for this page, with the selected template prefixed by ``*``.

``show_hide_hidden`` - ``str``
    Rendered hidden ``<input>`` HTML preserving the current ``_hide_sql`` or ``_show_sql`` state.

``show_hide_link`` - ``str``
    The URL to toggle showing/hiding the SQL

``show_hide_text`` - ``str``
    The text for the show/hide SQL link

``stored_query`` - ``str``
    The name of the stored query if this is a stored query

``stored_query_write`` - ``bool``
    Boolean indicating if this is a stored query that allows writes

``table_columns`` - ``dict``
    Dictionary mapping table names to lists of column names, used to power SQL autocomplete.

``tables`` - ``list[DatabaseTable]``
    List of ``DatabaseTable`` objects describing tables in the database. Each item has ``name``, ``columns``, ``primary_keys``, ``count``, ``count_truncated``, ``hidden``, ``fts_table``, ``foreign_keys`` and ``private`` attributes. ``count_truncated`` is true if ``count`` is a capped lower bound rather than an exact total.

``top_query`` - ``callable``
    Async callable that renders the ``top_query`` plugin slot for this query and returns HTML.

``top_stored_query`` - ``callable``
    Async callable that renders the ``top_stored_query`` plugin slot for stored queries and returns HTML.

``url_csv`` - ``str``
    URL for CSV export

Table page
----------

The page showing the rows in a table or SQL view, e.g. /fixtures/facetable. Rendered using the ``table.html`` template.

Many of these keys are shared with the :ref:`JSON API <json_api>` for this page.

``actions`` - ``callable``
    Async callable returning table or view actions made available by core and plugin hooks. Each item is either a link with ``href``, ``label`` and optional ``description`` keys, or a button with ``type: "button"``, ``label``, optional ``description`` and optional ``attrs``. See :ref:`plugin_actions`, :ref:`plugin_hook_table_actions` and :ref:`plugin_hook_view_actions`.

``all_columns`` - ``list``
    List of all column names in the table, regardless of ``_col=`` or ``_nocol=`` filtering.

``allow_execute_sql`` - ``bool``
    True if the current actor can execute custom SQL against this database

``alternate_url_json`` - ``str``
    URL for the JSON version of this page

``append_querystring`` - ``callable``
    Function ``append_querystring(url, querystring)`` that appends additional query string arguments to a URL, using ``?`` or ``&`` as appropriate.

``columns`` - ``list``
    List of column names returned by this table, row or query.

``count`` - ``int``
    Total count of rows matching these filters

``count_sql`` - ``str``
    SQL query string used to calculate the total count for the current table view, including active filters.

``count_truncated`` - ``bool``
    True if ``count`` is a capped lower bound rather than an exact total, because Datasette stopped counting after its configured row-count limit.

``custom_table_templates`` - ``list``
    List of custom template names considered for rendering table rows, in lookup order.

``database`` - ``str``
    Database name

``database_color`` - ``str``
    Color assigned to the database

``datasette_allow_facet`` - ``str``
    The string "true" or "false" reflecting the allow_facet setting

``display_columns`` - ``list``
    Column metadata used by the HTML table display. Each item includes ``name``, ``sortable``, ``is_pk``, ``type``, ``notnull``, ``description``, ``column_type`` and ``column_type_config`` keys.

``display_rows`` - ``list``
    Rows formatted for the HTML table display. Each row is iterable and contains cell dictionaries with ``column``, ``value``, ``raw`` and ``value_type`` keys; table pages may also provide ``pk_path``, ``row_path`` and ``row_label`` attributes on each row object.

``expandable_columns`` - ``list``
    List of foreign key columns that can be expanded with labels. Each item is a ``(foreign_key, label_column)`` pair where ``foreign_key`` is the SQLite foreign key dictionary and ``label_column`` is the label column in the referenced table, or ``None``.

``extra_wheres_for_ui`` - ``list``
    Extra where clauses from ``?_where=`` for display in the UI. Each item has ``text`` for the SQL fragment and ``remove_url`` for a URL that removes that fragment.

``facet_results`` - ``dict``
    Results of facets calculated against this data. A dictionary with ``results`` and ``timed_out`` keys: ``results`` maps facet names to facet dictionaries with ``name``, ``type``, ``results`` and URL keys, and each facet result item includes ``value``, ``label``, ``count`` and ``toggle_url``.

``facets_timed_out`` - ``list``
    List of names of facet calculations that exceeded the facet time limit.

``filter_columns`` - ``list``
    List of column names offered by the filter interface, including currently displayed columns and any hidden columns that can still be filtered.

``filters`` - ``Filters``
    ``Filters`` object used by the HTML table interface. Useful methods include ``filters.human_description_en()``; this is not JSON serializable.

``fix_path`` - ``callable``
    Function that applies the configured ``base_url`` prefix to a path.

``form_hidden_args`` - ``list``
    List of ``(name, value)`` pairs for hidden form fields used by the HTML table interface to preserve current query string options.

``human_description_en`` - ``str``
    Human-readable description of the filters

``is_sortable`` - ``bool``
    True if any of the displayed columns can be used to sort

``is_view`` - ``bool``
    Whether this resource is a view instead of a table

``metadata`` - ``dict``
    Metadata dictionary for the table, database or stored query. Table and row metadata include a ``columns`` dictionary mapping column names to descriptions; stored query metadata returns the stored query configuration.

``next`` - ``str``
    Pagination token for the next page, or None

``next_url`` - ``str``
    Full URL for the next page of results

``ok`` - ``bool``
    True if the data for this page was retrieved without errors

``path_with_replaced_args`` - ``callable``
    Function for building the current path with modified query string arguments. Pass the current ``request`` and a dictionary of argument names to replacement values, using ``None`` to remove an argument.

``primary_keys`` - ``list``
    List of primary key column names for this table, or an empty list if the table has no explicit primary key.

``private`` - ``bool``
    Whether this resource is private to the current actor

``query`` - ``dict``
    Details of the underlying SQL query as a dictionary with ``sql`` and ``params`` keys.

``query_ms`` - ``float``
    Time taken by the SQL queries for this page, in milliseconds

``renderers`` - ``dict``
    Dictionary mapping output format names such as ``json`` or plugin-provided renderer names to URLs for this data in that format.

``rows`` - ``list``
    The rows for this page, as a list of dictionaries mapping column name to raw value.

``select_templates`` - ``list``
    List of template names that were considered for this page, with the selected template prefixed by ``*``.

``set_column_type_ui`` - ``dict``
    Information needed to build an interface for assigning column types, or ``None`` if unavailable. When present it has ``path`` and ``columns`` keys; ``columns`` maps column names to ``current`` and ``options`` values.

``settings`` - ``dict``
    Dictionary of Datasette's current settings, keyed by setting name.

``sort`` - ``str``
    Column the page is sorted by, or None

``sort_desc`` - ``str``
    Column the page is sorted by in descending order, or None

``sorted_facet_results`` - ``list``
    Facet result dictionaries sorted for display. Each item has the same shape as an entry from ``facet_results['results']``.

``suggested_facets`` - ``list``
    Suggestions for facets that might return interesting results. Each item is a dictionary with ``name`` and ``toggle_url`` keys, and may include extra keys such as ``type`` or ``label`` depending on the facet class.

``supports_search`` - ``bool``
    True if this table has full-text search configured

``table`` - ``str``
    Table name

``table_alter_ui`` - ``dict``
    Information needed to enable the alter table UI, or ``None`` if altering this table is not available to the current actor. When present it has ``path``, ``tableName``, ``columns``, ``primaryKeys``, ``columnTypes``, ``defaultExpressions`` and ``foreignKeyTargetsPath`` keys, plus optional ``customColumnTypes`` and ``dropPath`` keys.

``table_definition`` - ``str``
    SQL definition for this table

``table_insert_ui`` - ``dict``
    Information needed to enable the row insertion UI, or ``None`` if row insertion is not available to the current actor. When present it has ``path``, ``tableName``, ``columns``, ``bulkColumns``, ``primaryKeys`` and ``maxInsertRows`` keys, plus optional ``upsertPath`` if the current actor has permission to update rows. ``columns`` lists columns for the single-row insert form, while ``bulkColumns`` lists columns for the bulk insert form. Each column includes ``name``, ``sqlite_type``, ``notnull``, ``default``, ``has_default``, ``is_pk``, ``is_auto_pk``, ``value_kind`` and ``column_type`` keys.

``table_page_data`` - ``dict``
    JSON data used by JavaScript on the table page. Includes ``database``, ``table`` and ``tableUrl``, plus optional ``foreignKeys`` mapping column names to autocomplete URLs, optional ``insertRow`` data and optional ``alterTable`` data.

``top_table`` - ``callable``
    Async callable that renders the ``top_table`` plugin slot for this table or view and returns HTML.

``url_csv`` - ``str``
    URL for the CSV export of this page

``url_csv_hidden_args`` - ``list``
    List of ``(name, value)`` pairs for hidden form fields used by the CSV export form, preserving current filters while forcing ``_size=max``.

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
    List of column names returned by this table, row or query.

``custom_table_templates`` - ``list``
    Custom template names that were considered for displaying this row's table, in lookup order.

``database`` - ``str``
    Database name

``database_color`` - ``str``
    Color assigned to the database

``display_columns`` - ``list``
    Column metadata used by the HTML table display. Each item includes ``name``, ``sortable``, ``is_pk``, ``type``, ``notnull``, ``description``, ``column_type`` and ``column_type_config`` keys.

``display_rows`` - ``list``
    Rows formatted for the HTML table display. Each row is iterable and contains cell dictionaries with ``column``, ``value``, ``raw`` and ``value_type`` keys.

``foreign_key_tables`` - ``list``
    List of tables that link to this row using foreign keys. Each item includes the foreign key fields plus ``count`` for matching rows and ``link`` for the filtered table URL.

``metadata`` - ``dict``
    Metadata dictionary for the table, database or stored query. Table and row metadata include a ``columns`` dictionary mapping column names to descriptions; stored query metadata returns the stored query configuration.

``ok`` - ``bool``
    True if the data for this page was retrieved without errors

``primary_key_values`` - ``list``
    Values of the primary keys for this row, from the URL

``primary_keys`` - ``list``
    List of primary key column names for this table, or an empty list if the table has no explicit primary key.

``private`` - ``bool``
    Whether this resource is private to the current actor

``query_ms`` - ``float``
    Time taken by the SQL queries for this page, in milliseconds

``renderers`` - ``dict``
    Dictionary mapping output format names such as ``json`` to URLs for this row in that format.

``row_actions`` - ``list``
    Row actions made available by core and plugin hooks. Each item is either a link with ``href``, ``label`` and optional ``description`` keys, or a button with ``type: "button"``, ``label``, optional ``description`` and optional ``attrs``. See :ref:`plugin_actions` and :ref:`plugin_hook_row_actions`.

``row_mutation_ui`` - ``bool``
    True if the row edit/delete JavaScript UI should be enabled

``rows`` - ``list``
    A single-item list containing this row as a dictionary mapping column name to raw value.

``select_templates`` - ``list``
    List of template names that were considered for this page, with the selected template prefixed by ``*``.

``settings`` - ``dict``
    Dictionary of Datasette's current settings, keyed by setting name.

``table`` - ``str``
    Table name

``table_page_data`` - ``dict``
    JSON data used by JavaScript on the row page. Includes ``database``, ``table`` and ``tableUrl``, plus optional ``foreignKeys`` mapping column names to autocomplete URLs.

``top_row`` - ``callable``
    Async callable that renders the ``top_row`` plugin slot for this row and returns HTML.

``url_csv`` - ``str``
    URL for the CSV export of this page

``url_csv_hidden_args`` - ``list``
    List of ``(name, value)`` pairs for hidden form fields used by the CSV export form, preserving current options while forcing ``_size=max``.

``url_csv_path`` - ``str``
    Path portion of the CSV export URL

.. [[[end]]]
