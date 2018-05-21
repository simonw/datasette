import asyncio
import json
import re
import sqlite3
import threading
import time

import pint
from sanic import response
from sanic.exceptions import NotFound
from sanic.views import HTTPMethodView

from datasette import __version__
from datasette.utils import (
    CustomJSONEncoder,
    InterruptedError,
    InvalidSql,
    path_from_row_pks,
    path_with_added_args,
    path_with_ext,
    sqlite_timelimit,
    to_css_class
)

connections = threading.local()
ureg = pint.UnitRegistry()

HASH_LENGTH = 7


class DatasetteError(Exception):

    def __init__(self, message, title=None, error_dict=None, status=500, template=None):
        self.message = message
        self.title = title
        self.error_dict = error_dict or {}
        self.status = status


class RenderMixin(HTTPMethodView):

    def render(self, templates, **context):
        template = self.jinja_env.select_template(templates)
        select_templates = [
            "{}{}".format("*" if template_name == template.name else "", template_name)
            for template_name in templates
        ]
        return response.html(
            template.render(
                {
                    **context,
                    **{
                        "app_css_hash": self.ds.app_css_hash(),
                        "select_templates": select_templates,
                        "zip": zip,
                    }
                }
            )
        )


class BaseView(RenderMixin):
    re_named_parameter = re.compile(":([a-zA-Z0-9_]+)")

    def __init__(self, datasette):
        self.ds = datasette
        self.files = datasette.files
        self.jinja_env = datasette.jinja_env
        self.executor = datasette.executor
        self.page_size = datasette.page_size
        self.max_returned_rows = datasette.max_returned_rows

    def table_metadata(self, database, table):
        "Fetch table-specific metadata."
        return self.ds.metadata.get("databases", {}).get(database, {}).get(
            "tables", {}
        ).get(
            table, {}
        )

    def options(self, request, *args, **kwargs):
        r = response.text("ok")
        if self.ds.cors:
            r.headers["Access-Control-Allow-Origin"] = "*"
        return r

    def redirect(self, request, path, forward_querystring=True):
        if request.query_string and "?" not in path and forward_querystring:
            path = "{}?{}".format(path, request.query_string)
        r = response.redirect(path)
        r.headers["Link"] = "<{}>; rel=preload".format(path)
        if self.ds.cors:
            r.headers["Access-Control-Allow-Origin"] = "*"
        return r

    def resolve_db_name(self, db_name, **kwargs):
        databases = self.ds.inspect()
        hash = None
        name = None
        if "-" in db_name:
            # Might be name-and-hash, or might just be
            # a name with a hyphen in it
            name, hash = db_name.rsplit("-", 1)
            if name not in databases:
                # Try the whole name
                name = db_name
                hash = None
        else:
            name = db_name
        # Verify the hash
        try:
            info = databases[name]
        except KeyError:
            raise NotFound("Database not found: {}".format(name))

        expected = info["hash"][:HASH_LENGTH]
        if expected != hash:
            should_redirect = "/{}-{}".format(name, expected)
            if "table" in kwargs:
                should_redirect += "/" + kwargs["table"]
            if "pk_path" in kwargs:
                should_redirect += "/" + kwargs["pk_path"]
            if "as_json" in kwargs:
                should_redirect += kwargs["as_json"]
            if "as_db" in kwargs:
                should_redirect += kwargs["as_db"]
            return name, expected, should_redirect

        return name, expected, None

    async def execute(
        self,
        db_name,
        sql,
        params=None,
        truncate=False,
        custom_time_limit=None,
        page_size=None,
    ):
        """Executes sql against db_name in a thread"""
        page_size = page_size or self.page_size

        def sql_operation_in_thread():
            conn = getattr(connections, db_name, None)
            if not conn:
                info = self.ds.inspect()[db_name]
                conn = sqlite3.connect(
                    "file:{}?immutable=1".format(info["file"]),
                    uri=True,
                    check_same_thread=False,
                )
                self.ds.prepare_connection(conn)
                setattr(connections, db_name, conn)

            time_limit_ms = self.ds.sql_time_limit_ms
            if custom_time_limit and custom_time_limit < self.ds.sql_time_limit_ms:
                time_limit_ms = custom_time_limit

            with sqlite_timelimit(conn, time_limit_ms):
                try:
                    cursor = conn.cursor()
                    cursor.execute(sql, params or {})
                    max_returned_rows = self.max_returned_rows
                    if max_returned_rows == page_size:
                        max_returned_rows += 1
                    if max_returned_rows and truncate:
                        rows = cursor.fetchmany(max_returned_rows + 1)
                        truncated = len(rows) > max_returned_rows
                        rows = rows[:max_returned_rows]
                    else:
                        rows = cursor.fetchall()
                        truncated = False
                except sqlite3.OperationalError as e:
                    if e.args == ('interrupted',):
                        raise InterruptedError(e)
                    print(
                        "ERROR: conn={}, sql = {}, params = {}: {}".format(
                            conn, repr(sql), params, e
                        )
                    )
                    raise

            if truncate:
                return rows, truncated, cursor.description

            else:
                return rows

        return await asyncio.get_event_loop().run_in_executor(
            self.executor, sql_operation_in_thread
        )

    def get_templates(self, database, table=None):
        assert NotImplemented

    async def get(self, request, db_name, **kwargs):
        name, hash, should_redirect = self.resolve_db_name(db_name, **kwargs)
        if should_redirect:
            return self.redirect(request, should_redirect)

        return await self.view_get(request, name, hash, **kwargs)

    async def view_get(self, request, name, hash, **kwargs):
        try:
            as_json = kwargs.pop("as_json")
        except KeyError:
            as_json = False
        extra_template_data = {}
        start = time.time()
        status_code = 200
        templates = []
        try:
            response_or_template_contexts = await self.data(
                request, name, hash, **kwargs
            )
            if isinstance(response_or_template_contexts, response.HTTPResponse):
                return response_or_template_contexts

            else:
                data, extra_template_data, templates = response_or_template_contexts
        except InterruptedError as e:
            raise DatasetteError(str(e), title="SQL Interrupted", status=400)
        except (sqlite3.OperationalError, InvalidSql) as e:
            raise DatasetteError(str(e), title="Invalid SQL", status=400)

        except (sqlite3.OperationalError) as e:
            raise DatasetteError(str(e))

        except DatasetteError:
            raise

        end = time.time()
        data["query_ms"] = (end - start) * 1000
        for key in ("source", "source_url", "license", "license_url"):
            value = self.ds.metadata.get(key)
            if value:
                data[key] = value
        if as_json:
            # Special case for .jsono extension - redirect to _shape=objects
            if as_json == ".jsono":
                return self.redirect(
                    request,
                    path_with_added_args(
                        request,
                        {"_shape": "objects"},
                        path=request.path.rsplit(".jsono", 1)[0] + ".json",
                    ),
                    forward_querystring=False,
                )

            # Deal with the _shape option
            shape = request.args.get("_shape", "arrays")
            if shape in ("objects", "object", "array"):
                columns = data.get("columns")
                rows = data.get("rows")
                if rows and columns:
                    data["rows"] = [dict(zip(columns, row)) for row in rows]
                if shape == "object":
                    error = None
                    if "primary_keys" not in data:
                        error = "_shape=object is only available on tables"
                    else:
                        pks = data["primary_keys"]
                        if not pks:
                            error = "_shape=object not available for tables with no primary keys"
                        else:
                            object_rows = {}
                            for row in data["rows"]:
                                pk_string = path_from_row_pks(row, pks, not pks)
                                object_rows[pk_string] = row
                            data = object_rows
                    if error:
                        data = {
                            "ok": False,
                            "error": error,
                            "database": name,
                            "database_hash": hash,
                        }
                elif shape == "array":
                    data = data["rows"]
            elif shape == "arrays":
                pass
            else:
                status_code = 400
                data = {
                    "ok": False,
                    "error": "Invalid _shape: {}".format(shape),
                    "status": 400,
                    "title": None,
                }
            headers = {}
            if self.ds.cors:
                headers["Access-Control-Allow-Origin"] = "*"
            r = response.HTTPResponse(
                json.dumps(data, cls=CustomJSONEncoder),
                status=status_code,
                content_type="application/json",
                headers=headers,
            )
        else:
            extras = {}
            if callable(extra_template_data):
                extras = extra_template_data()
                if asyncio.iscoroutine(extras):
                    extras = await extras
            else:
                extras = extra_template_data
            context = {
                **data,
                **extras,
                **{
                    "url_json": path_with_ext(request, ".json"),
                    "url_jsono": path_with_ext(request, ".jsono"),
                    "extra_css_urls": self.ds.extra_css_urls(),
                    "extra_js_urls": self.ds.extra_js_urls(),
                    "datasette_version": __version__,
                }
            }
            if "metadata" not in context:
                context["metadata"] = self.ds.metadata
            r = self.render(templates, **context)
            r.status = status_code
        # Set far-future cache expiry
        if self.ds.cache_headers:
            r.headers["Cache-Control"] = "max-age={}".format(365 * 24 * 60 * 60)
        return r

    async def custom_sql(
        self, request, name, hash, sql, editable=True, canned_query=None
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
        rows, truncated, description = await self.execute(
            name, sql, params, truncate=True, **extra_args
        )
        columns = [r[0] for r in description]

        templates = ["query-{}.html".format(to_css_class(name)), "query.html"]
        if canned_query:
            templates.insert(
                0,
                "query-{}-{}.html".format(
                    to_css_class(name), to_css_class(canned_query)
                ),
            )

        return {
            "database": name,
            "rows": rows,
            "truncated": truncated,
            "columns": columns,
            "query": {"sql": sql, "params": params},
        }, {
            "database_hash": hash,
            "custom_sql": True,
            "named_parameter_values": named_parameter_values,
            "editable": editable,
            "canned_query": canned_query,
        }, templates
