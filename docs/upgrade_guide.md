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
