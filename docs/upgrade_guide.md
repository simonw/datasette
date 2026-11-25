(upgrade_guide)=
# Upgrade guide

(upgrade_guide_v1)=
## Datasette 0.X -> 1.0

This section reviews breaking changes Datasette ``1.0`` has when upgrading from a ``0.XX`` version. For new features that ``1.0`` offers, see the {ref}`changelog`.

(upgrade_guide_v1_sql_queries)=
### New URL for SQL queries

Prior to ``1.0a14`` the URL for executing a SQL query looked like this:

```text
/databasename?sql=select+1
# Or for JSON:
/databasename.json?sql=select+1
```

This endpoint served two purposes: without a ``?sql=`` it would list the tables in the database, but with that option it would return results of a query instead.

The URL for executing a SQL query now looks like this:

```text
/databasename/-/query?sql=select+1
# Or for JSON:
/databasename/-/query.json?sql=select+1
```

**This isn't a breaking change.** API calls to the older ``/databasename?sql=...`` endpoint will redirect to the new ``databasename/-/query?sql=...`` endpoint. Upgrading to the new URL is recommended to avoid the overhead of the additional redirect.

(upgrade_guide_v1_view_name_query)=
### `view_name` is now `"query"` for SQL query pages

Plugin hooks that accept a `view_name` argument - `extra_template_vars()`, `extra_css_urls()`, `extra_js_urls()`, `extra_body_script()` and the `render` and `can_render` functions of `register_output_renderer()` - are now passed `"query"` for pages that show the results of a SQL query. This applies to arbitrary SQL at `/databasename/-/query?sql=...` and to stored queries.

Previously those pages used `"database"`, the same value as the page listing the tables in a database, except for the output renderer `render` function which was passed `"table"`.

If your plugin checks `view_name == "database"` and should still run on query pages, update it to check for both:

```python
if view_name in ("database", "query"):
    ...
```

(upgrade_guide_v1_row_json)=
### Row JSON returns a single `"row"` object

The JSON for an individual row, such as `/databasename/tablename/1.json`, previously returned that row as the only item in a `"rows"` list. It now returns it as an object in a `"row"` key:

```json
{
  "ok": true,
  "row": {
    "id": 1,
    "content": "hello"
  }
}
```

Two other keys changed to match. The response no longer includes `"truncated"`, which was always `false`. If you request `?_extra=render_cell`, the `"render_cell"` key is now a single object keyed by column name instead of a list containing one object.

Add `?_shape=objects` to the URL to get the previous format, with a `"rows"` list containing that row, a `"truncated"` key and `"render_cell"` as a list. Any other `?_shape=` option returns the same shape as table JSON. See {ref}`json_api_row`.

Output renderer plugins are not affected: on row pages they are still passed a `rows` list containing the one row.

(upgrade_guide_v1_renderer_response)=
### Output renderers must return a `Response`

The `render` function of an output renderer registered using `register_output_renderer()` must now return a `Response` object, or `None` if it cannot render the data. Returning a dictionary, which was deprecated in Datasette 0.49, is now an error.

Change this:

```python
def render_demo():
    return {
        "body": "Hello",
        "content_type": "text/plain",
        "status_code": 200,
        "headers": {"x-demo": "1"},
    }
```

To this:

```python
from datasette import Response


def render_demo():
    return Response(
        "Hello",
        content_type="text/plain",
        status=200,
        headers={"x-demo": "1"},
    )
```

The dictionary returned by `register_output_renderer()` itself must use the `"render"` key for that function. The older `"callback"` key is no longer accepted.

(upgrade_guide_v1_rows)=
### Rows are now `datasette.Row` objects

The rows returned by {ref}`db.execute() <database_execute>` are now {ref}`datasette.Row <internals_row>` objects. They were previously `sqlite3.Row` objects. The rows passed to the `render_cell()`, `row_actions()` and `top_row()` plugin hooks and to output renderers are `datasette.Row` objects too.

The same is true for rows your own code fetches from the connection passed to a {ref}`db.execute_fn() <database_execute_fn>` or {ref}`db.execute_write_fn() <database_execute_write_fn>` function.

`Row` is not tied to SQLite, so that Datasette can support other databases in the future.

Most code does not need to change. These all work as before:

```python
row["name"]
row[0]
id, name = row
len(row)
row.keys()
dict(row)
tuple(row)
```

Check your code for the following, each of which behaves differently:

- **`isinstance(row, sqlite3.Row)`** is now `False`. Use `isinstance(row, datasette.Row)`, or remove the check.
- **Column names are case-sensitive.** `sqlite3.Row` ignored case, so `row["Name"]` could find a column called `name`. Use the name exactly as the query returns it.
- **A missing column name raises `KeyError`.** It previously raised `IndexError`. A position that is out of range still raises `IndexError`.

  ```python
  # Before
  try:
      value = row["optional_column"]
  except IndexError:
      value = None

  # After
  try:
      value = row["optional_column"]
  except KeyError:
      value = None
  ```

  You can also check `"optional_column" in row.columns`.

The connection passed to a {ref}`db.execute_isolated_fn() <database_execute_isolated_fn>` function is not configured by Datasette, so its rows are plain tuples. That has not changed.

(upgrade_guide_v1_results_columns)=
#### `results.columns` is no longer a list of strings

The `.columns` property of the {ref}`Results <database_results>` object returned by `db.execute()` is now a {ref}`Columns <internals_row_columns>` object, containing a `Column` object for each column. Use `.names` to get the names:

```python
results = await db.execute("select id, name from dogs")

# Before
names = results.columns

# After
names = results.columns.names
```

`results.columns.names` is a tuple. Wrap it in `list()` if your code needs a list.

The `columns` argument passed to output renderers is unchanged. It is still a list of strings.

(upgrade_guide_v1_table_column_details)=
#### `db.table_column_details()` returns `datasette.Column` objects

{ref}`db.table_column_details(table) <database_table_column_details>` previously returned a list of `datasette.utils.Column` named tuples. It now returns a {ref}`Columns <internals_row_columns>` object containing `datasette.Column` objects, the same class that describes the columns of a row. `datasette.utils.Column` has been removed.

Reading `column.name`, `column.type`, `column.notnull` and `column.default_value` works as before. These have changed:

- **`is_pk` is now `pk_position`.** It holds the position of the column in the primary key, starting at 1, or 0 if the column is not part of it.
- **`type` is `None` for a column declared without a type.** It was previously an empty string.
- **`hidden` is now a boolean, and generated columns have their own attribute.** `hidden` was previously the integer reported by SQLite: 1 for a hidden column of a virtual table, 2 or 3 for a generated column. It is now `True` only for a hidden column of a virtual table. `column.generated` is `"virtual"` or `"stored"` for a generated column and `None` otherwise. Code that used `if column.hidden` to skip columns that cannot be written to should use `if column.hidden or column.generated`.
- **`cid` has been removed.** Use the position of the column in the result, for example with `enumerate()`.
- **Columns are no longer tuples.** They cannot be unpacked or indexed by position.

```python
columns = await db.table_column_details("dogs")

# Before
pks = [column.name for column in columns if column.is_pk]
for cid, name, type_, *rest in columns:
    print(cid, name, type_.upper())

# After
pks = [
    column.name for column in columns if column.pk_position
]
for cid, column in enumerate(columns):
    print(cid, column.name, (column.type or "").upper())
```

The value returned is no longer a list. It can be looped over and indexed in the same way, and `columns["name"]` now looks up a column by name.

(upgrade_guide_v1_render_cell)=
#### `render_cell()` is always passed the stored value

The {ref}`render_cell() <plugin_hook_render_cell>` plugin hook previously received different things depending on the page. For a foreign key column that Datasette had expanded to a label, `value` was a dictionary such as `{"value": 3, "label": "Natalie"}` and `row` was an undocumented `CustomRow` object. On other pages `value` was the plain value and `row` was a `sqlite3.Row`.

`value` is now always the value stored in the database, and `row` is always a `datasette.Row`. The label is available in the new `row.labels` dictionary, keyed by column name. A column is only present in `row.labels` if Datasette found a label for the value in that row.

Change this:

```python
@hookimpl
def render_cell(value, column):
    if isinstance(value, dict):
        label = value["label"]
        value = value["value"]
        return f"{label} (#{value})"
```

To this:

```python
@hookimpl
def render_cell(row, value, column):
    label = row.labels.get(column)
    if label is not None:
        return f"{label} (#{value})"
```

If your plugin needs to work with both older and newer versions of Datasette:

```python
@hookimpl
def render_cell(row, value, column):
    if isinstance(value, dict):
        # Older versions of Datasette
        label = value["label"]
        value = value["value"]
    else:
        label = getattr(row, "labels", {}).get(column)
    ...
```

