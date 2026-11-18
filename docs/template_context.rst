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
This shows each variable with its documentation and current value - see
:ref:`setting_template_debug`.

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
    Object with methods for constructing URLs within Datasette. Common methods include ``urls.instance()``, ``urls.database(database)``, ``urls.table(database, table)``, ``urls.query(database, query)``, ``urls.row(database, table, row_path)``, ``urls.static(path)`` and ``urls.modify(request, ...)`` for links that change the current query string - see :ref:`internals_datasette_urls`.

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

Index page
----------

The homepage, listing the tables, views and queries across every database. Rendered using the ``index.html`` template.

``alternate_url_json`` - ``str``
    URL of the JSON version of this page, ``/.json``

``catalog`` - ``Catalog``
    The full catalog object: ``items`` and ``queries`` (see below), ``total`` (tables and views matching the search and database filter, across all pages), ``queries_total``, ``databases`` and ``totals`` (a dictionary of ``databases``, ``tables`` and ``views`` counts)

``database_filter`` - ``str | None``
    Name of the database the list is filtered to with ``?database=``, or None

``database_url`` - ``Callable``
    Function that takes a database name, or None for all databases, and returns the homepage URL filtered to that database, keeping the current search

``databases`` - ``list[dict]``
    One dictionary per database the actor can see, in the order they were attached, with keys ``name``, ``color``, ``private``, ``tables`` and ``views`` (counts matching the current search), ``url`` and ``size`` (bytes, or None for in-memory databases)

``homepage_actions`` - ``list[dict]``
    Links for the homepage actions menu, from plugins implementing :ref:`plugin_hook_homepage_actions`

``items`` - ``list[dict]``
    The tables and views on this page. Each is a dictionary with ``database``, ``name``, ``type`` (``table`` or ``view``), ``private``, ``title``, ``description``, ``columns`` (up to eight column names), ``more_columns`` (the rest of the column names), ``matched_columns`` (those matching the search), ``column_count``, ``linked_tables`` (other tables linked to or from by a foreign key), ``has_fts``, ``count``, ``count_truncated``, ``url`` and ``color``. ``count`` is None if counting took too long

``metadata`` - ``dict``
    Instance metadata, such as ``title``, ``description`` and ``license``

``next_url`` - ``str | None``
    URL of the next page, or None on the last page

``noindex`` - ``bool``
    True for the ``/-/`` alternative homepage, which should not be indexed by search engines

``other_results`` - ``list[dict]``
    While searching, items added to the Jump to menu by plugins using :ref:`plugin_hook_jump_items_sql` that match the search, excluding databases, tables, views and queries. Each has ``name``, ``url``, ``type`` and ``description`` keys

``page`` - ``int``
    The current page of tables and views, from ``?page=``

``page_count`` - ``int``
    The total number of pages

``page_size`` - ``int``
    The number of tables and views per page

``previous_url`` - ``str | None``
    URL of the previous page, or None on the first page

``private`` - ``bool``
    True if anonymous users cannot view this instance

``q`` - ``str``
    The current search, from ``?q=``

``queries`` - ``list[dict]``
    Up to five stored queries. Without a search these are queries from configuration or plugins, then the actor's own saved queries; with a search, any matching query the actor can see. Each is a dictionary with ``database``, ``name``, ``title``, ``description``, ``sql``, ``is_write``, ``private``, ``parameters``, ``url``, ``color`` and ``mine``

``select_templates`` - ``list[str]``
    The template names Datasette considered, with an asterisk next to the one that was used

``top_homepage`` - ``Callable``
    Function returning HTML from plugins that implement :ref:`plugin_hook_top_homepage`

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

``query_page_data`` - ``dict``
    JSON data used by the column statistics dialog on the query page, or ``None`` if it is not available. It is only provided for read-only queries that returned rows, use no magic parameters and are run by an actor who can execute custom SQL. Has ``database`` and ``executeSql`` keys. ``executeSql`` has ``queryUrl`` and ``editorUrl``, a ``query`` with the ``sql``, its ``params`` and ``shownRows``, the number of rows shown on the page, and ``columns``: the result columns with unique ``name`` values and the SQLite storage ``type`` seen in the returned rows (``mixed`` if more than one).

