import asyncio
import csv
import itertools
import re
import time
import urllib

import jinja2
import pint
from sanic import response
from sanic.exceptions import NotFound
from sanic.views import HTTPMethodView

from datasette import __version__
from datasette.plugins import pm
from datasette.utils import (
    InterruptedError,
    InvalidSql,
    LimitedWriter,
    format_bytes,
    is_url,
    path_with_added_args,
    path_with_removed_args,
    path_with_format,
    resolve_table_and_format,
    sqlite3,
    to_css_class,
)

ureg = pint.UnitRegistry()

HASH_LENGTH = 7


class DatasetteError(Exception):
    def __init__(
        self,
        message,
        title=None,
        error_dict=None,
        status=500,
        template=None,
        messagge_is_html=False,
    ):
        self.message = message
        self.title = title
        self.error_dict = error_dict or {}
        self.status = status
        self.messagge_is_html = messagge_is_html


class RenderMixin(HTTPMethodView):
    def _asset_urls(self, key, template, context):
        # Flatten list-of-lists from plugins:
        seen_urls = set()
        for url_or_dict in itertools.chain(
            itertools.chain.from_iterable(
                getattr(pm.hook, key)(
                    template=template.name,
                    database=context.get("database"),
                    table=context.get("table"),
                    datasette=self.ds,
                )
            ),
            (self.ds.metadata(key) or []),
        ):
            if isinstance(url_or_dict, dict):
                url = url_or_dict["url"]
                sri = url_or_dict.get("sri")
            else:
                url = url_or_dict
                sri = None
            if url in seen_urls:
                continue
            seen_urls.add(url)
            if sri:
                yield {"url": url, "sri": sri}
            else:
                yield {"url": url}

    def database_url(self, database):
        db = self.ds.databases[database]
        if self.ds.config("hash_urls") and db.hash:
            return "/{}-{}".format(database, db.hash[:HASH_LENGTH])
        else:
            return "/{}".format(database)

    def database_color(self, database):
        return "ff0000"

    def render(self, templates, **context):
        template = self.ds.jinja_env.select_template(templates)
        select_templates = [
            "{}{}".format("*" if template_name == template.name else "", template_name)
            for template_name in templates
        ]
        body_scripts = []
        # pylint: disable=no-member
        for script in pm.hook.extra_body_script(
            template=template.name,
            database=context.get("database"),
            table=context.get("table"),
            view_name=self.name,
            datasette=self.ds,
        ):
            body_scripts.append(jinja2.Markup(script))
        return response.html(
            template.render(
                {
                    **context,
                    **{
                        "app_css_hash": self.ds.app_css_hash(),
                        "select_templates": select_templates,
                        "zip": zip,
                        "body_scripts": body_scripts,
                        "extra_css_urls": self._asset_urls(
                            "extra_css_urls", template, context
                        ),
                        "extra_js_urls": self._asset_urls(
                            "extra_js_urls", template, context
                        ),
                        "format_bytes": format_bytes,
                        "database_url": self.database_url,
                        "database_color": self.database_color,
                    },
                }
            )
        )


