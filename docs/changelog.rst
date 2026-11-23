.. _changelog:

=========
Changelog
=========

.. _unreleased:

Unreleased
----------

- Arbitrary SQL queries can now use ``explain`` and ``explain query plan`` with ``insert``, ``update``, ``delete`` and ``replace`` statements.
- The SQL editor and SQL formatter ``<script>`` tags now carry ``data-cfasync="false"``, so Cloudflare's Rocket Loader no longer breaks the editor and "Format SQL" on instances proxied by Cloudflare. (:issue:`1969`)

.. _v1_0_a44:

1.0a44 (2026-11-22)
-------------------

This alpha settles a great deal of the outstanding JSON and internal APIs needed prior to the stable 1.0 release - see the `1.0 milestone <https://github.com/simonw/datasette/milestone/37>`__ for current progress.

Queries now return ``datasette.Row`` objects in place of ``sqlite3.Row``. The ``path_with_*()`` helpers have been replaced by a new ``datasette.urls.modify()`` method. Datasette now handles null primary keys (allowed by SQLite) correctly.

The :ref:`upgrade guide <upgrade_guide_v1_rows>` describes how to update plugins for the backwards incompatible changes in this release.

Security fixes
~~~~~~~~~~~~~~

- Fixed a security issue where the :ref:`/-/config <JsonDataView_config>` introspection endpoint showed anonymous visitors the ``allow`` blocks, the names of private tables and the name and SQL of private stored queries defined in configuration, on an instance that was otherwise public. **Backwards incompatible:** the endpoint now requires the ``permissions-debug`` permission, which by default is only granted to the :ref:`root user <authentication_root>`. It previously needed only ``view-instance``.
- Fixed a security issue where a SQL parameter returned by a :ref:`permission_resources_sql() <plugin_hook_permission_resources_sql>` plugin could be replaced by a value from Datasette, or from another plugin, that used the same parameter name. A plugin with a parameter called ``:database``, ``:q``, ``:pattern``, ``:allowed_dbs`` or ``:limit`` had it overwritten on the homepage and database page, which could list the names, columns and row counts of tables the visitor was not allowed to view. Row data was not exposed. Datasette now renames the parameters of each ``PermissionSQL`` before combining them, so plugins no longer need to choose names that are unique.
- Fixed a security issue where a stored query could be given a name ending in a newline, such as ``"dogs\n"``, which got past the check that a query name does not conflict with a table or view. Those names are now rejected.

Features
~~~~~~~~

- Datasette now handles rows with a primary key that is ``null``, an empty string or a binary value, and primary key columns with no declared type. (:issue:`2145`, :issue:`2805`, :issue:`2419`, :pr:`3112`)

  - A row with an empty string primary key has a row page at ``/database/table/$empty``, with working edit and delete buttons.
  - A row with a binary primary key has a row page at a URL such as ``/database/table/b:0102``. Binary keys are displayed as ``binary 0102``.
  - In a primary key column with no declared type the integer ``1`` and the text ``"1"`` are different rows. They now have different URLs, ``/database/table/i:1`` and ``/database/table/1``, where previously the integer row could not be reached.
  - A row with a ``null`` primary key cannot be told apart from other rows, so it no longer has a link, a row page or edit and delete buttons. It previously linked to ``/database/table/None``, where the delete button could delete a different row with the text ``None`` as its key.
  - Pagination now works for tables with any of these keys. Following ``next_url`` through a table with a binary primary key previously returned the same page for ever.
  - Foreign key links to these rows, and the links in the edit row dialog, use the same row URLs. The :ref:`autocomplete API <TableAutocompleteView>` returns a new ``"path"`` key for each row, the last part of the URL to its row page, or ``null`` for a row with a null primary key.
  - New ``primary_key_edge_cases``, ``compound_primary_key_edge_cases`` and ``primary_key_edge_case_references`` tables in the fixtures database demonstrate these.

- New ``datasette.urls.modify(target, add=, replace=, remove=, format=)`` method for building links that change the query string of the current page or of any other path. It applies ``base_url`` for you and is available in templates as ``urls.modify()``. See :ref:`internals_datasette_urls_modify`. (:issue:`1342`)
- ``datasette.urls.row()`` and ``datasette.urls.row_blob()`` now accept a list or tuple of primary key values, which are tilde-encoded and joined with commas. A string is still used exactly as it is given. See :ref:`internals_datasette_urls`. (:issue:`1048`)
- The following ``datasette.utils`` functions and classes are now :ref:`documented <internals_utils>` and considered stable for use by plugins: ``escape_sqlite()``, ``escape_fts()``, ``to_css_class()``, ``modify_query_string()``, ``actor_matches_allow()``, ``add_cors_headers()``, ``StartupError``, ``BadMetadataError``, ``MultiParams``, ``PrefixedUrlString``, ``PaginatedResources`` and the ``sqlite3`` module. (:issue:`1630`)
- The ``custom_time_limit=`` argument to :ref:`db.execute() <database_execute>` can now set a time limit that is longer than the :ref:`setting_sql_time_limit_ms` setting. Previously a longer limit was ignored. The ``?_timelimit=`` query string argument can still only shorten the limit, and :ref:`setting_facet_time_limit_ms` and :ref:`setting_facet_suggest_time_limit_ms` are still capped by ``sql_time_limit_ms``. Plugins can apply the same cap using the new :ref:`datasette.capped_time_limit_ms() <datasette_capped_time_limit_ms>` method.
- The ``?_context=1`` template debug page, enabled by the :ref:`setting_template_debug` setting, now shows each variable passed to the template with its documentation, its current value and the ``{{ variable }}`` syntax for using it, with a copy button. Functions are shown with their arguments and docstring. Variables are grouped into those documented for the page, undocumented variables, variables added by plugins and variables available on every page.
- The :ref:`template context documentation <template_context>` now covers the stored query pages, the execute write SQL page, the schema pages, the create token page, the API explorer, the logout page, the error pages and the pages that display JSON such as ``/-/versions``, in addition to the homepage, database, table, row and query pages. Each of these is now rendered using a documented dataclass. The variables for the permission and other debugging tools are documented as well, in a separate section that is not covered by the promise of stability.
- New ``DatasetteModal.alert()`` JavaScript method for showing a message using an in-page dialog, returning a promise that resolves when it is dismissed. Datasette now uses this in place of the browser's ``alert()``, which embedded browsers can dismiss without displaying. See :ref:`javascript_plugins_modal_alert`.
- The tabs for panels added by JavaScript plugins using :ref:`makeAboveTablePanelConfigs() <javascript_plugins_makeAboveTablePanelConfigs>` now show which panel is currently selected. They are also now accessible tabs: the selected state is exposed to assistive technology and the arrow, ``Home`` and ``End`` keys move between them. (:issue:`2253`)
- ``?_stream=1`` now fetches rows in pages of :ref:`setting_max_returned_rows`, 1,000 by default, instead of the default page size of 100. This makes streaming every row of a large table several times faster. A ``?_size=`` in the URL is still respected. If a page of that size takes longer than :ref:`setting_sql_time_limit_ms`, it is fetched again using the default page size, which is then used for the rest of the stream. (:pr:`3070`, :pr:`3092`)
- The "Export data" dialog now groups plugin formats that can stream with TSV and CSV, so its "Include all rows" option applies to those formats too.
- Datasette now remembers the columns, primary keys, foreign keys, label column and full-text search table of each table until the schema of its database changes. Loading a table page previously looked up the columns of that table nine times. (:pr:`3102`)
- The query page now repeats its notice that the results have been truncated below the table of results, where previously it was only shown above the SQL editor.
- The link to download a SQLite database from the database page is now a button showing the size of the file.
- File sizes shown on the database page and in the tooltips for binary values now use decimal units, where 1 MB is 1,000,000 bytes, matching the sizes already shown on the homepage. They previously used 1 MB to mean 1,048,576 bytes, so a 5,242,880 byte database that was displayed as ``5.0 MB`` is now displayed as ``5.2 MB``.
- CSV, TSV, JSON and other exports of a custom SQL query or a stored query now include a ``Datasette-Truncated: true`` or ``Datasette-Truncated: false`` HTTP header, so code paging through the results of a query can tell whether rows were left out because of :ref:`setting_max_returned_rows`. Truncated responses also include a ``Datasette-Max-Returned-Rows`` header with the limit that was applied. Both headers are listed in ``Access-Control-Expose-Headers`` for ``--cors``. See :ref:`csv_export_truncated`. (:issue:`1553`)