``renderer_options`` - ``dict``
    Dictionary mapping output format names to the ``label``, ``description``, ``is_binary`` and ``is_webpage`` options their :ref:`plugin_register_output_renderer` plugin hook set, used by the export dialog.

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

``truncated`` - ``bool``
    Boolean indicating if the results were truncated at the ``max_returned_rows`` limit

``url_csv`` - ``str``
    URL for CSV export

``url_csv_hidden_args`` - ``list``
    List of ``(name, value)`` pairs for hidden form fields used by the CSV export form, preserving the SQL and parameters while forcing ``_size=max``.

``url_csv_path`` - ``str``
    Path portion of the CSV export URL

``url_tsv_path`` - ``str``
    Path portion of the TSV export URL, which takes the same arguments as CSV, or None if a plugin provides the ``tsv`` format

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

``clear_filters_url`` - ``str``
    URL for this page with all column filters removed, or None if no column filters are applied. Other arguments such as ``?_search=``, ``?_where=`` and ``?_facet=`` are preserved.

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

``display_columns`` - ``list``
    Column metadata used by the HTML table display. Each item includes ``name``, ``sortable``, ``is_pk``, ``type``, ``notnull``, ``description``, ``column_type``, ``column_type_config`` and ``has_fks`` keys. ``has_fks`` is true when at least one cell in the column is displayed as a link to a foreign key row.

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

``filter_description_en`` - ``str``
    Human readable description of the filters, search and ``?_where=`` clauses applied to the table, for example ``where state = "CA"``, or an empty string if there are none.

``filters`` - ``Filters``
    ``Filters`` object used by the HTML table interface. Useful methods include ``filters.human_description_en()``; this is not JSON serializable.

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
    Full URL for the next page of results, or None if there are no more pages. See :ref:`json_api_pagination`.

``ok`` - ``bool``
    True if the data for this page was retrieved without errors

``page_position`` - ``dict``
    On pages after the first (``?_next=`` is set) a dictionary with ``first_url`` (this page without ``?_next=``), ``count_before_url`` (POST to count the rows before this page), ``after_column`` and ``after_value`` (a description of the pagination cursor). ``None`` on the first page.

``primary_keys`` - ``list``
    List of primary key column names for this table, or an empty list if the table has no explicit primary key.

``private`` - ``bool``
    Whether this resource is private to the current actor

``query`` - ``dict``
    Details of the underlying SQL query as a dictionary with ``sql`` and ``params`` keys.

``query_ms`` - ``float``
    Time taken by the SQL queries for this page, in milliseconds

``renderer_options`` - ``dict``
    Dictionary mapping output format names to the ``label``, ``description``, ``is_binary`` and ``is_webpage`` options their :ref:`plugin_register_output_renderer` plugin hook set, used by the export dialog.

``renderers`` - ``dict``
    Dictionary mapping output format names such as ``json`` or plugin-provided renderer names to URLs for this data in that format.

``row_mutation_ui`` - ``bool``
    True if the current actor can edit or delete rows in this table, which enables the row edit/delete JavaScript UI

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

``sort_description_en`` - ``str``
    Human readable description of the sort order, for example ``sorted by name descending``, or an empty string if no sort order was specified.

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
    JSON data used by JavaScript on the table page. Includes ``database``, ``table`` and ``tableUrl``, plus optional ``foreignKeys`` mapping column names to autocomplete URLs, optional ``insertRow`` data, optional ``alterTable`` data and optional ``executeSql`` data with ``queryUrl`` and ``editorUrl`` keys, present if the current actor can execute custom SQL against this database. ``executeSql`` also has a ``columns`` list of every column in the table with its ``name`` and declared ``type``, and an optional ``filter`` key with the ``where`` clause, ``params`` and human readable ``description`` of the filters currently applied to the table.