class BaseView(RenderMixin):
    name = ""
    re_named_parameter = re.compile(":([a-zA-Z0-9_]+)")

    def __init__(self, datasette):
        self.ds = datasette

    def options(self, request, *args, **kwargs):
        r = response.text("ok")
        if self.ds.cors:
            r.headers["Access-Control-Allow-Origin"] = "*"
        return r

    def redirect(self, request, path, forward_querystring=True, remove_args=None):
        if request.query_string and "?" not in path and forward_querystring:
            path = "{}?{}".format(path, request.query_string)
        if remove_args:
            path = path_with_removed_args(request, remove_args, path=path)
        r = response.redirect(path)
        r.headers["Link"] = "<{}>; rel=preload".format(path)
        if self.ds.cors:
            r.headers["Access-Control-Allow-Origin"] = "*"
        return r

    async def data(self, request, database, hash, **kwargs):
        raise NotImplementedError

    async def resolve_db_name(self, request, db_name, **kwargs):
        hash = None
        name = None
        if "-" in db_name:
            # Might be name-and-hash, or might just be
            # a name with a hyphen in it
            name, hash = db_name.rsplit("-", 1)
            if name not in self.ds.databases:
                # Try the whole name
                name = db_name
                hash = None
        else:
            name = db_name
        # Verify the hash
        try:
            db = self.ds.databases[name]
        except KeyError:
            raise NotFound("Database not found: {}".format(name))

        expected = "000"
        if db.hash is not None:
            expected = db.hash[:HASH_LENGTH]
        correct_hash_provided = expected == hash

        if not correct_hash_provided:
            if "table_and_format" in kwargs:

                async def async_table_exists(t):
                    return await self.ds.table_exists(name, t)

                table, _format = await resolve_table_and_format(
                    table_and_format=urllib.parse.unquote_plus(
                        kwargs["table_and_format"]
                    ),
                    table_exists=async_table_exists,
                    allowed_formats=self.ds.renderers.keys(),
                )
                kwargs["table"] = table
                if _format:
                    kwargs["as_format"] = ".{}".format(_format)
            elif "table" in kwargs:
                kwargs["table"] = urllib.parse.unquote_plus(kwargs["table"])

            should_redirect = "/{}-{}".format(name, expected)
            if "table" in kwargs:
                should_redirect += "/" + urllib.parse.quote_plus(kwargs["table"])
            if "pk_path" in kwargs:
                should_redirect += "/" + kwargs["pk_path"]
            if "as_format" in kwargs:
                should_redirect += kwargs["as_format"]
            if "as_db" in kwargs:
                should_redirect += kwargs["as_db"]

            if (
                (self.ds.config("hash_urls") or "_hash" in request.args)
                and
                # Redirect only if database is immutable
                not self.ds.databases[name].is_mutable
            ):
                return name, expected, correct_hash_provided, should_redirect

        return name, expected, correct_hash_provided, None

    def get_templates(self, database, table=None):
        assert NotImplemented

    async def get(self, request, db_name, **kwargs):
        database, hash, correct_hash_provided, should_redirect = await self.resolve_db_name(
            request, db_name, **kwargs
        )
        if should_redirect:
            return self.redirect(request, should_redirect, remove_args={"_hash"})

        return await self.view_get(
            request, database, hash, correct_hash_provided, **kwargs
        )

    async def as_csv(self, request, database, hash, **kwargs):
        stream = request.args.get("_stream")
        if stream:
            # Some quick sanity checks
            if not self.ds.config("allow_csv_stream"):
                raise DatasetteError("CSV streaming is disabled", status=400)
            if request.args.get("_next"):
                raise DatasetteError("_next not allowed for CSV streaming", status=400)
            kwargs["_size"] = "max"
        # Fetch the first page
        try:
            response_or_template_contexts = await self.data(
                request, database, hash, **kwargs
            )
            if isinstance(response_or_template_contexts, response.HTTPResponse):
                return response_or_template_contexts
            else:
                data, _, _ = response_or_template_contexts
        except (sqlite3.OperationalError, InvalidSql) as e:
            raise DatasetteError(str(e), title="Invalid SQL", status=400)

        except (sqlite3.OperationalError) as e:
            raise DatasetteError(str(e))

        except DatasetteError:
            raise

        # Convert rows and columns to CSV
        headings = data["columns"]
        # if there are expanded_columns we need to add additional headings
        expanded_columns = set(data.get("expanded_columns") or [])
        if expanded_columns:
            headings = []
            for column in data["columns"]:
                headings.append(column)
                if column in expanded_columns:
                    headings.append("{}_label".format(column))

        async def stream_fn(r):
            nonlocal data
            writer = csv.writer(LimitedWriter(r, self.ds.config("max_csv_mb")))
            first = True
            next = None
            while first or (next and stream):
                try:
                    if next:
                        kwargs["_next"] = next
                    if not first:
                        data, _, _ = await self.data(request, database, hash, **kwargs)
                    if first:
                        writer.writerow(headings)
                        first = False
                    next = data.get("next")
                    for row in data["rows"]:
                        if not expanded_columns:
                            # Simple path
                            writer.writerow(row)
                        else:
                            # Look for {"value": "label": } dicts and expand
                            new_row = []
                            for cell in row:
                                if isinstance(cell, dict):
                                    new_row.append(cell["value"])
                                    new_row.append(cell["label"])
                                else:
                                    new_row.append(cell)
                            writer.writerow(new_row)
                except Exception as e:
                    print("caught this", e)
                    r.write(str(e))
                    return

        content_type = "text/plain; charset=utf-8"
        headers = {}
        if self.ds.cors:
            headers["Access-Control-Allow-Origin"] = "*"
        if request.args.get("_dl", None):
            content_type = "text/csv; charset=utf-8"
            disposition = 'attachment; filename="{}.csv"'.format(
                kwargs.get("table", database)
            )
            headers["Content-Disposition"] = disposition

        return response.stream(stream_fn, headers=headers, content_type=content_type)

    async def get_format(self, request, database, args):
        """ Determine the format of the response from the request, from URL
            parameters or from a file extension.

            `args` is a dict of the path components parsed from the URL by the router.
        """
        # If ?_format= is provided, use that as the format
        _format = request.args.get("_format", None)
        if not _format:
            _format = (args.pop("as_format", None) or "").lstrip(".")
        if "table_and_format" in args:

            async def async_table_exists(t):
                return await self.ds.table_exists(database, t)

            table, _ext_format = await resolve_table_and_format(
                table_and_format=urllib.parse.unquote_plus(args["table_and_format"]),
                table_exists=async_table_exists,
                allowed_formats=self.ds.renderers.keys(),
            )
            _format = _format or _ext_format
            args["table"] = table
            del args["table_and_format"]
        elif "table" in args:
            args["table"] = urllib.parse.unquote_plus(args["table"])
        return _format, args

    async def view_get(self, request, database, hash, correct_hash_provided, **kwargs):
        _format, kwargs = await self.get_format(request, database, kwargs)

        if _format == "csv":
            return await self.as_csv(request, database, hash, **kwargs)

        if _format is None:
            # HTML views default to expanding all foriegn key labels
            kwargs["default_labels"] = True

        extra_template_data = {}
        start = time.time()
        status_code = 200
        templates = []
        try:
            response_or_template_contexts = await self.data(
                request, database, hash, **kwargs
            )
            if isinstance(response_or_template_contexts, response.HTTPResponse):
                return response_or_template_contexts

            else:
                data, extra_template_data, templates = response_or_template_contexts
        except InterruptedError:
            raise DatasetteError(
                """
                SQL query took too long. The time limit is controlled by the
                <a href="https://datasette.readthedocs.io/en/stable/config.html#sql-time-limit-ms">sql_time_limit_ms</a>
                configuration option.
            """,
                title="SQL Interrupted",
                status=400,
                messagge_is_html=True,
            )
        except (sqlite3.OperationalError, InvalidSql) as e:
            raise DatasetteError(str(e), title="Invalid SQL", status=400)

        except (sqlite3.OperationalError) as e:
            raise DatasetteError(str(e))

        except DatasetteError:
            raise

        end = time.time()
        data["query_ms"] = (end - start) * 1000
        for key in ("source", "source_url", "license", "license_url"):
            value = self.ds.metadata(key)
            if value:
                data[key] = value

        # Special case for .jsono extension - redirect to _shape=objects
        if _format == "jsono":
            return self.redirect(
                request,
                path_with_added_args(
                    request,
                    {"_shape": "objects"},
                    path=request.path.rsplit(".jsono", 1)[0] + ".json",
                ),
                forward_querystring=False,
            )

        if _format in self.ds.renderers.keys():
            # Dispatch request to the correct output format renderer
            # (CSV is not handled here due to streaming)
            result = self.ds.renderers[_format](request.args, data, self.name)
            if result is None:
                raise NotFound("No data")

            response_args = {
                "content_type": result.get("content_type", "text/plain"),
                "status": result.get("status_code", 200),
            }

            if type(result.get("body")) == bytes:
                response_args["body_bytes"] = result.get("body")
            else:
                response_args["body"] = result.get("body")

            r = response.HTTPResponse(**response_args)
        else:
            extras = {}
            if callable(extra_template_data):
                extras = extra_template_data()
                if asyncio.iscoroutine(extras):
                    extras = await extras
            else:
                extras = extra_template_data
            url_labels_extra = {}
            if data.get("expandable_columns"):
                url_labels_extra = {"_labels": "on"}

            renderers = {
                key: path_with_format(request, key, {**url_labels_extra})
                for key in self.ds.renderers.keys()
            }
            url_csv_args = {"_size": "max", **url_labels_extra}
            url_csv = path_with_format(request, "csv", url_csv_args)
            url_csv_path = url_csv.split("?")[0]
            context = {
                **data,
                **extras,
                **{
                    "renderers": renderers,
                    "url_csv": url_csv,
                    "url_csv_path": url_csv_path,
                    "url_csv_hidden_args": [
                        (key, value)
                        for key, value in urllib.parse.parse_qsl(request.query_string)
                        if key not in ("_labels", "_facet", "_size")
                    ]
                    + [("_size", "max")],
                    "datasette_version": __version__,
                    "config": self.ds.config_dict(),
                },
            }
            if "metadata" not in context:
                context["metadata"] = self.ds.metadata
            r = self.render(templates, **context)
            r.status = status_code

        ttl = request.args.get("_ttl", None)
        if ttl is None or not ttl.isdigit():
            if correct_hash_provided:
                ttl = self.ds.config("default_cache_ttl_hashed")
            else:
                ttl = self.ds.config("default_cache_ttl")

        return self.set_response_headers(r, ttl)

    def set_response_headers(self, response, ttl):
        # Set far-future cache expiry
        if self.ds.cache_headers and response.status == 200:
            ttl = int(ttl)
            if ttl == 0:
                ttl_header = "no-cache"
            else:
                ttl_header = "max-age={}".format(ttl)
            response.headers["Cache-Control"] = ttl_header
        response.headers["Referrer-Policy"] = "no-referrer"
        if self.ds.cors:
            response.headers["Access-Control-Allow-Origin"] = "*"
        return response

    async def custom_sql(
        self,
        request,
        database,
        hash,
        sql,
        editable=True,
        canned_query=None,
        metadata=None,
        _size=None,
    ):
        params = request.raw_args
        if "sql" in params:
            params.pop("sql")
        if "_shape" in params:
            params.pop("_shape")
        # Extract any :named parameters
        named_parameters = self.re_named_parameter.findall(sql)
        named_parameter_values = {
            named_parameter: params.get(named_parameter) or ""
            for named_parameter in named_parameters
        }

        # Set to blank string if missing from params
        for named_parameter in named_parameters:
            if named_parameter not in params:
                params[named_parameter] = ""

        extra_args = {}
        if params.get("_timelimit"):
            extra_args["custom_time_limit"] = int(params["_timelimit"])
        if _size:
            extra_args["page_size"] = _size
        results = await self.ds.execute(
            database, sql, params, truncate=True, **extra_args
        )
        columns = [r[0] for r in results.description]

        templates = ["query-{}.html".format(to_css_class(database)), "query.html"]
        if canned_query:
            templates.insert(
                0,
                "query-{}-{}.html".format(
                    to_css_class(database), to_css_class(canned_query)
                ),
            )

        async def extra_template():
            display_rows = []
            for row in results.rows:
                display_row = []
                for column, value in zip(results.columns, row):
                    display_value = value
                    # Let the plugins have a go
                    # pylint: disable=no-member
                    plugin_value = pm.hook.render_cell(
                        value=value,
                        column=column,
                        table=None,
                        database=database,
                        datasette=self.ds,
                    )
                    if plugin_value is not None:
                        display_value = plugin_value
                    else:
                        if value in ("", None):
                            display_value = jinja2.Markup("&nbsp;")
                        elif is_url(str(display_value).strip()):
                            display_value = jinja2.Markup(
                                '<a href="{url}">{url}</a>'.format(
                                    url=jinja2.escape(value.strip())
                                )
                            )
                    display_row.append(display_value)
                display_rows.append(display_row)
            return {
                "display_rows": display_rows,
                "custom_sql": True,
                "named_parameter_values": named_parameter_values,
                "editable": editable,
                "canned_query": canned_query,
                "metadata": metadata,
                "config": self.ds.config_dict(),
                "request": request,
                "path_with_added_args": path_with_added_args,
                "path_with_removed_args": path_with_removed_args,
                "hide_sql": "_hide_sql" in params,
            }

        return (
            {
                "database": database,
                "rows": results.rows,
                "truncated": results.truncated,
                "columns": columns,
                "query": {"sql": sql, "params": params},
            },
            extra_template,
            templates,
        )