Rows and columns
~~~~~~~~~~~~~~~~

- **Backwards incompatible:** rows returned by :ref:`db.execute() <database_execute>` are now :ref:`datasette.Row <internals_row>` objects instead of ``sqlite3.Row`` objects. So are rows fetched from the connection passed to a :ref:`db.execute_fn() <database_execute_fn>` or :ref:`db.execute_write_fn() <database_execute_write_fn>` function. ``Row`` is designed to work for database backends other than SQLite. See the :ref:`upgrade guide <upgrade_guide_v1_rows>` for how to update plugins. ``row["name"]``, ``row[0]``, ``row.keys()``, ``dict(row)``, ``tuple(row)`` and iterating over a row all continue to work. Code will need to change if it uses ``isinstance(row, sqlite3.Row)``, looks up columns using a different case from the one in the query, or catches ``IndexError`` for a missing column name - that is now a ``KeyError``. (:issue:`1973`)
- **Backwards incompatible:** ``results.columns`` is now a :ref:`Columns <internals_row_columns>` object describing each column, shared by every row as ``row.columns``. Use ``results.columns.names`` for the column names. On table and row pages each ``Column`` includes the details of that column in its table, such as the type it was declared with.
- **Backwards incompatible:** :ref:`db.table_column_details() <database_table_column_details>` now returns a :ref:`Columns <internals_row_columns>` object of ``datasette.Column`` objects, the same class used for the columns of a row. It previously returned a list of ``datasette.utils.Column`` named tuples, and that class has been removed. The columns can no longer be unpacked or indexed as tuples. ``is_pk`` is now ``pk_position``, ``type`` is ``None`` and not an empty string for a column declared without a type, ``hidden`` is a boolean with generated columns described by a new ``generated`` attribute, and ``cid`` has been removed. See :ref:`the upgrade guide <upgrade_guide_v1_table_column_details>`. (:issue:`3100`)
- **Backwards incompatible:** the :ref:`render_cell() <plugin_hook_render_cell>` plugin hook, and the ``render_cell()`` method of :ref:`column types <datasette_column_types>`, are now always passed the value stored in the database. Previously the ``value`` for a foreign key column was sometimes a ``{"value": ..., "label": ...}`` dictionary, depending on the page, the query string and the data. The ``row`` passed to the hook is now a ``datasette.Row`` on every page, where previously it was either a ``sqlite3.Row`` or an undocumented ``CustomRow``. Labels for foreign keys are available in the new ``row.labels`` dictionary. If a column is a foreign key and also has a column type assigned, the column type now renders the cell. Previously the cell showed a link to the referenced row, because the column type was passed the label dictionary and did not recognize it. (:issue:`1973`)
- **Backwards incompatible:** the ``rows`` passed to :ref:`output renderers <plugin_register_output_renderer>` are ``datasette.Row`` objects. With ``?_labels=on`` they previously contained ``{"value": ..., "label": ...}`` dictionaries - the labels are now in ``row.labels``. The :ref:`row_actions() <plugin_hook_row_actions>` and :ref:`top_row() <plugin_hook_top_row>` hooks are passed a ``datasette.Row`` too.
- **Backwards incompatible:** the ``row`` passed to ``render_cell()`` on the row page now has its values in the same order as on the table page. Previously the primary key columns were moved to the start, so ``row[0]`` could mean something different on the two pages. The row page still displays primary keys first.
- **Backwards incompatible:** ``?_labels=on`` no longer expands a foreign key that refers to a table with no :ref:`label column <table_configuration_label_column>`. These were previously returned as ``{"value": 1, "label": "1"}``, repeating the value as its own label, and are now returned as the plain value. The column is no longer listed in ``expanded_columns`` or given a ``column_label`` column in CSV exports. The HTML table still links the value to the row it refers to.
- **Backwards incompatible:** ``datasette.utils.CustomRow`` has been removed.

Breaking changes
~~~~~~~~~~~~~~~~

- **Backwards incompatible:** optional arguments to many of the :ref:`documented internal methods <internals>` are now keyword-only, so they can no longer be passed positionally. See :ref:`the upgrade guide <upgrade_guide_v1_keyword_only>`. (:issue:`1822`) The affected signatures are:

  - ``Datasette(files=None, *, ...)`` - every argument other than ``files`` is keyword-only.
  - ``datasette.render_template(templates, context=None, *, request=None, view_name=None)``
  - ``datasette.execute(db_name, sql, params=None, *, ...)`` and ``db.execute(sql, params=None, *, ...)``
  - ``db.execute_write(sql, params=None, *, ...)``, ``db.execute_write_script(sql, *, ...)``, ``db.execute_write_many(sql, params_seq, *, ...)`` and ``db.execute_write_fn(fn, *, ...)``
  - ``datasette.get_database(name=None, *, route=None)``, ``datasette.add_database(db, name=None, *, route=None)`` and ``datasette.add_memory_database(memory_name, name=None, *, route=None)``
  - ``Database(ds, path=None, *, ...)`` - every argument after ``path`` is keyword-only. The :ref:`documentation for this constructor <database_constructor>` previously left out its ``mode`` argument, so passing ``is_temp_disk`` by position following that documentation set ``mode`` instead. (:issue:`2942`)
  - ``datasette.plugin_config(plugin_name, *, database=None, table=None, fallback=True)``
  - ``datasette.set_actor_cookie(response, actor, *, expire_after=None)``
  - ``datasette.client.get(path, *, skip_permission_checks=False, **kwargs)`` and the other ``datasette.client`` methods.
  - ``datasette.urls.instance()``, ``.path()``, ``.database()``, ``.database_query()``, ``.table()``, ``.query()`` and ``.row()`` now require ``format=`` as a keyword.
  - ``Request.fake(path_with_query_string, *, method="GET", scheme="http", url_vars=None)``
  - ``Response(body=None, *, status=200, headers=None, content_type="text/plain")``
  - ``Response.html()``, ``.text()``, ``.json()``, ``.stream()``, ``.error()`` and ``.redirect()`` still accept ``status`` as a second positional argument - ``Response.json(data, 400)`` continues to work - but ``headers``, ``default`` and ``content_type`` are keyword-only.
  - ``response.set_cookie(key, value="", *, max_age=None, ...)``