``top_table`` - ``callable``
    Async callable that renders the ``top_table`` plugin slot for this table or view and returns HTML.

``url_csv`` - ``str``
    URL for the CSV export of this page

``url_csv_hidden_args`` - ``list``
    List of ``(name, value)`` pairs for hidden form fields used by the CSV export form, preserving current filters and any ``?_next=`` pagination cursor while forcing ``_size=max``.

``url_csv_path`` - ``str``
    Path portion of the CSV export URL

``url_tsv_path`` - ``str``
    Path portion of the TSV export URL, which takes the same arguments as CSV, or None if a plugin provides the ``tsv`` format

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
    Column metadata used by the HTML table display. Each item includes ``name``, ``sortable``, ``is_pk``, ``type``, ``notnull``, ``description``, ``column_type``, ``column_type_config`` and ``has_fks`` keys. ``has_fks`` is true when at least one cell in the column is displayed as a link to a foreign key row.

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

``renderer_options`` - ``dict``
    Dictionary mapping output format names to the ``label``, ``description``, ``is_binary`` and ``is_webpage`` options their :ref:`plugin_register_output_renderer` plugin hook set, used by the export dialog.

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
    List of ``(name, value)`` pairs for hidden form fields used by the CSV export form, preserving current options.

``url_csv_path`` - ``str``
    Path portion of the CSV export URL

``url_tsv_path`` - ``str``
    Path portion of the TSV export URL, which takes the same arguments as CSV, or None if a plugin provides the ``tsv`` format

Query list page
---------------

The list of stored queries, at ``/-/queries`` for every database and ``/<database>/-/queries`` for one database. Rendered using the ``query_list.html`` template.

``actor_id`` - ``str | None``
    ID of the current actor, or None

``all_queries_href`` - ``str | None``
    URL of the list covering every database, keeping the current search and filters

``alternate_url_json`` - ``str``
    URL of the JSON version of this page

``database`` - ``str | None``
    Name of the database the list is for, or None for the list covering every database

``database_chips`` - ``list[dict]``
    Links for switching between databases, each with ``name``, ``count``, ``color``, ``selected`` and ``href`` keys. Empty if fewer than two databases have queries

``database_color`` - ``str | None``
    Color assigned to the database, or None

``facets`` - ``list[dict]``
    Filters shown next to the list. Each has a ``title`` and a list of ``items``, each with ``label``, ``count``, ``href`` (to toggle the filter, or None) and ``active`` keys

``filters`` - ``dict``
    The current values of the ``q``, ``is_write``, ``is_private``, ``source`` and ``owner_id`` query string arguments, as strings that are empty if not set

``limit`` - ``int``
    Maximum number of queries per page

``next`` - ``str | None``
    Pagination token for the next page, or None

``next_url`` - ``str | None``
    URL of the next page of queries, or None

``ok`` - ``bool``
    Always ``True``

``queries`` - ``list[StoredQuery]``
    The stored queries on this page that the actor can see. Each has ``database``, ``name``, ``sql``, ``title``, ``description``, ``description_html``, ``parameters``, ``is_write``, ``is_private``, ``is_trusted``, ``source`` and ``owner_id`` attributes

``query_list_path`` - ``str``
    Path to this list without its query string, used as the action for the search form

``query_parameters`` - ``dict``
    Dictionary mapping ``(database, name)`` tuples to the list of named parameters for that query

``select_templates`` - ``list[str]``
    The template names Datasette considered, with an asterisk next to the one that was used

``show_database`` - ``bool``
    True if the list covers every database, so each query should be shown with its database

``show_private_note`` - ``bool``
    True if any query on this page is private

``show_trusted_note`` - ``bool``
    True if any query on this page is trusted, meaning it skips the permission checks for the SQL it runs

``start`` - ``int``
    Number of queries on earlier pages, used to display the position of this page

``total`` - ``int``
    Number of queries matching the current search and filters, across all pages

