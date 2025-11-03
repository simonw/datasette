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

```{include} upgrade-1.0a20.md
:heading-offset: 1
```