- **Backwards incompatible:** ``datasette.utils.path_with_added_args()``, ``path_with_removed_args()``, ``path_with_replaced_args()``, ``path_with_format()``, ``path_without_format()`` and ``append_querystring()`` have been removed. Plugins should use ``datasette.urls.modify()`` instead: ``path_with_added_args(request, {"_facet": "x"})`` becomes ``datasette.urls.modify(request, add={"_facet": "x"})`` and ``path_with_format(request=request, format="json", extra_qs={"_shape": "array"})`` becomes ``datasette.urls.modify(request, format="json", add={"_shape": "array"})``. ``path_without_format(request, include_query_string=True)`` becomes ``datasette.urls.modify(request, format=False)``. Note that ``modify()`` applies ``base_url``, so its result should not have the prefix added again. See :ref:`the upgrade guide <upgrade_guide_v1_urls_modify>`. (:issue:`1342`)
- **Backwards incompatible:** the ``path_with_replaced_args``, ``fix_path`` and ``append_querystring`` functions are no longer passed to the table and query templates. Custom templates should use ``urls.modify()``: ``fix_path(path_with_replaced_args(request, {"_sort": "x"}))`` becomes ``urls.modify(request, replace={"_sort": "x"})`` and ``append_querystring(url, "_shape=array")`` becomes ``urls.modify(url, add={"_shape": "array"})``. ``fix_path(path)`` is the same as ``urls.path(path)``.
- **Backwards incompatible:** the ``format_bytes()`` function is no longer available in templates, and ``datasette.utils.format_bytes()`` has been removed. Custom templates that call ``{{ format_bytes(size) }}`` should use Jinja's built-in `filesizeformat <https://jinja.palletsprojects.com/en/stable/templates/#jinja-filters.filesizeformat>`__ filter instead: ``{{ size|filesizeformat }}``. See :ref:`the upgrade guide <upgrade_guide_v1_format_bytes>`. (:issue:`1841`)
- **Backwards incompatible:** the undocumented ``datasette.utils.testing`` module, which held the ``TestClient`` and ``TestResponse`` classes, has been removed. Tests should use :ref:`datasette.client <internals_datasette_client>` instead, as described in :ref:`testing_plugins`. See :ref:`the upgrade guide <upgrade_guide_v1_test_client>`. (:issue:`3074`)
- Datasette no longer depends on ``asgiref``. A plugin that imports it should list it as a dependency of its own. (:issue:`3074`)
- **Backwards incompatible:** the homepage JSON at ``/.json`` now returns the same searchable catalog of tables, views and stored queries as the :ref:`HTML homepage <IndexView>`, and accepts the same ``?q=``, ``?database=`` and ``?page=`` parameters. It previously returned up to five tables for each database in a format left over from the old homepage design, ignored those parameters and could take several seconds for instances with thousands of tables. Each database in ``databases`` now has ``name``, ``private``, ``tables``, ``views``, ``url`` and ``size`` keys - ``url`` replaces ``path`` - and tables and views are listed in a new ``items`` key, paginated using ``next_url``. This endpoint is still marked as unstable. (:issue:`1674`)
- **Backwards incompatible:** ``?_shape=object`` now returns a 400 error for a table with rows that have a ``null`` primary key, since those rows have no key to file them under. They were previously merged into one entry. The keys of the object are now the row paths used in row URLs, so in a primary key column with no declared type the integer ``5`` has the key ``"i:5"``. Tables with ordinary primary keys are not affected.
- **Backwards incompatible:** the ``primary_key_values`` variable passed to the row page template now holds the values decoded from the URL. A binary key is ``bytes``, and a number in a primary key column with no declared type is an ``int`` or ``float``. They were previously always strings. Use the new ``key_value_display`` template filter to display one: ``{{ value|key_value_display }}``. See :ref:`the upgrade guide <upgrade_guide_v1_primary_key_values>`.
- **Backwards incompatible:** the JSON that ``?_context=1`` previously returned has moved to ``?_context_json=1``.
- **Backwards incompatible:** removed template variables that were documented but not used by any of Datasette's default templates, so that they are not frozen by the :ref:`template context <template_context>` promise of stability. (:issue:`3088`) Custom templates that use them should switch to the alternatives listed here:

  - ``ok`` on the table, row, stored query list, save query, edit query and error pages. On the save query and edit query pages use ``analysis_error`` instead, which is ``None`` if the SQL was analyzed without an error.
  - ``path``, ``editable`` and ``queries_more`` on the database page. Use ``urls.database(database)`` for the path. ``editable`` was always true. ``queries_count`` is the number of stored queries, which can be compared to the length of ``queries``.
  - ``page_size`` on the homepage, which was always 30.
  - ``limit``, ``show_private_note``, ``show_trusted_note`` and ``show_database`` on the stored query list. ``show_database`` was the same as ``database is none``.
  - ``parameters`` on the save query and edit query pages.

  The ``url_csv``, ``count_sql``, ``rows``, ``columns``, ``next`` and ``base_url`` variables are also not used by the default templates, but have been kept because custom templates and plugins rely on them. The JSON API is unchanged, except that ``/-/queries.json`` and ``/<database>/-/queries.json`` no longer return the ``show_private_note`` and ``show_trusted_note`` keys.

Bug fixes
~~~~~~~~~