Query create page
-----------------

The form for saving a new stored query, at ``/<database>/-/queries/store``. Rendered using the ``query_create.html`` template.

``analysis_error`` - ``str | None``
    Error message if the SQL could not be analyzed, for example because it is not valid, otherwise None

``analysis_is_write`` - ``bool``
    True if the SQL writes to the database

``analysis_rows`` - ``list[dict]``
    The operations the SQL would perform, one per table it reads or writes. Each has ``operation``, ``database``, ``table``, ``required_permission``, ``message``, ``source`` and ``allowed`` keys. ``allowed`` is False if the actor may not perform that operation, and None for operations that need no permission check

``database`` - ``str``
    Database name

``database_color`` - ``str``
    Color assigned to the database

``description`` - ``str``
    The description entered for the query

``has_sql`` - ``bool``
    True if any SQL has been entered

``is_private`` - ``bool``
    True if the private checkbox should be checked

``name`` - ``str``
    The name entered for the query, used in its URL

``ok`` - ``bool``
    True if the SQL was analyzed without an error

``parameters`` - ``list[str]``
    Names of the ``:named`` parameters found in the SQL

``save_disabled`` - ``bool``
    True if the query cannot be saved, because there is no SQL, it could not be analyzed or the actor lacks a permission it needs

``select_templates`` - ``list[str]``
    The template names Datasette considered, with an asterisk next to the one that was used

``sql`` - ``str``
    The SQL for the query

``title`` - ``str``
    The title entered for the query

Query edit page
---------------

The form for editing a stored query, at ``/<database>/<query>/-/edit``. Rendered using the ``query_edit.html`` template.

``analysis_error`` - ``str | None``
    Error message if the SQL could not be analyzed, for example because it is not valid, otherwise None

``analysis_is_write`` - ``bool``
    True if the SQL writes to the database

``analysis_rows`` - ``list[dict]``
    The operations the SQL would perform, one per table it reads or writes. Each has ``operation``, ``database``, ``table``, ``required_permission``, ``message``, ``source`` and ``allowed`` keys. ``allowed`` is False if the actor may not perform that operation, and None for operations that need no permission check

``database`` - ``str``
    Database name

``database_color`` - ``str``
    Color assigned to the database

``description`` - ``str``
    The description of the query, or an empty string

``has_sql`` - ``bool``
    True if any SQL has been entered

``is_private`` - ``bool``
    True if the private checkbox should be checked

``name`` - ``str``
    Name of the query being edited

``ok`` - ``bool``
    True if the SQL was analyzed without an error

``parameters`` - ``list[str]``
    Names of the ``:named`` parameters found in the SQL

``query_url`` - ``str``
    URL of the page for the query

``save_disabled`` - ``bool``
    True if the query cannot be saved, because there is no SQL, it could not be analyzed or the actor lacks a permission it needs

``select_templates`` - ``list[str]``
    The template names Datasette considered, with an asterisk next to the one that was used

``sql`` - ``str``
    The SQL for the query

``title`` - ``str``
    The title of the query, or an empty string

Query delete page
-----------------

The page asking for confirmation before deleting a stored query, at ``/<database>/<query>/-/delete``. Rendered using the ``query_delete.html`` template.

``database`` - ``str``
    Database name

``database_color`` - ``str``
    Color assigned to the database

``query`` - ``dict``
    The query to be deleted, a dictionary with keys including ``name``, ``sql``, ``title``, ``description``, ``is_write``, ``is_private`` and ``owner_id``

``query_url`` - ``str``
    URL of the page for the query

``select_templates`` - ``list[str]``
    The template names Datasette considered, with an asterisk next to the one that was used

Execute write page
------------------

The form for running SQL that writes to a database, at ``/<database>/-/execute-write``. Rendered using the ``execute_write.html`` template.

``analysis_error`` - ``str | None``
    Error message if the SQL could not be analyzed or is not a write, otherwise None