The same applies to the `render_cell()` method of a {ref}`column type <datasette_column_types>`, which is now always passed the stored value. If a column is a foreign key and also has a column type assigned, the column type now renders the cell. Previously the cell showed a link to the referenced row, because the column type was passed the label dictionary and did not recognize it. The `transform_value()` method of a column type is likewise passed the stored value.

On the row page, the `row` passed to `render_cell()` now has its values in the order they were selected, the same as on the table page. Previously the primary key columns were moved to the start, so `row[0]` could refer to a different column on the two pages. The row page still displays primary keys first.

(upgrade_guide_v1_renderer_labels)=
#### Output renderers and `?_labels=on`

When a request uses `?_labels=on` or `?_label=column`, the `rows` passed to an {ref}`output renderer <plugin_register_output_renderer>` previously contained `{"value": ..., "label": ...}` dictionaries in place of the expanded values. Those rows now contain the stored values, with the labels in `row.labels`:

```python
async def render_demo(rows, columns):
    lines = []
    for row in rows:
        for column, value in zip(columns, row):
            # Before: value could be {"value": 3, "label": "Natalie"}
            # After: value is 3 and the label is in row.labels
            label = row.labels.get(column)
            lines.append(f"{column}: {value} {label or ''}")
    return Response.text("\n".join(lines))
```

(upgrade_guide_v1_labels_no_label_column)=
#### `?_labels=on` only expands values that have a label

{ref}`Expanding foreign keys <expand_foreign_keys>` with `?_labels=on` previously expanded a foreign key that refers to a table with no {ref}`label column <table_configuration_label_column>`, using the value as its own label:

```json
{"id": 1, "owner_id": {"value": 3, "label": "3"}}
```

Those values are now returned as they are stored:

```json
{"id": 1, "owner_id": 3}
```

The column is no longer listed in the `expanded_columns` extra, and CSV exports no longer add an `owner_id_label` column for it. Foreign keys to tables that do have a label column are expanded as before.

In the HTML interface the value is still a link to the row it refers to.

(upgrade_guide_v1_customrow)=
#### `datasette.utils.CustomRow` has been removed

This undocumented class was used for rows with expanded labels. Use {ref}`datasette.Row <internals_row>` in its place. `Row.from_dict({"id": 1, "owner_id": 3}, labels={"owner_id": "Natalie"})` constructs a row with labels, which is useful for testing a `render_cell()` implementation.

(upgrade_guide_v1_urls_modify)=
### `datasette.urls.modify()` replaces the `path_with_*()` functions

These undocumented functions have been removed from `datasette.utils`:

- `path_with_added_args()`
- `path_with_removed_args()`
- `path_with_replaced_args()`
- `path_with_format()`
- `path_without_format()`
- `append_querystring()`

Use the new {ref}`datasette.urls.modify() <internals_datasette_urls_modify>` method in their place. It takes the current request, or a path as a string, and returns that URL with changes made to its query string or its format.

```python
# Before
from datasette.utils import (
    append_querystring,
    path_with_added_args,
    path_with_format,
    path_with_removed_args,
    path_with_replaced_args,
    path_without_format,
)

path_with_added_args(request, {"_facet": "city"})
path_with_removed_args(request, {"_next"})
path_with_replaced_args(request, {"_sort": "city"})
path_with_format(
    request=request,
    format="json",
    extra_qs={"_shape": "array"},
)
path_without_format(request, include_query_string=True)
append_querystring(url, "_shape=array")

# After
datasette.urls.modify(request, add={"_facet": "city"})
datasette.urls.modify(request, remove="_next")
datasette.urls.modify(request, replace={"_sort": "city"})
datasette.urls.modify(
    request, format="json", add={"_shape": "array"}
)
datasette.urls.modify(request, format=False)
datasette.urls.modify(url, add={"_shape": "array"})
```

Two things behave differently:

- **The result already includes the `base_url` prefix.** The old functions returned a path without it, which then had to be passed to `datasette.urls.path()`. Do not add the prefix to the result of `modify()` yourself.
- **Passing `None` as a value to `path_with_added_args()` removed that key.** Use `remove="key"` for that, or `replace={"key": None}`.

A plugin that needs to work with both older and newer versions of Datasette can check for the new method:

```python
if hasattr(datasette.urls, "modify"):
    url = datasette.urls.modify(
        request, add={"_facet": "city"}
    )
else:
    from datasette.utils import path_with_added_args

    url = datasette.urls.path(
        path_with_added_args(request, {"_facet": "city"})
    )
```

#### In custom templates

The `path_with_replaced_args()`, `fix_path()` and `append_querystring()` functions are no longer passed to the table and query templates. Use `urls.modify()`, which is available in every template:

```jinja
{# Before #}
<a href="{{ fix_path(path_with_replaced_args(request, {"_sort": "name"})) }}">Sort by name</a>
<a href="{{ append_querystring(renderers.json, "_shape=array") }}">JSON array</a>

{# After #}
<a href="{{ urls.modify(request, replace={"_sort": "name"}) }}">Sort by name</a>
<a href="{{ urls.modify(renderers.json, add={"_shape": "array"}) }}">JSON array</a>
```

`fix_path(path)` on its own can be replaced with `urls.path(path)`.

(upgrade_guide_v1_keyword_only)=
### Optional arguments are now keyword-only

Many of the {ref}`documented internal methods <internals>` now only accept their optional arguments as keywords. Passing one of them by position raises a `TypeError`, for example `Datasette.render_template() takes from 2 to 3 positional arguments but 4 were given`.

```python
# Before
html = await datasette.render_template(
    "my_plugin.html", context, request
)
config = datasette.plugin_config(
    "my-plugin", "fixtures", "facetable"
)
url = datasette.urls.table("fixtures", "facetable", "json")
results = await db.execute(sql, params, True)
response = Response.html(body, 200, {"x-my-plugin": "1"})
db = Database(datasette, "/path/to/data.db", False)

# After
html = await datasette.render_template(
    "my_plugin.html", context, request=request
)
config = datasette.plugin_config(
    "my-plugin", database="fixtures", table="facetable"
)
url = datasette.urls.table(
    "fixtures", "facetable", format="json"
)
results = await db.execute(sql, params, truncate=True)
response = Response.html(
    body, 200, headers={"x-my-plugin": "1"}
)
db = Database(
    datasette, "/path/to/data.db", is_mutable=False
)
```

The names of the arguments have not changed, so code that passes them as keywords also works with earlier versions of Datasette.

These are the affected signatures:

- `Datasette(files=None, *, ...)` - every argument other than `files` is keyword-only.
- `datasette.render_template(templates, context=None, *, request=None, view_name=None)`
- `datasette.execute(db_name, sql, params=None, *, ...)` and `db.execute(sql, params=None, *, ...)`
- `db.execute_write(sql, params=None, *, ...)`, `db.execute_write_script(sql, *, ...)`, `db.execute_write_many(sql, params_seq, *, ...)` and `db.execute_write_fn(fn, *, ...)`
- `datasette.get_database(name=None, *, route=None)`, `datasette.add_database(db, name=None, *, route=None)` and `datasette.add_memory_database(memory_name, name=None, *, route=None)`
- `Database(ds, path=None, *, ...)` - every argument after `path` is keyword-only.
- `datasette.plugin_config(plugin_name, *, database=None, table=None, fallback=True)`
- `datasette.set_actor_cookie(response, actor, *, expire_after=None)`
- `datasette.client.get(path, *, skip_permission_checks=False, **kwargs)` and the other `datasette.client` methods.
- `datasette.urls.instance()`, `.path()`, `.database()`, `.database_query()`, `.table()`, `.query()` and `.row()` now require `format=` as a keyword.
- `Request.fake(path_with_query_string, *, method="GET", scheme="http", url_vars=None)`
- `Response(body=None, *, status=200, headers=None, content_type="text/plain")`
- `Response.html()`, `.text()`, `.json()`, `.stream()`, `.error()` and `.redirect()` still accept `status` as a second positional argument, so `Response.json(data, 400)` continues to work. Their `headers`, `default` and `content_type` arguments are keyword-only.
- `response.set_cookie(key, value="", *, max_age=None, ...)`

(upgrade_guide_v1_format_bytes)=
### `format_bytes()` has been removed

The `format_bytes()` function is no longer available in templates, and `datasette.utils.format_bytes()` has been removed. Use Jinja's built-in [filesizeformat](https://jinja.palletsprojects.com/en/stable/templates/#jinja-filters.filesizeformat) filter in their place.

In a custom template:

```jinja
{# Before #}
{{ format_bytes(size) }}

{# After #}
{{ size|filesizeformat }}
```

In Python code:

```python
# Before
from datasette.utils import format_bytes

label = format_bytes(size)

# After
from jinja2.filters import do_filesizeformat

label = do_filesizeformat(size)
```

The output is not identical. `filesizeformat` uses decimal units, where 1 MB is 1,000,000 bytes. `format_bytes()` used 1 MB to mean 1,048,576 bytes, so 5,242,880 bytes was displayed as `5.0 MB` and is now `5.2 MB`. Kilobytes are now written as `kB`, and sizes under 1,000 bytes as `999 Bytes`.

(upgrade_guide_v1_test_client)=
### `datasette.utils.testing.TestClient` has been removed

The undocumented `datasette.utils.testing` module has been removed, along with its `TestClient` and `TestResponse` classes. Tests that used them should use {ref}`datasette.client <internals_datasette_client>`, as described in {ref}`testing_plugins`.

The methods of `TestClient` were synchronous. The methods of `datasette.client` are `async`, so each test needs to be an `async def` function run by `pytest-asyncio`:

```python
# Before
from datasette.app import Datasette
from datasette.utils.testing import TestClient


def test_versions():
    client = TestClient(Datasette(memory=True))
    response = client.get("/-/versions.json")
    assert response.status == 200
    assert "python" in response.json


# After
import pytest
from datasette.app import Datasette


@pytest.mark.asyncio
async def test_versions():
    datasette = Datasette(memory=True)
    response = await datasette.client.get(
        "/-/versions.json"
    )
    assert response.status_code == 200
    assert "python" in response.json()
```

The response is now an `httpx` response object:

- `response.status` is now `response.status_code`.
- `response.json` was a property. It is now a method, `response.json()`.
- `response.body` is now `response.content`.

`client.actor_cookie(actor)` has moved to `datasette.client.actor_cookie(actor)`.

(upgrade_guide_v1_primary_key_values)=
### Primary key values in row URLs and templates

Datasette now supports rows with a primary key that is an empty string or a binary value, and primary key columns with no declared type. Row URLs for ordinary integer and text primary keys have not changed.

Three things may need attention:

- **`primary_key_values` on the row page is no longer always a list of strings.** A custom `row.html` template is passed the values decoded from the URL. A binary key is `bytes`, and a number in a primary key column with no declared type is an `int` or `float`. Use the new `key_value_display` filter to display one:

  ```jinja
  {# Before #}
  {{ primary_key_values|join(", ") }}

  {# After #}
  {% for value in primary_key_values %}{{ value|key_value_display }}{% if not loop.last %}, {% endif %}{% endfor %}
  ```

- **`?_shape=object` returns a 400 error if any row has a `null` primary key.** Those rows have no key to be filed under. Use the default `?_shape=objects` for tables that contain them.
- **Rows with a `null` primary key have no row page.** They previously linked to `/database/table/None`, which did not show that row. Code that builds a row URL for every row in a table needs to skip them.

Build row URLs with {ref}`datasette.urls.row() <internals_datasette_urls>`, passing a list of primary key values, in place of formatting the values into a path yourself. It encodes empty strings and binary values for you.

(upgrade_guide_v1_metadata)=
### Metadata changes

Metadata was completely revamped for Datasette 1.0. There are a number of related breaking changes, from the ``metadata.yaml`` file to Python APIs, that you'll need to consider when upgrading.

(upgrade_guide_v1_metadata_split)=
#### ``metadata.yaml`` split into ``datasette.yaml``

Before Datasette 1.0, the ``metadata.yaml`` file became a kitchen sink if a mix of metadata, configuration, and settings. Now ``metadata.yaml`` is strictly for metadata (ex title and descriptions of database and tables, licensing info, etc). Other settings have been moved to a ``datasette.yml`` configuration file, described in {ref}`configuration`.

To start Datasette with both metadata and configuration files, run it like this:

```bash
datasette --metadata metadata.yaml --config datasette.yaml
# Or the shortened version:
datasette -m metadata.yml -c datasette.yml
```

(upgrade_guide_v1_metadata_upgrade)=
#### Upgrading an existing ``metadata.yaml`` file