- A URL with a format extension that Datasette does not recognize, such as ``/database/table.blah`` or ``/database/-/query.blah``, now returns a 404. These previously returned a 500 error. (:issue:`1914`)
- ``PUT``, ``PATCH`` and ``DELETE`` requests to a table URL now return a 405 error. They were previously handled as if they were a ``GET``.
- ``405`` and ``403`` errors now decide whether to respond with JSON in the same way as other errors: for a ``.json`` URL, an ``Accept: application/json`` header or a JSON ``Content-Type`` header, including one with a ``charset`` parameter. A ``403`` for a ``.csv`` or ``.tsv`` URL is now plain text, not an HTML page.
- ``?column__in=`` and ``?column__notin=`` now return a 400 error if their JSON array is not valid JSON, or contains a nested list or object, an integer too large for SQLite or text that cannot be encoded as UTF-8. These previously returned a 500 error. Thanks, `goranai <https://github.com/goranai>`__. (:issue:`3090`, :pr:`3091`)
- ``datasette.urls.database_query(database, sql, format="json")`` and ``datasette.urls.path(path, format=...)`` now put the format extension before the query string. They previously added it to the end of the ``?sql=`` value.
- Fixed a bug where filtering a view by a column calculated using an expression, such as ``count(*)`` or ``sum(amount)``, returned no rows for a number: ``?total=5`` did not match rows where ``total`` was ``5``. Facet links for those columns led to empty pages for the same reason. The ``__exact``, ``__not``, ``__in`` and ``__notin`` filters now compare a value that looks like a number as both a number and text, for columns that have no declared type. This also applies to table columns declared without a type, where ``?column=1`` now matches the number ``1`` as well as the text ``"1"``. (:issue:`1671`, :issue:`2073`, :issue:`2713`)
- Fixed a bug where the foreign keys of a table could be reported as empty for a table that had just been created or altered by another connection, which hid foreign key links and labels for that table until the schema next changed. This was also the cause of intermittent failures in the write API tests. (:issue:`2356`, :issue:`1919`)
- Fixed a bug where the search box on the table page was empty after running a search, instead of showing the current ``?_search=`` term. The term is now available to the table template as ``search``.
- Fixed a bug where sort, facet and filter-removal links on the table page dropped query string arguments with blank values, so sorting ``/db/table?name=`` lost the filter for rows where ``name`` is the empty string.
- The "View and edit SQL" link on the table page now links straight to ``/database/-/query``, instead of to a URL that redirects there.
- ``?_context_json=1`` no longer returns a 500 error for pages such as ``/-/queries`` that have a value in their context which cannot be represented as JSON.
- A read query that fails part way through now has its cursor closed immediately. On Python 3.11 the open cursor could keep a named in-memory database alive after its ``Datasette`` instance was closed.
- Tables created using the :ref:`create table API <TableCreateView>` now have ``NOT NULL`` primary key columns, so they can no longer hold rows with a null primary key. This applies to text and compound primary keys, for tables created from ``columns`` and from example ``rows``. Inserting a row without a value for one of those keys now returns a 400 error. A single integer primary key is unchanged, since SQLite never allows it to be null. Thanks, `Pranav Mishra <https://github.com/PranavMishra28>`__. (:issue:`2807`, :pr:`2826`)
- ``datasette publish`` and ``datasette package`` no longer fail on Windows when removing their temporary directory. (:issue:`1479`)
- Datasette's test suite now runs on Windows in CI. (:issue:`1627`)
- Fixed a bug where a custom SQL query or stored query returned one row more than :ref:`setting_max_returned_rows` if that setting was the same as :ref:`setting_default_page_size`. Thanks, `kokokoXUY <https://github.com/kokokoXUY>`__, who spotted this in :pr:`2963`.
- Datasette no longer runs ``count(*)`` against SpatiaLite ``KNN`` and ``KNN2`` virtual tables. That query could run for minutes, ignoring the time limit. Those tables are now shown as having 0 rows. (:issue:`1611`)
- The :ref:`insert API <TableInsertView>` can now return the rows it inserted into a ``WITHOUT ROWID`` table. Inserting using ``"row"``, or using ``"rows"`` with ``"return": true``, previously failed with a ``no such column: rowid`` error. (:issue:`1873`, :pr:`3081`)
- Rows returned by the insert API for a table with no primary key now include the ``rowid`` of each row, matching the upsert API.
- The column menu on a view page now offers "Facet by this" for the first column, which was previously left out. Thanks, `Sebastian Cao <https://github.com/cycsmail>`__. (:issue:`2045`, :pr:`2810`)

.. _v1_0_a43:

1.0a43 (2026-11-15)
-------------------

This alpha includes security fixes for private stored queries and database downloads, streaming output renderers with CSV and TSV now implemented as renderers, a new JSON format for individual rows, and tables listed as cards on the homepage and database page.

See `0.65.6 <https://docs.datasette.io/en/stable/changelog.html#v0-65-6>`__ for the database download fix backported to the stable 0.65.x branch.

Security fixes
~~~~~~~~~~~~~~

- Fixed a security issue where the ``?owner_id=`` filter on the ``/-/queries`` and ``/<database>/-/queries`` pages, and their ``.json`` equivalents, returned private stored queries belonging to that owner - including their SQL - to any actor, including anonymous users. This affected Datasette 1.0a31 through 1.0a42.
- Fixed a security issue where a database registered using ``add_database(db, name=..., route=...)`` with a route that differs from its name could be downloaded from ``/<route>.db`` by actors who were denied access to it. Permission rules for the ``view-database-download`` action that were written against the route no longer apply, and should use the database name like rules for every other action.
- The example ``permission_resources_sql()`` hook in the :ref:`upgrade guide for 1.0a20 <upgrade_guide_v1_a20>` was missing its ``if action == "permissions-debug"`` check. A plugin that copied that example allowed every action, including write actions, for every actor including anonymous users. **If you used that example in your own plugin, add the missing check.**

Breaking changes
~~~~~~~~~~~~~~~~

- The JSON for an individual row, such as ``/database/table/1.json``, now returns ``{"row": {"id": 1, ...}}``. It previously returned ``{"rows": [{"id": 1, ...}]}``, a list that always contained exactly one row. Two other keys in that response changed to match:

  - ``"truncated"`` is no longer included. That key says whether a list of rows was cut short at the page size limit, so for a single row it was always ``false``.
  - ``?_extra=render_cell``, which returns the HTML that plugins rendered for each column, now returns ``"render_cell": {"column": "<html>"}``. It previously returned ``"render_cell": [{"column": "<html>"}]``, a list with one item for the one row.

  Add ``?_shape=objects`` to the URL to get the previous format back. The other ``?_shape=`` options work as they do for table JSON. See :ref:`json_api_row` and the :ref:`upgrade guide <upgrade_guide_v1_row_json>`. (:issue:`2612`, :issue:`2137`)
- Output renderer ``render`` functions must now return a :ref:`Response <internals_response>` object, or ``None``. Returning a dictionary with ``body``, ``content_type``, ``status_code`` and ``headers`` keys, deprecated since Datasette 0.49, is now an error. The ``"callback"`` key, the pre-0.49 name for ``"render"``, has also been removed. See :ref:`the upgrade guide <upgrade_guide_v1_renderer_response>`. (:issue:`954`)
- The ``view_name`` passed to plugin hooks for SQL query pages, both arbitrary SQL and stored queries, is now ``"query"``. Template hooks such as :ref:`plugin_hook_extra_template_vars` were previously passed ``"database"`` for those pages. Output renderers were passed ``"table"`` by ``render`` and ``"database"`` by ``can_render``. See :ref:`the upgrade guide <upgrade_guide_v1_view_name_query>`.
- An "include all rows" export that fails part of the way through, or that reaches the :ref:`setting_max_stream_mb` limit, now drops the connection so the download fails. Previously the error message was written as the last line of the file, which otherwise looked complete.
- The ``datasette_allow_facet`` template variable has been removed from the table page. Custom ``table.html`` templates that use it should use ``{{ settings.default_allow_facet|tojson }}`` instead.

Output renderers
~~~~~~~~~~~~~~~~

- New ``Response.stream(body)`` method for returning a response a chunk at a time from an async iterator of strings or bytes, for example from a :ref:`plugin_register_routes` view. See :ref:`internals_response_stream`.
- Output renderers can now stream every row of a table or view. A ``render`` function that accepts the new ``row_stream`` argument - an async iterator of rows - and returns ``Response.stream()`` will be sent every row when the URL includes ``?_stream=1``. See :ref:`plugin_register_output_renderer_streaming`. (:issue:`1101`)
- CSV and TSV export are now implemented as default output renderers using that mechanism, replacing the previous special case. (:issue:`1062`)
- Output renderers registered with :ref:`plugin_register_output_renderer` can now list the :ref:`extras <json_api_extra>` they need, such as ``count`` or ``human_description_en``, using a new ``"extras"`` key. Datasette calculates those and passes them to the ``render`` function in a new ``page`` argument, a dictionary which also includes ``next`` and ``next_url`` on table pages. (:issue:`2770`, :issue:`2186`)
- Output renderer ``render`` and ``can_render`` functions can now accept ``path`` and ``full_path`` arguments: the path to the HTML version of the page without the ``.extension``, with and without the query string. See :ref:`plugin_register_output_renderer`. (:issue:`1179`, :pr:`2987`)
- Output renderers whose ``render`` function accepts the undocumented ``data`` or ``args`` arguments now trigger a ``DeprecationWarning`` when Datasette starts. Those arguments will be removed before Datasette 1.0. Use the ``page`` argument in place of ``data``, and ``request.args`` in place of ``args``. (:issue:`2770`)
- The ``allow_csv_stream`` and ``max_csv_mb`` settings have been renamed to :ref:`setting_default_allow_stream` and :ref:`setting_max_stream_mb`, since they now apply to every format that can stream. The previous names continue to work.
- ``?_dl=1`` now adds a ``content-disposition: attachment`` header for every export format, not only CSV and TSV.
- ``csv`` and ``tsv`` are now included in the ``?_extra=renderers`` extra.