``analysis_rows`` - ``list[dict]``
    The operations the SQL would perform, one per table it reads or writes. Each has ``operation``, ``database``, ``table``, ``required_permission``, ``message``, ``source`` and ``allowed`` keys. ``allowed`` is False if the actor may not perform that operation

``database`` - ``str``
    Database name

``database_color`` - ``str``
    Color assigned to the database

``execute_disabled`` - ``bool``
    True if the SQL cannot be executed as it stands

``execute_disabled_reason`` - ``str | None``
    Why the SQL cannot be executed, or None if it can or if that reason is already displayed as the error for this request

``execute_write_columns`` - ``list[str]``
    Names of the columns of the returned rows

``execute_write_display_rows`` - ``list``
    The returned rows, formatted for display in the same way as ``display_rows`` on the query page

``execute_write_returns_rows`` - ``bool``
    True if the executed SQL returned rows, for example by using ``RETURNING``

``execute_write_truncated`` - ``bool``
    True if more rows were returned than are displayed

``execution_links`` - ``list[dict]``
    Links to show after the SQL was executed, such as to a row that was inserted. Each has ``href`` and ``label`` keys

``execution_message`` - ``str | None``
    Message describing the result of executing the SQL, or None if nothing has been executed

``execution_ok`` - ``bool | None``
    True if the SQL was executed, False if it failed and None if nothing has been executed

``parameter_names`` - ``list[str]``
    Names of the ``:named`` parameters found in the SQL

``parameter_values`` - ``dict``
    Dictionary mapping parameter names to the values that were submitted for them

``save_query_base_url`` - ``str | None``
    URL of the form for saving a stored query without any SQL filled in, or None if the actor cannot save queries

``save_query_url`` - ``str | None``
    URL for saving this SQL as a stored query, or None if the actor cannot do that or the SQL cannot be executed

``select_templates`` - ``list[str]``
    The template names Datasette considered, with an asterisk next to the one that was used

``sql`` - ``str``
    The SQL in the editor

``sql_parameter_name_prefix`` - ``str``
    Prefix added to the name of each parameter to make the name of its form field

``table_columns`` - ``dict``
    Dictionary mapping the name of each table and view in the database to a list of its column names, used for autocomplete in the editor

``write_create_table_template_sql`` - ``str | None``
    Example ``CREATE TABLE`` SQL, or None if the actor cannot create tables

``write_template_operations`` - ``list[dict]``
    The operations that have example SQL for at least one table, each with ``name`` and ``label`` keys

``write_template_tables`` - ``dict``
    Tables the actor can write to. Maps each table name to a dictionary with a ``templates`` key, itself a dictionary mapping an operation such as ``insert`` to example SQL for that operation

Schema page
-----------

The schema pages: ``/-/schema`` for every database, ``/<database>/-/schema`` for one database and ``/<database>/<table>/-/schema`` for one table. Rendered using the ``schema.html`` template.

``is_instance`` - ``bool``
    True on the page showing the schema of every database

``schemas`` - ``list[dict]``
    One dictionary per database, with ``database`` (its name) and ``schema`` (the SQL that creates the tables and views the actor can see). The table page has a single item holding the SQL for just that table

``select_templates`` - ``list[str]``
    The template names Datasette considered, with an asterisk next to the one that was used

``table_name`` - ``str | None``
    Name of the table on the page for a single table, or None

Create token page
-----------------

The form for creating an API token, at ``/-/create-token``. Rendered using the ``create_token.html`` template.

``all_actions`` - ``list[str]``
    Names of every action, which the token can be restricted to across the whole instance

``child_actions`` - ``list[str]``
    Names of the actions that can be restricted to a specific table or other resource in a database

``database_actions`` - ``list[str]``
    Names of the actions that can be restricted to a specific database

``database_with_tables`` - ``list[dict]``
    The databases the actor can see. Each has ``name``, ``encoded`` (the name encoded for use in a form field) and ``tables``, a list of dictionaries with ``name`` and ``encoded`` keys