The [datasette-upgrade plugin](https://github.com/datasette/datasette-upgrade) can be used to split a Datasette 0.x.x ``metadata.yaml`` (or ``.json``) file into separate ``metadata.yaml`` and ``datasette.yaml`` files. First, install the plugin:

```bash
datasette install datasette-upgrade
```

Then run it like this to produce the two new files:

```bash
datasette upgrade metadata-to-config metadata.json -m metadata.yml -c datasette.yml
```

#### Metadata "fallback" has been removed

Certain keys in metadata like ``license`` used to "fallback" up the chain of ownership.
For example, if you set an ``MIT`` to a database and a table within that database did not have a specified license, then that table would inherit an ``MIT`` license.

This behavior has been removed in Datasette 1.0. Now license fields must be placed on all items, including individual databases and tables.

(upgrade_guide_v1_metadata_removed)=
#### The ``get_metadata()`` plugin hook has been removed

In Datasette ``0.x`` plugins could implement a ``get_metadata()`` plugin hook to customize how metadata was retrieved for different instances, databases and tables.

This hook could be inefficient, since some pages might load metadata for many different items (to list a large number of tables, for example) which could result in a large number of calls to potentially expensive plugin hook implementations.

As of Datasette ``1.0a14`` (2024-08-05), the ``get_metadata()`` hook has been deprecated:

```python
# ❌ DEPRECATED in Datasette 1.0
@hookimpl
def get_metadata(datasette, key, database, table):
    pass
```

Instead, plugins are encouraged to interact directly with Datasette's in-memory metadata tables in SQLite using the following methods on the {ref}`internals_datasette`:

- {ref}`get_instance_metadata() <datasette_get_instance_metadata>` and {ref}`set_instance_metadata() <datasette_set_instance_metadata>`
- {ref}`get_database_metadata() <datasette_get_database_metadata>` and {ref}`set_database_metadata() <datasette_set_database_metadata>`
- {ref}`get_resource_metadata() <datasette_get_resource_metadata>` and {ref}`set_resource_metadata() <datasette_set_resource_metadata>`
- {ref}`get_column_metadata() <datasette_get_column_metadata>` and {ref}`set_column_metadata() <datasette_set_column_metadata>`

A plugin that stores or calculates its own metadata can implement the {ref}`plugin_hook_startup` hook to populate those items on startup, and then call those methods while it is running to persist any new metadata changes.

(upgrade_guide_v1_metadata_json_removed)=
#### The ``/metadata.json`` endpoint has been removed

As of Datasette ``1.0a14``, the root level ``/metadata.json`` endpoint has been removed. Metadata for tables will become available through currently in-development extras in a future alpha.

(upgrade_guide_v1_metadata_method_removed)=
#### The ``metadata()`` method on the Datasette class has been removed

As of Datasette ``1.0a14``, the ``.metadata()`` method on the Datasette Python API has been removed.

Instead, one should use the following methods on a Datasette class:

- {ref}`get_instance_metadata() <datasette_get_instance_metadata>`
- {ref}`get_database_metadata() <datasette_get_database_metadata>`
- {ref}`get_resource_metadata() <datasette_get_resource_metadata>`
- {ref}`get_column_metadata() <datasette_get_column_metadata>`

(upgrade_guide_v1_a20)=
```{include} upgrade-1.0a20.md
:heading-offset: 1
```

(upgrade_guide_v1_a25)=
### Datasette 1.0a25: `create_token()` signature change

`datasette.create_token()` is now an `async` method (previously it was synchronous). The `restrict_all`, `restrict_database`, and `restrict_resource` keyword arguments have been replaced by a single `restrictions` parameter that accepts a {ref}`TokenRestrictions <TokenRestrictions>` object.

Old code:

```python
token = datasette.create_token(
    actor_id="user1",
    restrict_all=["view-instance", "view-table"],
    restrict_database={"docs": ["view-query"]},
    restrict_resource={
        "docs": {
            "attachments": ["insert-row", "update-row"]
        }
    },
)
```

New code:

```python
from datasette.tokens import TokenRestrictions

token = await datasette.create_token(
    actor_id="user1",
    restrictions=(
        TokenRestrictions()
        .allow_all("view-instance")
        .allow_all("view-table")
        .allow_database("docs", "view-query")
        .allow_resource("docs", "attachments", "insert-row")
        .allow_resource("docs", "attachments", "update-row")
    ),
)
```

The `datasette create-token` CLI command is unchanged.

(upgrade_guide_csrf)=
### CSRF protection is now header-based

Datasette's Cross-Site Request Forgery protection no longer uses tokens. The previous `asgi-csrf` mechanism - which set a `ds_csrftoken` cookie and required a matching `<input type="hidden" name="csrftoken">` in every form - has been replaced with an ASGI middleware that inspects the browser-set `Sec-Fetch-Site` and `Origin` headers, following the approach described in [Filippo Valsorda's research](https://words.filippo.io/csrf/) and implemented in Go 1.25's `http.CrossOriginProtection`.

This works identically on HTTPS, HTTP, and localhost. Non-browser clients (curl, Python `requests`, server-to-server scripts) do not send `Sec-Fetch-Site` or `Origin` and are passed through unchanged - CSRF is a browser-only attack.

Requests that carry an explicit `Authorization: Bearer ...` header are also exempt from the CSRF check, because bearer tokens are not ambient browser credentials: a malicious cross-origin page cannot cause the browser to attach a target site's bearer token unless the attacker's JavaScript already possesses it. This exemption is narrow - it covers the `Bearer` scheme only, not `Basic` or `Digest` - and it does not depend on the `--cors` setting. The exemption is about CSRF classification, not browser read access; CORS still controls the latter.

#### What you can remove

You can now delete any of the following from your plugins and custom templates:

- Hidden CSRF form fields:

  ```html
  <input type="hidden" name="csrftoken" value="{{ csrftoken() }}">
  ```

  The `csrftoken()` template helper (and `request.scope["csrftoken"]()` for plugins that call it from Python) still exists as a compatibility shim. It now returns a per-request random string rather than a cookie-bound signed value. Datasette no longer validates this token, and no `ds_csrftoken` cookie is set.

  **Important for plugin authors:** if your plugin previously used `request.scope["csrftoken"]()` or the `ds_csrftoken` cookie as a security primitive (for example, signing a URL and later comparing it to the cookie), the invariant that the token equals `request.cookies["ds_csrftoken"]` no longer holds. Replace those flows with signed, short-lived action URLs or explicit non-ambient credentials.

- Manual CSRF token extraction in tests, e.g.:

  ```python
  # No longer needed
  csrftoken = response.cookies["ds_csrftoken"]
  cookies["ds_csrftoken"] = csrftoken
  post_data["csrftoken"] = csrftoken
  ```

  The `ds_csrftoken` cookie is no longer set at all. The `csrftoken_from=` argument of the Datasette test client's `.post()` method is now a no-op and can be removed from your test code.

#### Breaking changes

- **The `skip_csrf` plugin hook has been removed.** Existing plugins that still declare a `skip_csrf` hookimpl will continue to load - pluggy silently ignores unknown hook names - but the hook is no longer consulted by core, so the flows it previously unlocked will now be blocked (or allowed) purely on the basis of the new header check.

  The new middleware already covers the common cases that `skip_csrf` was written for:

  - Browser-initiated JSON POSTs automatically get `Sec-Fetch-Site: same-origin` and pass the check.
  - Non-browser API clients (curl, `requests`, server-to-server scripts) do not send browser security headers and are passed through.
  - Requests with an explicit `Authorization: Bearer ...` header are exempt from the CSRF check (see above).

  If your plugin previously used `skip_csrf` to accept cross-origin browser POSTs, replace that flow with an authentication mechanism that does **not** rely on ambient browser credentials. Safe patterns include:

  - Requiring an `Authorization: Bearer ...` API token on the endpoint.
  - Requiring a non-ambient credential in the request body (a webhook secret, HMAC signature, signed capability URL, OAuth client credential, or similar).
  - Issuing a short-lived signed URL that encodes the actor, the action, and an expiry, and verifying the signature on request.

  Do not rely on the `ds_csrftoken` cookie for your own plugin's security checks - Datasette no longer sets or validates it, and the `request.scope["csrftoken"]()` compatibility shim now returns a fresh random value each request rather than the signed cookie-bound value it used to.

- **The `asgi-csrf` dependency has been dropped.** Any plugin that imported from `asgi_csrf` directly will need to be updated.

- **The `csrf_error.html` template now receives a `reason` context variable** instead of `message_id` and `message_name`. Custom overrides of this template should be updated.

#### Security properties

For defense-in-depth the `ds_actor` and `ds_messages` cookies continue to be set with `SameSite=Lax` (Datasette's long-standing default). This means a genuine cross-site POST from an attacker's page would arrive without the user's authentication cookie even if the header check somehow failed.