Features
~~~~~~~~

- Tables and views on the homepage and the database page are now listed as cards, each showing the row count, the column names and links to the tables it references. A "+N more" button reveals the rest of a table's columns. The database page now matches the design of the homepage. (:issue:`3067`)
- JSON objects and arrays on the row page have a new button for switching between the value as stored and a pretty-printed version. The copy button copies whichever is displayed.
- Filtering, faceting, sorting and paginating a table now crossfades the table and facets in place, in browsers that support CSS cross-document view transitions. Suggested facet links no longer jump to a ``#facet-...`` fragment. (:issue:`3061`)
- New ``<datasette-copy-button>`` custom element that plugins and custom templates can use to add a copy to clipboard button. Datasette now uses it for the schema on the table and schema pages, as well as for values on the row page. See :ref:`javascript_plugins_copy_button`. (:pr:`3063`)
- New ``DatasetteModal.confirm()`` JavaScript method for asking a question using an in-page dialog, returning a promise. See :ref:`javascript_plugins_modal_confirm`.
- The ``beforeClose`` guard for a :ref:`modal dialog <javascript_plugins_modals>` can now return a promise. The dialog stays open until that promise resolves.
- The list of columns in the Column statistics dialog now shows at most three rows of columns, with a "+N more" button to show the rest, and no longer truncates column names when there is room to show them. (:pr:`3064`)
- The select boxes used to filter a table now look the same across different browsers. (:issue:`3060`)
- New ``?_extra=expanded_columns`` extra for table JSON, listing the foreign key columns that were expanded using ``?_labels=on``.
- The total time spent calculating suggested facets for a page is now capped at ten times the :ref:`setting_facet_suggest_time_limit_ms` setting, so tables with a large number of columns can no longer spend several seconds suggesting facets. (:issue:`862`)
- The default :ref:`setting_max_post_body_bytes` limit has been increased from 2MB to 10MB (``10485760`` bytes), allowing larger JSON API requests.
- The ``allow_facet`` and ``allow_download`` settings have been renamed to :ref:`setting_default_allow_facet` and :ref:`setting_default_allow_download`, matching :ref:`setting_default_allow_sql`. The previous names continue to work. (:issue:`1410`)
- ``datasette publish`` and ``datasette package`` now install the same version of Datasette as the one used to run the command, unless ``--branch`` is used or an ``--install`` option names Datasette. Previously they installed the latest stable release, which does not accept the ``--config`` option.
- ``datasette publish cloudrun`` now deploys with a new ``content_length_workaround`` option enabled, which omits ``Content-Length`` from responses of 32,000,000 bytes or more because Cloud Run rejects larger responses that declare a length. See :ref:`publish_cloud_run_content_length`. (:issue:`749`)

Bug fixes
~~~~~~~~~

- Fixed a bug where following ``next_url`` through a sorted table with a text or compound primary key could repeat some rows and skip others, if several rows shared the same value in the sort column. Thanks, `Zihan Dai <https://github.com/PDGGK>`__. (:issue:`3054`, :pr:`3055`)
- Fixed a bug where following ``next_url`` through a table sorted by a column with no declared type, such as a column in a table created using ``CREATE TABLE ... AS SELECT``, could skip or repeat rows. The sort value in the ``next`` token was always compared as text, so numbers in those columns were compared against it incorrectly. The token now records whether the value is an integer, a floating point number or a BLOB. ``next`` tokens issued by previous versions are still accepted. Thanks, `Peter Olkhovets <https://github.com/peterolkhov>`__. (:issue:`3056`, :pr:`3057`)
- The ``gt``, ``gte``, ``lt`` and ``lte`` table filters no longer return a 500 error for an integer too large for SQLite's signed 64-bit integers, such as ``?id__gt=9223372036854775808``. Thanks, `Ilya Khalafi <https://github.com/IlyaKhalafi>`__. (:issue:`3035`, :pr:`3036`)
- The create table, alter table and row edit dialogs no longer use the browser's native ``confirm()`` prompt when discarding unsaved changes or dropping a table. Browsers embedded in other applications can dismiss those prompts without showing them, which left the "Drop table" button doing nothing and the create table dialog impossible to close.
- Database downloads now send a ``Content-Length`` header without also sending ``Transfer-Encoding: chunked``, so browsers can show download progress and clients that reject that invalid combination, such as Node.js, can download the file. (:issue:`2663`, :issue:`2317`)
- ``If-None-Match`` headers are now compared against the ``ETag`` for database downloads and static files using the weak comparison specified by HTTP: a ``W/`` prefix is ignored, the header can list several values and ``*`` matches anything. Proxies that compress responses often turn a strong ``ETag`` into a weak one, which previously caused a full download on every request. The ``304`` response for a database download now includes the ``ETag`` and CORS headers.
- A ``plugins`` block in metadata is now merged with the ``plugins`` block in configuration, instead of replacing it.
- :ref:`OpenTelemetry <internals_telemetry>` spans for requests made using ``datasette.client`` are now nested under the span that was active when the request was made, sharing its trace ID. Thanks, `Ankita Advitot <https://github.com/AnkitaAdvitot>`__. (:issue:`2969`, :pr:`2980`)
- Long values on the row page are no longer collapsed behind a "Show all 2,099 characters" toggle if plugin JavaScript has replaced the text with something else, such as a map. See :ref:`javascript_plugins_row_values`.
- Fixed a bug where links generated by output renderers using ``path_with_format()``, and the URLs returned by ``?_extra=renderers`` on ``.json`` pages, used a ``?_format=`` parameter that Datasette no longer reads.
- Plugins loaded using ``--plugins-dir`` now have ``__spec__`` and ``__loader__`` set on their module. Python 3.15 emitted a ``DeprecationWarning`` whenever an exception traceback passed through one of those plugins.
- Fixed a scrolling bug on the ``/-/queries`` page. (:issue:`3053`)
- Fixed a bug where a database, table or saved query with a name such as ``count``, ``wrapper`` or ``tag`` could break the layout of its pages, because the class added to the ``<body>`` element for that name matched a class used by Datasette's own CSS and JavaScript.

.. _v1_0_a42:

1.0a42 (2026-11-08)
-------------------

A redesigned homepage, redesigned table, row and query pages, a new "Export data" dialog with TSV support, a column statistics feature, and Python 3.11 is now the minimum supported version.

Python 3.11 or higher
~~~~~~~~~~~~~~~~~~~~~

- Datasette now requires Python 3.11 or higher. This allowed the removal of several compatibility shims. (:pr:`2998`)
- Datasette is now tested on Python 3.11, 3.12, 3.13, 3.14, and 3.15.

Export data dialog and TSV
~~~~~~~~~~~~~~~~~~~~~~~~~~