``errors`` - ``list[str]``
    Error messages from validating the submitted form

``select_templates`` - ``list[str]``
    The template names Datasette considered, with an asterisk next to the one that was used

``token`` - ``str | None``
    The token that was created, or None if the form has not been successfully submitted

``token_bits`` - ``dict | None``
    The decoded contents of the token that was created, or None

API explorer page
-----------------

The API explorer, a tool for trying out the JSON API, at ``/-/api``. Rendered using the ``api_explorer.html`` template.

``api_path`` - ``Callable``
    Function that takes one of those links and returns the URL of the API explorer with the form filled in for it

``example_links`` - ``list[dict]``
    Example API calls for the databases the actor can see. Each database has ``name``, ``links`` and ``tables`` keys, and each table has ``name`` and ``links``. A link has ``label``, ``method`` and ``path`` keys, plus ``json`` holding an example request body for write APIs

``private`` - ``bool``
    True if anonymous users cannot view this instance

``select_templates`` - ``list[str]``
    The template names Datasette considered, with an asterisk next to the one that was used

Logout page
-----------

The page asking the actor to confirm that they want to log out, at ``/-/logout``. It has no variables of its own - the actor is the ``actor`` variable available on every page. Rendered using the ``logout.html`` template.

``select_templates`` - ``list[str]``
    The template names Datasette considered, with an asterisk next to the one that was used

JSON data pages
---------------

The pages that display JSON describing the instance, such as ``/-/versions``, ``/-/plugins``, ``/-/settings``, ``/-/config``, ``/-/databases``, ``/-/threads`` and ``/-/actor``. ``/-/actions`` uses the same variables with the ``debug_actions.html`` template. Rendered using the ``show_json.html`` template.

``alternate_url_json`` - ``str``
    URL of the JSON version of this page

``data`` - ``dict | list``
    The data for this page, as returned by its JSON API

``data_json`` - ``str``
    That data as a string of indented JSON

``filename`` - ``str``
    Name of the JSON file for this page, such as ``versions.json``, used as the page title

``has_debug_permission`` - ``bool``
    True if this page requires the ``permissions-debug`` permission, in which case links to the other permission debugging tools are shown

``select_templates`` - ``list[str]``
    The template names Datasette considered, with an asterisk next to the one that was used

Autocomplete debug page
-----------------------

A tool for trying out the autocomplete for the rows of a table, at ``/-/debug/autocomplete``. Rendered using the ``debug_autocomplete.html`` template.

``autocomplete_url`` - ``str | None``
    URL of the autocomplete API for the table, or None if no table has been selected

``database_name`` - ``str | None``
    Name of the database from ``?database=``, or None

``error`` - ``str | None``
    Error message if the database or table could not be found, otherwise None

``label_column`` - ``str | None``
    The label column for the table, which autocomplete searches against, or None

``reached_scan_limit`` - ``bool``
    True if the search for suggestions stopped because it reached its limit of 100 tables

``scanned`` - ``int``
    Number of tables examined while looking for suggestions

``select_templates`` - ``list[str]``
    The template names Datasette considered, with an asterisk next to the one that was used

``suggestions`` - ``list[dict]``
    Up to five tables with a label column to try, shown if no table has been selected. Each has ``database``, ``table``, ``label_column`` and ``url`` keys

``table_name`` - ``str | None``
    Name of the table from ``?table=``, or None

Permissions debug page
----------------------

The permissions playground at ``/-/permissions``, which lists recent permission checks and has a form for testing a permission check against any actor. Rendered using the ``debug_permissions_playground.html`` template.

``filter`` - ``str``
    Which checks are listed, from ``?filter=``: ``all``, ``exclude-yours`` or ``only-yours``

``has_debug_permission`` - ``bool``
    True if the actor has the ``permissions-debug`` permission, in which case links to the other permission debugging tools are shown

``permission_checks`` - ``list``
    Recent permission checks, most recent first. Each has ``when``, ``actor``, ``action``, ``parent``, ``child`` and ``result`` attributes. ``result`` is None if no rule had an opinion