- Table, query and row pages replace the "This data as..." links and the "Advanced export" box with an **Export data** button. This opens a dialog listing every available format - TSV, CSV, JSON, JSON array, newline-delimited JSON, JSON object and any plugin formats - each with Copy, View, Download and Copy URL actions. Old ``#export`` links open the dialog. See :ref:`csv_export`. (:pr:`3037`)
- New :ref:`TSV export <csv_export_tsv>`, for pasting into Excel and Google Sheets. TSV uses the same streaming code, options and URL parameters as CSV, with Excel-compatible quoting.
- :ref:`register_output_renderer <plugin_register_output_renderer>` dictionaries can now include optional ``label`` and ``description`` keys, shown in the Export data dialog, plus ``is_binary`` and ``is_webpage`` flags that control which actions the dialog offers for that format.

Column statistics
~~~~~~~~~~~~~~~~~

- The column action menu has a new **Column statistics** item, available to actors with the ``execute-sql`` permission. It opens a dialog showing counts of rows, nulls and distinct values, a breakdown of storage types, numeric statistics with a histogram, box plot and percentiles, text length statistics, a chart of dates grouped by month, year or decade, and the most common values. Every query it runs is listed, with a link to open it in the SQL editor. (:pr:`3038`)
- Column statistics are also available for the results of arbitrary SQL queries and stored queries.

Table page redesign
~~~~~~~~~~~~~~~~~~~

- A refreshed design for tables on the table, row and query pages, with right-aligned numbers and shadows indicating horizontally scrollable content. (:pr:`3032`)
- Values taller than three lines are clamped to a preview with an "expand" button. Column headers offer an "expand all" toggle.
- A pin in the primary key column header freezes the header row and primary key columns while scrolling. On query pages it freezes just the header row. The choice is remembered.
- Tables that span more than one page now show a pagination bar, for example "Rows 101-150 of 1,761", with "Back to first page" and "Next page" buttons. This uses a new ``?_count=before`` option for the :ref:`count endpoint <TableCountView>`, which counts the rows before a ``?_next=`` cursor. (:pr:`3043`)
- The row count is now shown in the table page heading, and the pagination bar sits directly above the table. (:pr:`3048`)
- The ``CREATE TABLE`` or ``CREATE VIEW`` statement is now collapsed into a "Table schema" or "View definition" disclosure at the bottom of the page.
- Facet values now show a faint bar indicating their share of the rows. Suggested facets and facet results have been restyled.
- New **Clear filters** button next to "Apply filters", shown when column filters are applied.

Row page redesign
~~~~~~~~~~~~~~~~~

- The row page now shows the row as a list with one field per line, each with a button to copy its value, in place of a single-row table. Long values are clamped with a toggle to show them in full, and the tables that reference the row are listed below it. (:pr:`3052`)
- **Backwards incompatible:** the row page now includes a new ``_row_fields.html`` template instead of ``_table.html``. A custom ``_table.html`` template that applies to every table no longer changes the row page - provide a ``_row_fields.html`` template instead. Templates for a specific table, ``_table-mydatabase-mytable.html`` and ``_table-row-mydatabase-mytable.html``, still apply to the row page. See :ref:`customization_custom_templates`.
- The row page no longer contains a ``table.rows-and-columns`` element, so plugin JavaScript that looks for that table on row pages will not find it. The ``.above-table-panel`` element plugins can attach to is still present.

Homepage redesign
~~~~~~~~~~~~~~~~~

- The :ref:`homepage <IndexView>` now shows one searchable catalog of tables, views and stored queries across every database, instead of grouping them by database. (:pr:`3049`)
- A search box, focused when the page loads, matches table and view names, column names, table descriptions and the names, titles, descriptions and SQL of stored queries. Results update as you type and can be navigated with the arrow keys and Enter.
- Tables, views, queries and databases you visit are recorded in the browser's ``localStorage``, separately for each signed-in actor, and shown in a **Recently visited** section. The "Recent" list in the Jump to menu now uses the same history.
- Database filter buttons (``?database=``) are shown when more than one database is attached. The table list is built from the internal ``catalog_*`` tables so it stays fast with thousands of tables, and is paginated 30 at a time.
- New :ref:`makeHomepageSections() <javascript_plugins_makeHomepageSections>` JavaScript plugin hook for adding sections to the homepage. Items added to Jump to using the :ref:`plugin_hook_jump_items_sql` plugin hook are included in search results.
- The :ref:`stored query browser <pages_stored_query_browser>` at ``/-/queries`` has been redesigned to match the new homepage.
- **Backwards incompatible:** the ``index.html`` template context has changed and is now documented in :ref:`template_context`. Custom ``index.html`` templates that used keys such as ``tables_and_views_truncated`` or ``table_rows_sum`` will need to be updated. The ``/.json`` output is unchanged.

Other changes
~~~~~~~~~~~~~

- App, database, table, row and column action menus have been redesigned to share positioning, dismissal and keyboard navigation behavior. See the new `/-/patterns/menus <https://latest.datasette.io/-/patterns/menus>`__ page. (:pr:`2984`)
- The :ref:`/-/jump <JumpView>` endpoint now supports ``?type=`` filtering, for example ``?type=table&type=database``. Items now include the URL arguments used to build their URL as fields, hidden tables are excluded, and stored queries use their title as their display name. (:issue:`3024`, :pr:`3025`)
- The internal ``catalog_tables`` table has a new ``hidden`` boolean column, populated for tables that Datasette treats as hidden from default table lists.
- Error responses now use the :ref:`JSON error format <json_api_errors>` when the request sends an ``Accept: application/json`` or ``Content-Type: application/json`` header, not just for URLs ending in ``.json``. Thanks, `kokokoXUY <https://github.com/kokokoXUY>`__. (:issue:`1949`, :pr:`2961`)
- HTML table pages no longer send a ``rel="next"`` HTTP ``Link`` header. (:issue:`3046`)
- ``edit-tools.js``, ``export-dialog.js`` and ``column-stats.js`` are now loaded on demand, the first time they are needed, rather than on every page. The table page now only includes the editing tools loader if the actor can insert, alter, update or delete, indicated by a new ``row_mutation_ui`` template context variable. (:pr:`3042`)

Bug fixes
~~~~~~~~~

- ``execute-write`` operations now work against tables with FTS index triggers, such as those created by sqlite-utils ``enable_fts(create_triggers=True)``. The SQL analysis table now shows why an operation was rejected.
- The ``about`` and ``about_url`` metadata are now displayed even if no ``source`` or ``license`` is set. (:issue:`512`, :pr:`2985`)
- The query page heading now says "returning more than N rows" when results are truncated.
- A stored query that references a missing ``:_actor_X`` key now raises a ``KeyError`` naming that key.
- Facet counts on the second and later pages of a table now count every matching row, and facet links no longer keep the ``?_next=`` pagination cursor. Thanks, `b6tang <https://github.com/b6tang>`__. (:issue:`3021`, :pr:`3026`)
- The ``?_json=`` option now decodes JSON columns on table pages, no longer replaces row values when combined with ``?_extra=columns``, and works with ``?_shape=arrays`` and ``?_shape=arrayfirst``. Thanks, `IlyaKhalafi <https://github.com/IlyaKhalafi>`__. (:issue:`3003`, :pr:`3005`)
- Views with a BLOB column no longer return a 500 error, and CSV exports of those views now include a link to download each value. (:issue:`2601`)
- Fixed a bug where Datasette running with :ref:`setting_base_url` produced links and redirects that repeated the prefix, such as ``/prefix/prefix/``, when the prefix was included in the request path. This affected filter and sort redirects, column sort links, next page links, facet links and the query page. (:issue:`2347`, :issue:`1633`)
- Sorting a table that has no primary key by a column hidden using ``?_nocol=`` or ``?_col=`` no longer returns a 500 error when the results span more than one page. Thanks, `feiiiiii5 <https://github.com/feiiiiii5>`__. (:issue:`2974`, :pr:`2975`)
- Full-text search now works for FTS tables that declare their content table unquoted or with single quotes or backticks, for example ``content=documents``. Previously these tables showed no search box and ``?_search=`` was ignored. Thanks, `b84330808 <https://github.com/b84330808>`__. (:issue:`1991`, :pr:`3011`)
- ``datasette.plugin_config(plugin_name, database=...)`` now falls back to the top-level plugin configuration for a database that has no configuration of its own for that plugin. Thanks, `pujitha24 <https://github.com/pujitha24>`__. (:issue:`2959`, :pr:`3041`)

.. _v1_0_a41:

1.0a41 (2026-09-24)
-------------------

OpenTelemetry support, a new JavaScript API for creating modal dialogs, and several smaller bug fixes.

OpenTelemetry
~~~~~~~~~~~~~

Datasette now supports `OpenTelemetry <https://opentelemetry.io/>`__ traces and metrics for monitoring application performance. Thanks, `Alex Garcia <https://github.com/asg017>`__. (:issue:`1730`, :issue:`2867`)

- Traces cover HTTP requests, database queries and startup, including time spent waiting for SQL threads and queued writes.
- Metrics report query latency, time-limit interruptions, SQL thread usage, write queues and open connections.
- New :ref:`tools for plugin authors <plugin_telemetry>` help plugins add their own traces and metrics, with shared registry classes and pytest helpers.

To collect telemetry, configure an OpenTelemetry SDK and exporter, then run Datasette using ``opentelemetry-instrument``. See :ref:`internals_telemetry` for setup instructions.

Other features
~~~~~~~~~~~~~~

- New :ref:`DatasetteModal JavaScript API <javascript_plugins_modals>` for plugins to create dialogs with Datasette's shared styles, keyboard behavior and focus handling. Datasette's built-in dialogs use the same API. (:issue:`2790`, :pr:`2948`)

Bug fixes
~~~~~~~~~

- Table pages now show measured query timings instead of always displaying 1.2ms. (:issue:`2446`)
- Facet loading now ignores unrelated query parameters such as ``?_facets=x``, instead of returning a 500 error. Thanks, `Peng Boyu <https://github.com/pengboyu-dev>`__. (:pr:`2949`)
- Foreign key values no longer link to tables that do not exist. Thanks, `Dipak Chaudhari <https://github.com/dchaudhari7177>`__. (:issue:`1515`, :pr:`2952`)
- Fixed missing punctuation between table and view counts on the homepage, such as ``0 tables1 view``. Thanks, `Dipak Chaudhari <https://github.com/dchaudhari7177>`__. (:issue:`2012`, :pr:`2951`)
- The sort menu now excludes primary keys that are not included in :ref:`sortable_columns <table_configuration_sortable_columns>`. Thanks, `Sanjay Santhanam <https://github.com/Sanjays2402>`__. (:issue:`1980`, :pr:`2858`)

.. _v1_0_a40:

1.0a40 (2026-09-16)
-------------------

A security fix, a new set of APIs providing background tasks for plugins, an endpoint for counting matching rows, and a collection of bug fixes.

Security fix
~~~~~~~~~~~~

- Fixed a security issue where a trailing newline in a requested table name could bypass table permissions and expose private rows. Thanks for the report, `dpfkdlemtp <https://github.com/dpfkdlemtp>`__. `GHSA-h547-rmjf-5m2m <https://github.com/simonw/datasette/security/advisories/GHSA-h547-rmjf-5m2m>`__

Background tasks
~~~~~~~~~~~~~~~~

Datasette plugins can now use **background tasks** to run code independent of the Datasette request/response cycle.

- New :ref:`datasette_add_background_task` API: plugins register supervised, long-lived background work - typically from a ``startup`` hook - and these will be launched after every ``startup`` hook has run. Tasks are cancelled (with a five-second grace period) on shutdown.
- New ``/-/tasks`` JSON debug endpoint lists every supervised background task and its state, in the style of ``/-/threads``. See :ref:`JsonDataView_tasks`. It requires the ``permissions-debug`` permission.
- New :ref:`plugin_hook_shutdown` plugin hook, called during graceful shutdown (Ctrl-C, ``SIGTERM``) before background tasks are cancelled and before database connections are closed. It is not called on a hard kill (``SIGKILL``).
- Plugin ``asgi_wrapper`` middleware now always runs *after* startup has completed.
- If your plugin uses ``asgi_wrapper`` to start background tasks on the first incoming request, you should migrate to ``datasette.add_background_task()`` instead. `datasette-cron <https://datasette.io/plugins/datasette-cron>`__ and `datasette-enrichments <https://datasette.io/plugins/datasette-enrichments>`__ are being migrated to this pattern.

Other features
~~~~~~~~~~~~~~

- New :ref:`POST count endpoint <TableCountView>` for counting filtered table rows, now used by the **count all** button. (:issue:`2914`)
- Datasette now uses `httpx2 <https://httpx2.pydantic.dev/>`__, the Pydantic-maintained continuation of `httpx <https://www.python-httpx.org/>`__, in place of ``httpx``. The public API is the same, but responses returned by :ref:`internals_datasette_client` are now ``httpx2.Response`` objects rather than ``httpx.Response``. Plugins that use ``isinstance()`` checks against ``httpx.Response`` should be updated to use ``httpx2``. **Plugins that use httpx without explicitly depending on it** will need to add an explicit dependency or switch to `httpx2`.

Bug fixes
~~~~~~~~~

- Column facets now show the remove-filter link for filters using ``column__exact=value``, as well as ``column=value``. (:issue:`1695`)
- The :ref:`alter-table API <TableAlterView>` now rolls back schema changes when a :ref:`write_wrapper <plugin_hook_write_wrapper>` raises after the write. (:issue:`2924`, :pr:`2925`)
- The :ref:`extra_template_vars() <plugin_hook_extra_template_vars>` plugin hook can now return a function or awaitable that resolves to ``None`` when no extra variables are needed. (:issue:`2005`)
- :ref:`request.headers <internals_request>` now supports case-insensitive header lookups, so ``request.headers.get("Content-Type")`` works as well as ``request.headers.get("content-type")``. (:issue:`1861`)
- CSV endpoints now return plain-text error messages for SQL errors. (:issue:`2129`)
- The :ref:`render_cell() <plugin_hook_render_cell>` plugin hook now receives an empty ``pks`` list when rendering SQL views in HTML, matching the JSON ``?_extra=render_cell`` behavior. (:issue:`2639`)
- Numeric comparison filters now correctly handle decimal values, negative numbers and scientific notation when filtering computed columns and SQL views. Thanks, `Rami Abdelrazzaq <https://github.com/RamiNoodle733>`__. (:issue:`1681`, :pr:`2876`)
- Fixed CSV streaming with ``?_stream=on`` on SQL views repeating the second page of results until the CSV size limit was reached. Thanks, `Ankita Advitot <https://github.com/AnkitaAdvitot>`__. (:issue:`2902`, :pr:`2903`)

.. _v1_0_a39:

1.0a39 (2026-09-10)
-------------------