``permissions`` - ``list[dict]``
    Every registered action, each with ``name``, ``abbr``, ``description``, ``takes_parent`` and ``takes_child`` keys

``select_templates`` - ``list[str]``
    The template names Datasette considered, with an asterisk next to the one that was used

Allowed resources page
----------------------

A tool for listing the resources the current actor can access for a given action, at ``/-/allowed``. Rendered using the ``debug_allowed.html`` template.

``has_debug_permission`` - ``bool``
    True if the actor has the ``permissions-debug`` permission, in which case links to the other permission debugging tools are shown

``select_templates`` - ``list[str]``
    The template names Datasette considered, with an asterisk next to the one that was used

``supported_actions`` - ``list[str]``
    Names of every registered action, with the most commonly used ones first

Permission rules page
---------------------

A tool for viewing the permission rules that apply to the current actor for a given action, at ``/-/rules``. Rendered using the ``debug_rules.html`` template.

``has_debug_permission`` - ``bool``
    True if the actor has the ``permissions-debug`` permission, in which case links to the other permission debugging tools are shown

``select_templates`` - ``list[str]``
    The template names Datasette considered, with an asterisk next to the one that was used

``sorted_actions`` - ``list[str]``
    Names of every registered action, in alphabetical order

Permission check page
---------------------

A tool for testing whether an actor can perform an action against a resource, with an explanation of the result, at ``/-/check``. Rendered using the ``debug_check.html`` template.

``actions`` - ``list[dict]``
    Every registered action in alphabetical order, each with ``name``, ``description``, ``takes_parent``, ``takes_child`` and ``also_requires`` keys

``actor_json`` - ``str``
    JSON for the actor to test, from ``?actor=`` or otherwise the current actor

``has_debug_permission`` - ``bool``
    True if the actor has the ``permissions-debug`` permission, in which case links to the other permission debugging tools are shown

``select_templates`` - ``list[str]``
    The template names Datasette considered, with an asterisk next to the one that was used

Allow debug page
----------------

A tool for testing an actor against an ``allow`` block, at ``/-/allow-debug``. Rendered using the ``allow_debug.html`` template.

``actor_input`` - ``str``
    JSON for the actor to test, from ``?actor=``

``allow_input`` - ``str``
    JSON for the allow block to test, from ``?allow=``

``error`` - ``str``
    Error messages for input that was not valid JSON, or an empty string

``has_debug_permission`` - ``bool``
    True if the actor has the ``permissions-debug`` permission, in which case links to the other permission debugging tools are shown

``result`` - ``str | None``
    ``"True"`` if the actor matches the allow block, ``"False"`` if it does not, or None if either input was not valid JSON

``select_templates`` - ``list[str]``
    The template names Datasette considered, with an asterisk next to the one that was used

Messages debug page
-------------------

A tool for setting a message to be shown on the next page, at ``/-/messages``. It has no variables of its own. Rendered using the ``messages_debug.html`` template.

``select_templates`` - ``list[str]``
    The template names Datasette considered, with an asterisk next to the one that was used

Error page
----------

The page shown for an error, such as a 404 or a 500. Datasette looks for a template named after the status code first, so ``404.html`` can be used to customize just that error. Rendered using the ``error.html`` template.

Error pages other than the 403 permission denied page are rendered without most of the variables that are available on every page - only ``urls`` and ``menu_links`` are provided. Errors raised by plugins can include additional variables of their own.

``error`` - ``str``
    The error message

``ok`` - ``bool``
    Always ``False``

``status`` - ``int``
    The HTTP status code, for example ``404``

``title`` - ``str | None``
    Title for the page, or None to use the default of ``Error`` followed by the status code

CSRF error page
---------------

The page shown when a form submission is rejected by Datasette's CSRF protection. Rendered using the ``csrf_error.html`` template.

``reason`` - ``str``
    Technical explanation of why the request was rejected, intended for developers

.. [[[end]]]