This alpha release includes security fixes for permissions, SQL construction, HTML rendering, authentication and caching, plus improvements to application startup and write execution.

See `0.65.4 <https://docs.datasette.io/en/stable/changelog.html#v0-65-4>`__ for fixes that have been backported to the stable 0.65.x branch.

The Datasette blog `has more details on these releases <https://datasette.io/blog/2026/september-security-releases/>`__.

Some of the security fixes include:

- Table and view permission checks now take SQLite's case-insensitive names into account. See :ref:`authentication_permissions_explained`.
- Viewing a full-text search index table now checks you have permission to view the table from which it draws its content.
- Viewing SQLite statistics tables (``sqlite_stat1`` through ``sqlite_stat4``) is now denied by a default.
- Table schema display now obeys the ``view-table`` permission.
- Table filters using ``?_through=`` require permission to view the intermediate table.
- Foreign-key target and suggestion APIs, incoming foreign-key relationships and their row counts now respect ``view-table`` permission.
- Row endpoints check permissions before resolving primary keys, to avoid revealing the existence of an otherwise invisible primary key.
- Improved permission checks for the create-table API. See :ref:`json_api_write`.
- The write SQL interface now checks ``view-table`` permission for tables referenced by ``CREATE VIEW`` statements.
- Fixed SQL identifier escaping for column names from untrusted database schemas.
- Fixed HTML escaping for column names from untrusted database schemas.
- URL columns now render links only for validated HTTP or HTTPS URLs.
- Private and personalized dynamic responses now use ``Cache-Control: private, no-store``. Anonymous dynamic responses vary by ``Cookie`` and ``Authorization``.
- Actor cookies now respect ``expire_after``.
- Restricted actors can no longer create API tokens.
- Stored-query create, edit and delete forms now block framing to prevent clickjacking.
- Configuration secret redaction now matches key names case-insensitively.
- SQLite extension loading is disabled after extensions supplied using ``--load-extension`` have been loaded.

Other improvements and fixes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- :ref:`db.execute_write() <database_execute_write>` now has a default execution time limit of 2,000ms. Plugins can override this using ``time_limit_ms=`` or disable it using ``time_limit_ms=None``. This limit is independent of the ``sql_time_limit_ms`` setting for read queries.
- Application startup now runs through ASGI lifespan events before requests are accepted, with a first-request fallback for hosts without lifespan support. Thanks, `Alex Garcia <https://github.com/asg017>`__. (:pr:`2887`)
- ``datasette serve`` now runs startup hooks and Uvicorn on the same event loop, preserving background tasks started by plugins. The minimum Uvicorn version is now 0.29. Thanks, `Alex Garcia <https://github.com/asg017>`__. (:pr:`2886`)
- Non-blocking writes using ``execute_write_fn(..., block=False)`` now return a distinct task UUID for every call and work correctly with ``num_sql_threads=0``. Thanks, `Zain Dana Harper <https://github.com/HarperZ9>`__. (:issue:`2860`, :issue:`2859`)
- Dropping a table now disables its full-text search index first. (:issue:`2874`)
- Fixed ``CREATE VIEW`` SQL analysis on Python 3.10.

.. _v1_0_a38:

1.0a38 (2026-08-06)
-------------------

This release fixes a **SQL injection** security issue that affects Datasette instances that serve a **mixture of public and private tables** in the same database, with access configured using the :ref:`Datasette permissions system <authentication>`.

Site administrators who serve private tables in this way are advised to disable the :ref:`execute-sql permission <actions_execute_sql>` on that database to prevent users from accessing private tables using raw SQL queries. The bug that has been fixed would have allowed users with access to any public table to execute SQL injection attacks despite that restriction, giving them read-only access to data in private tables in the same database.

This fix is also available in Datasette 0.65.3.

.. _v1_0_a37:

1.0a37 (2026-07-14)
-------------------

Performance improvement for SQL-backed permission checks, plus an improved permission debugging interface.

- SQL used to resolve permission checks now aggregates permission rules before joining them to resources, improving performance on instances with large schemas. (:issue:`2832`)
- The :ref:`PermissionCheckView` permission debugger now explains why a decision was allowed or denied, including the matching rules. The interactive form can also test a hypothetical actor supplied as JSON, and the :ref:`permissions documentation <authentication_permissions_explained>` now describes resolution rules in more detail. (:issue:`2841`)
- :ref:`db.execute_write(sql, ..., transaction=True) <database_execute_write>` has a new ``transaction=`` parameter, which can be set to ``False`` for statements such as ``VACUUM`` that cannot run inside a transaction. Write tasks now start their transactions using ``BEGIN IMMEDIATE``, which also ensures that writes are rolled back if the task fails. (:issue:`2831`)
- Refreshing a database's schema in Datasette's internal catalog is now performed as a single atomic operation. (:issue:`2831`)
- Fixed schema introspection, table pages, facets and table counts for tables with names containing a ``]`` character. Thanks, `TowyTowy <https://github.com/TowyTowy>`__. (:issue:`2431`, :pr:`2846`)
- ``/-/plugins.json`` once again returns a top-level JSON array of plugin objects, reverting the object envelope introduced in 1.0a36. This should fix a large number of trivial test failures in existing plugins. (:issue:`2842`, :pr:`2843`)

.. _v1_0_a36:

1.0a36 (2026-07-07)
-------------------

The signature features of this alpha are new UIs for **inserting multiple rows at once** (from TSV, CSV or JSON) and for **creating a table from rows**, plus a large number of small **JSON API consistency fixes** in preparation for a 1.0 stable release.

- Table pages now offer an "Insert multiple rows" mode in the row insertion dialog. This accepts pasted TSV, CSV or JSON, previews the parsed rows before inserting them, validates unknown columns as data is pasted and displays omitted auto integer primary keys as ``auto`` in the preview. (:pr:`2813`)
- The bulk insert UI can skip rows with existing primary keys, or update existing rows and insert new rows using the existing ``/<database>/<table>/-/upsert`` API when the actor has both :ref:`insert-row <actions_insert_row>` and :ref:`update-row <actions_update_row>` permissions. (:pr:`2813`)
- The "Create table" dialog now includes a "Create table from data" mode. Paste TSV, CSV or JSON rows to preview inferred columns and types, choose the table name and primary key, then create the table and insert those rows in one step. (:pr:`2813`)
- Datasette's JSON APIs now consistently encode every ``BLOB`` value using the documented :ref:`binary value JSON format <binary_json_format>`, even when the bytes could be decoded as UTF-8 text. (:issue:`2806`, :pr:`2822`)
- The insert and edit row dialogs now provide a dedicated control for ``BLOB`` values. Existing binary values are shown by byte size, image values under 10MB are previewed as thumbnails, and replacements can be attached, dropped or pasted into the control. (:issue:`2806`, :pr:`2822`)
- The table and row JSON APIs now support ``?_extra=column_details`` for returning SQLite schema details for columns, including declared type, SQLite affinity, primary key, ``NOT NULL``, default and hidden-column metadata.
- POST bodies that Datasette reads fully into memory - such as JSON submitted to the write API - are now capped by the new :ref:`setting_max_post_body_bytes` setting, defaulting to 2MB. Oversized requests are rejected with an HTTP 413 error as soon as the limit is exceeded, protecting smaller servers from memory exhaustion. File uploads are unaffected - ``request.form()`` streams those to disk and has its own separate limits. (:issue:`2823`)
