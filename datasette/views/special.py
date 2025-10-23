import json
import logging
from datasette.events import LogoutEvent, LoginEvent, CreateTokenEvent
from datasette.utils.asgi import Response, Forbidden
from datasette.utils import (
    actor_matches_allow,
    add_cors_headers,
    await_me_maybe,
    tilde_encode,
    tilde_decode,
)
from datasette.permissions import PermissionSQL
from datasette.utils.permissions import resolve_permissions_from_catalog
from datasette.plugins import pm
from .base import BaseView, View
import secrets
import urllib


logger = logging.getLogger(__name__)


def _resource_path(parent, child):
    if parent is None:
        return "/"
    if child is None:
        return f"/{parent}"
    return f"/{parent}/{child}"


class JsonDataView(BaseView):
    name = "json_data"

    def __init__(
        self,
        datasette,
        filename,
        data_callback,
        needs_request=False,
        permission="view-instance",
    ):
        self.ds = datasette
        self.filename = filename
        self.data_callback = data_callback
        self.needs_request = needs_request
        self.permission = permission

    async def get(self, request):
        if self.permission:
            await self.ds.ensure_permissions(request.actor, [self.permission])
        if self.needs_request:
            data = self.data_callback(request)
        else:
            data = self.data_callback()
        return await self.respond_json_or_html(request, data, self.filename)


class PatternPortfolioView(View):
    async def get(self, request, datasette):
        await datasette.ensure_permissions(request.actor, ["view-instance"])
        return Response.html(
            await datasette.render_template(
                "patterns.html",
                request=request,
                view_name="patterns",
            )
        )


class AuthTokenView(BaseView):
    name = "auth_token"
    has_json_alternate = False

    async def get(self, request):
        # If already signed in as root, redirect
        if request.actor and request.actor.get("id") == "root":
            return Response.redirect(self.ds.urls.instance())
        token = request.args.get("token") or ""
        if not self.ds._root_token:
            raise Forbidden("Root token has already been used")
        if secrets.compare_digest(token, self.ds._root_token):
            self.ds._root_token = None
            response = Response.redirect(self.ds.urls.instance())
            root_actor = {"id": "root"}
            self.ds.set_actor_cookie(response, root_actor)
            await self.ds.track_event(LoginEvent(actor=root_actor))
            return response
        else:
            raise Forbidden("Invalid token")


class LogoutView(BaseView):
    name = "logout"
    has_json_alternate = False

    async def get(self, request):
        if not request.actor:
            return Response.redirect(self.ds.urls.instance())
        return await self.render(
            ["logout.html"],
            request,
            {"actor": request.actor},
        )

    async def post(self, request):
        response = Response.redirect(self.ds.urls.instance())
        self.ds.delete_actor_cookie(response)
        self.ds.add_message(request, "You are now logged out", self.ds.WARNING)
        await self.ds.track_event(LogoutEvent(actor=request.actor))
        return response


class PermissionsDebugView(BaseView):
    name = "permissions_debug"
    has_json_alternate = False

    async def get(self, request):
        await self.ds.ensure_permissions(request.actor, ["view-instance"])
        if not await self.ds.permission_allowed(request.actor, "permissions-debug"):
            raise Forbidden("Permission denied")
        filter_ = request.args.get("filter") or "all"
        permission_checks = list(reversed(self.ds._permission_checks))
        if filter_ == "exclude-yours":
            permission_checks = [
                check
                for check in permission_checks
                if (check["actor"] or {}).get("id") != request.actor["id"]
            ]
        elif filter_ == "only-yours":
            permission_checks = [
                check
                for check in permission_checks
                if (check["actor"] or {}).get("id") == request.actor["id"]
            ]
        return await self.render(
            ["permissions_debug.html"],
            request,
            # list() avoids error if check is performed during template render:
            {
                "permission_checks": permission_checks,
                "filter": filter_,
                "permissions": [
                    {
                        "name": p.name,
                        "abbr": p.abbr,
                        "description": p.description,
                        "takes_database": p.takes_database,
                        "takes_resource": p.takes_resource,
                        "default": p.default,
                    }
                    for p in self.ds.permissions.values()
                ],
            },
        )

    async def post(self, request):
        await self.ds.ensure_permissions(request.actor, ["view-instance"])
        if not await self.ds.permission_allowed(request.actor, "permissions-debug"):
            raise Forbidden("Permission denied")
        vars = await request.post_vars()
        actor = json.loads(vars["actor"])
        permission = vars["permission"]
        resource_1 = vars["resource_1"]
        resource_2 = vars["resource_2"]
        resource = []
        if resource_1:
            resource.append(resource_1)
        if resource_2:
            resource.append(resource_2)
        resource = tuple(resource)
        if len(resource) == 1:
            resource = resource[0]
        result = await self.ds.permission_allowed(
            actor, permission, resource, default="USE_DEFAULT"
        )
        return Response.json(
            {
                "actor": actor,
                "permission": permission,
                "resource": resource,
                "result": result,
                "default": self.ds.permissions[permission].default,
            }
        )


class AllowedResourcesView(BaseView):
    name = "allowed"
    has_json_alternate = False

    CANDIDATE_SQL = {
        "view-table": (
            "SELECT database_name AS parent, table_name AS child FROM catalog_tables",
            {},
        ),
        "view-database": (
            "SELECT database_name AS parent, NULL AS child FROM catalog_databases",
            {},
        ),
        "view-instance": ("SELECT NULL AS parent, NULL AS child", {}),
        "execute-sql": (
            "SELECT database_name AS parent, NULL AS child FROM catalog_databases",
            {},
        ),
    }

    async def get(self, request):
        await self.ds.refresh_schemas()

        # Check if user has permissions-debug (to show sensitive fields)
        has_debug_permission = await self.ds.permission_allowed(
            request.actor, "permissions-debug"
        )

        # Check if this is a request for JSON (has .json extension)
        as_format = request.url_vars.get("format")

        if not as_format:
            # Render the HTML form (even if query parameters are present)
            return await self.render(
                ["debug_allowed.html"],
                request,
                {
                    "supported_actions": sorted(self.CANDIDATE_SQL.keys()),
                },
            )

        # JSON API - action parameter is required
        action = request.args.get("action")
        if not action:
            return Response.json({"error": "action parameter is required"}, status=400)
        if action not in self.ds.permissions:
            return Response.json({"error": f"Unknown action: {action}"}, status=404)
        if action not in self.CANDIDATE_SQL:
            return Response.json(
                {"error": f"Action '{action}' is not supported by this endpoint"},
                status=400,
            )

        actor = request.actor if isinstance(request.actor, dict) else None
        actor_id = actor.get("id") if actor else None
        parent_filter = request.args.get("parent")
        child_filter = request.args.get("child")
        if child_filter and not parent_filter:
            return Response.json(
                {"error": "parent must be provided when child is specified"},
                status=400,
            )

        try:
            page = int(request.args.get("page", "1"))
            page_size = int(request.args.get("page_size", "50"))
        except ValueError:
            return Response.json(
                {"error": "page and page_size must be integers"}, status=400
            )
        if page < 1:
            return Response.json({"error": "page must be >= 1"}, status=400)
        if page_size < 1:
            return Response.json({"error": "page_size must be >= 1"}, status=400)
        max_page_size = 200
        if page_size > max_page_size:
            page_size = max_page_size
        offset = (page - 1) * page_size

        candidate_sql, candidate_params = self.CANDIDATE_SQL[action]

        db = self.ds.get_internal_database()
        required_tables = set()
        if "catalog_tables" in candidate_sql:
            required_tables.add("catalog_tables")
        if "catalog_databases" in candidate_sql:
            required_tables.add("catalog_databases")

        for table in required_tables:
            if not await db.table_exists(table):
                headers = {}
                if self.ds.cors:
                    add_cors_headers(headers)
                return Response.json(
                    {
                        "action": action,
                        "actor_id": (actor or {}).get("id") if actor else None,
                        "page": page,
                        "page_size": page_size,
                        "total": 0,
                        "items": [],
                    },
                    headers=headers,
                )

        plugins = []
        for block in pm.hook.permission_resources_sql(
            datasette=self.ds,
            actor=actor,
            action=action,
        ):
            block = await await_me_maybe(block)
            if block is None:
                continue
            if isinstance(block, (list, tuple)):
                candidates = block
            else:
                candidates = [block]
            for candidate in candidates:
                if candidate is None:
                    continue
                plugins.append(candidate)

        rows = await resolve_permissions_from_catalog(
            db,
            actor=actor,
            plugins=plugins,
            action=action,
            candidate_sql=candidate_sql,
            candidate_params=candidate_params,
            implicit_deny=True,
        )

        allowed_rows = [row for row in rows if row["allow"] == 1]
        if parent_filter is not None:
            allowed_rows = [
                row for row in allowed_rows if row["parent"] == parent_filter
            ]
        if child_filter is not None:
            allowed_rows = [row for row in allowed_rows if row["child"] == child_filter]
        total = len(allowed_rows)
        paged_rows = allowed_rows[offset : offset + page_size]

        items = []
        for row in paged_rows:
            item = {
                "parent": row["parent"],
                "child": row["child"],
                "resource": row["resource"],
            }
            # Only include sensitive fields if user has permissions-debug
            if has_debug_permission:
                item["reason"] = row["reason"]
                item["source_plugin"] = row["source_plugin"]
            items.append(item)

        def build_page_url(page_number):
            pairs = []
            for key in request.args:
                if key in {"page", "page_size"}:
                    continue
                for value in request.args.getlist(key):
                    pairs.append((key, value))
            pairs.append(("page", str(page_number)))
            pairs.append(("page_size", str(page_size)))
            query = urllib.parse.urlencode(pairs)
            return f"{request.path}?{query}"

        response = {
            "action": action,
            "actor_id": actor_id,
            "page": page,
            "page_size": page_size,
            "total": total,
            "items": items,
        }

        if total > offset + page_size:
            response["next_url"] = build_page_url(page + 1)
        if page > 1:
            response["previous_url"] = build_page_url(page - 1)

        headers = {}
        if self.ds.cors:
            add_cors_headers(headers)
        return Response.json(response, headers=headers)


class PermissionRulesView(BaseView):
    name = "permission_rules"
    has_json_alternate = False

    async def get(self, request):
        await self.ds.ensure_permissions(request.actor, ["view-instance"])
        if not await self.ds.permission_allowed(request.actor, "permissions-debug"):
            raise Forbidden("Permission denied")

        # Check if this is a request for JSON (has .json extension)
        as_format = request.url_vars.get("format")

        if not as_format:
            # Render the HTML form (even if query parameters are present)
            return await self.render(
                ["debug_rules.html"],
                request,
                {
                    "sorted_permissions": sorted(self.ds.permissions.keys()),
                },
            )

        # JSON API - action parameter is required
        action = request.args.get("action")
        if not action:
            return Response.json({"error": "action parameter is required"}, status=400)
        if action not in self.ds.permissions:
            return Response.json({"error": f"Unknown action: {action}"}, status=404)

        actor = request.actor if isinstance(request.actor, dict) else None

        try:
            page = int(request.args.get("page", "1"))
            page_size = int(request.args.get("page_size", "50"))
        except ValueError:
            return Response.json(
                {"error": "page and page_size must be integers"}, status=400
            )
        if page < 1:
            return Response.json({"error": "page must be >= 1"}, status=400)
        if page_size < 1:
            return Response.json({"error": "page_size must be >= 1"}, status=400)
        max_page_size = 200
        if page_size > max_page_size:
            page_size = max_page_size
        offset = (page - 1) * page_size

        union_sql, union_params = await self.ds._build_permission_rules_sql(
            actor, action
        )
        await self.ds.refresh_schemas()
        db = self.ds.get_internal_database()

        count_query = f"""
        WITH rules AS (
            {union_sql}
        )
        SELECT COUNT(*) AS count
        FROM rules
        """
        count_row = (await db.execute(count_query, union_params)).first()
        total = count_row["count"] if count_row else 0

        data_query = f"""
        WITH rules AS (
            {union_sql}
        )
        SELECT parent, child, allow, reason, source_plugin
        FROM rules
        ORDER BY allow DESC, (parent IS NOT NULL), parent, child
        LIMIT :limit OFFSET :offset
        """
        params = {**union_params, "limit": page_size, "offset": offset}
        rows = await db.execute(data_query, params)

        items = []
        for row in rows:
            parent = row["parent"]
            child = row["child"]
            items.append(
                {
                    "parent": parent,
                    "child": child,
                    "resource": _resource_path(parent, child),
                    "allow": row["allow"],
                    "reason": row["reason"],
                    "source_plugin": row["source_plugin"],
                }
            )

        def build_page_url(page_number):
            pairs = []
            for key in request.args:
                if key in {"page", "page_size"}:
                    continue
                for value in request.args.getlist(key):
                    pairs.append((key, value))
            pairs.append(("page", str(page_number)))
            pairs.append(("page_size", str(page_size)))
            query = urllib.parse.urlencode(pairs)
            return f"{request.path}?{query}"

        response = {
            "action": action,
            "actor_id": (actor or {}).get("id") if actor else None,
            "page": page,
            "page_size": page_size,
            "total": total,
            "items": items,
        }

        if total > offset + page_size:
            response["next_url"] = build_page_url(page + 1)
        if page > 1:
            response["previous_url"] = build_page_url(page - 1)

        headers = {}
        if self.ds.cors:
            add_cors_headers(headers)
        return Response.json(response, headers=headers)


class PermissionCheckView(BaseView):
    name = "permission_check"
    has_json_alternate = False

    async def get(self, request):
        # Check if user has permissions-debug (to show sensitive fields)
        has_debug_permission = await self.ds.permission_allowed(
            request.actor, "permissions-debug"
        )

        # Check if this is a request for JSON (has .json extension)
        as_format = request.url_vars.get("format")

        if not as_format:
            # Render the HTML form (even if query parameters are present)
            return await self.render(
                ["debug_check.html"],
                request,
                {
                    "sorted_permissions": sorted(self.ds.permissions.keys()),
                },
            )

        # JSON API - action parameter is required
        action = request.args.get("action")
        if not action:
            return Response.json({"error": "action parameter is required"}, status=400)
        if action not in self.ds.permissions:
            return Response.json({"error": f"Unknown action: {action}"}, status=404)

        parent = request.args.get("parent")
        child = request.args.get("child")
        if child and not parent:
            return Response.json(
                {"error": "parent is required when child is provided"}, status=400
            )

        if parent and child:
            resource = (parent, child)
        elif parent:
            resource = parent
        else:
            resource = None

        before_checks = len(self.ds._permission_checks)
        allowed = await self.ds.permission_allowed_2(request.actor, action, resource)

        info = None
        if len(self.ds._permission_checks) > before_checks:
            for check in reversed(self.ds._permission_checks):
                if (
                    check.get("actor") == request.actor
                    and check.get("action") == action
                    and check.get("resource") == resource
                ):
                    info = check
                    break

        response = {
            "action": action,
            "allowed": bool(allowed),
            "resource": {
                "parent": parent,
                "child": child,
                "path": _resource_path(parent, child),
            },
        }

        if request.actor and "id" in request.actor:
            response["actor_id"] = request.actor["id"]

        if info is not None:
            response["used_default"] = info.get("used_default")
            response["depth"] = info.get("depth")
            # Only include sensitive fields if user has permissions-debug
            if has_debug_permission:
                response["reason"] = info.get("reason")
                response["source_plugin"] = info.get("source_plugin")

        return Response.json(response)


class AllowDebugView(BaseView):
    name = "allow_debug"
    has_json_alternate = False

    async def get(self, request):
        errors = []
        actor_input = request.args.get("actor") or '{"id": "root"}'
        try:
            actor = json.loads(actor_input)
            actor_input = json.dumps(actor, indent=4)
        except json.decoder.JSONDecodeError as ex:
            errors.append(f"Actor JSON error: {ex}")
        allow_input = request.args.get("allow") or '{"id": "*"}'
        try:
            allow = json.loads(allow_input)
            allow_input = json.dumps(allow, indent=4)
        except json.decoder.JSONDecodeError as ex:
            errors.append(f"Allow JSON error: {ex}")

        result = None
        if not errors:
            result = str(actor_matches_allow(actor, allow))

        return await self.render(
            ["allow_debug.html"],
            request,
            {
                "result": result,
                "error": "\n\n".join(errors) if errors else "",
                "actor_input": actor_input,
                "allow_input": allow_input,
            },
        )


class MessagesDebugView(BaseView):
    name = "messages_debug"
    has_json_alternate = False

    async def get(self, request):
        await self.ds.ensure_permissions(request.actor, ["view-instance"])
        return await self.render(["messages_debug.html"], request)

    async def post(self, request):
        await self.ds.ensure_permissions(request.actor, ["view-instance"])
        post = await request.post_vars()
        message = post.get("message", "")
        message_type = post.get("message_type") or "INFO"
        assert message_type in ("INFO", "WARNING", "ERROR", "all")
        datasette = self.ds
        if message_type == "all":
            datasette.add_message(request, message, datasette.INFO)
            datasette.add_message(request, message, datasette.WARNING)
            datasette.add_message(request, message, datasette.ERROR)
        else:
            datasette.add_message(request, message, getattr(datasette, message_type))
        return Response.redirect(self.ds.urls.instance())


class CreateTokenView(BaseView):
    name = "create_token"
    has_json_alternate = False

    def check_permission(self, request):
        if not self.ds.setting("allow_signed_tokens"):
            raise Forbidden("Signed tokens are not enabled for this Datasette instance")
        if not request.actor:
            raise Forbidden("You must be logged in to create a token")
        if not request.actor.get("id"):
            raise Forbidden(
                "You must be logged in as an actor with an ID to create a token"
            )
        if request.actor.get("token"):
            raise Forbidden(
                "Token authentication cannot be used to create additional tokens"
            )

    async def shared(self, request):
        self.check_permission(request)
        # Build list of databases and tables the user has permission to view
        database_with_tables = []
        for database in self.ds.databases.values():
            if database.name == "_memory":
                continue
            if not await self.ds.permission_allowed(
                request.actor, "view-database", database.name
            ):
                continue
            hidden_tables = await database.hidden_table_names()
            tables = []
            for table in await database.table_names():
                if table in hidden_tables:
                    continue
                if not await self.ds.permission_allowed(
                    request.actor,
                    "view-table",
                    resource=(database.name, table),
                ):
                    continue
                tables.append({"name": table, "encoded": tilde_encode(table)})
            database_with_tables.append(
                {
                    "name": database.name,
                    "encoded": tilde_encode(database.name),
                    "tables": tables,
                }
            )
        return {
            "actor": request.actor,
            "all_permissions": self.ds.permissions.keys(),
            "database_permissions": [
                key
                for key, value in self.ds.permissions.items()
                if value.takes_database
            ],
            "resource_permissions": [
                key
                for key, value in self.ds.permissions.items()
                if value.takes_resource
            ],
            "database_with_tables": database_with_tables,
        }

    async def get(self, request):
        self.check_permission(request)
        return await self.render(
            ["create_token.html"], request, await self.shared(request)
        )

    async def post(self, request):
        self.check_permission(request)
        post = await request.post_vars()
        errors = []
        expires_after = None
        if post.get("expire_type"):
            duration_string = post.get("expire_duration")
            if (
                not duration_string
                or not duration_string.isdigit()
                or not int(duration_string) > 0
            ):
                errors.append("Invalid expire duration")
            else:
                unit = post["expire_type"]
                if unit == "minutes":
                    expires_after = int(duration_string) * 60
                elif unit == "hours":
                    expires_after = int(duration_string) * 60 * 60
                elif unit == "days":
                    expires_after = int(duration_string) * 60 * 60 * 24
                else:
                    errors.append("Invalid expire duration unit")

        # Are there any restrictions?
        restrict_all = []
        restrict_database = {}
        restrict_resource = {}

        for key in post:
            if key.startswith("all:") and key.count(":") == 1:
                restrict_all.append(key.split(":")[1])
            elif key.startswith("database:") and key.count(":") == 2:
                bits = key.split(":")
                database = tilde_decode(bits[1])
                action = bits[2]
                restrict_database.setdefault(database, []).append(action)
            elif key.startswith("resource:") and key.count(":") == 3:
                bits = key.split(":")
                database = tilde_decode(bits[1])
                resource = tilde_decode(bits[2])
                action = bits[3]
                restrict_resource.setdefault(database, {}).setdefault(
                    resource, []
                ).append(action)

        token = self.ds.create_token(
            request.actor["id"],
            expires_after=expires_after,
            restrict_all=restrict_all,
            restrict_database=restrict_database,
            restrict_resource=restrict_resource,
        )
        token_bits = self.ds.unsign(token[len("dstok_") :], namespace="token")
        await self.ds.track_event(
            CreateTokenEvent(
                actor=request.actor,
                expires_after=expires_after,
                restrict_all=restrict_all,
                restrict_database=restrict_database,
                restrict_resource=restrict_resource,
            )
        )
        context = await self.shared(request)
        context.update({"errors": errors, "token": token, "token_bits": token_bits})
        return await self.render(["create_token.html"], request, context)


class ApiExplorerView(BaseView):
    name = "api_explorer"
    has_json_alternate = False

    async def example_links(self, request):
        databases = []
        for name, db in self.ds.databases.items():
            if name == "_internal":
                continue
            database_visible, _ = await self.ds.check_visibility(
                request.actor, permissions=[("view-database", name), "view-instance"]
            )
            if not database_visible:
                continue
            tables = []
            table_names = await db.table_names()
            for table in table_names:
                visible, _ = await self.ds.check_visibility(
                    request.actor,
                    permissions=[
                        ("view-table", (name, table)),
                        ("view-database", name),
                        "view-instance",
                    ],
                )
                if not visible:
                    continue
                table_links = []
                tables.append({"name": table, "links": table_links})
                table_links.append(
                    {
                        "label": "Get rows for {}".format(table),
                        "method": "GET",
                        "path": self.ds.urls.table(name, table, format="json"),
                    }
                )
                # If not mutable don't show any write APIs
                if not db.is_mutable:
                    continue

                if await self.ds.permission_allowed(
                    request.actor, "insert-row", (name, table)
                ):
                    pks = await db.primary_keys(table)
                    table_links.extend(
                        [
                            {
                                "path": self.ds.urls.table(name, table) + "/-/insert",
                                "method": "POST",
                                "label": "Insert rows into {}".format(table),
                                "json": {
                                    "rows": [
                                        {
                                            column: None
                                            for column in await db.table_columns(table)
                                            if column not in pks
                                        }
                                    ]
                                },
                            },
                            {
                                "path": self.ds.urls.table(name, table) + "/-/upsert",
                                "method": "POST",
                                "label": "Upsert rows into {}".format(table),
                                "json": {
                                    "rows": [
                                        {
                                            column: None
                                            for column in await db.table_columns(table)
                                            if column not in pks
                                        }
                                    ]
                                },
                            },
                        ]
                    )
                if await self.ds.permission_allowed(
                    request.actor, "drop-table", (name, table)
                ):
                    table_links.append(
                        {
                            "path": self.ds.urls.table(name, table) + "/-/drop",
                            "label": "Drop table {}".format(table),
                            "json": {"confirm": False},
                            "method": "POST",
                        }
                    )
            database_links = []
            if (
                await self.ds.permission_allowed(request.actor, "create-table", name)
                and db.is_mutable
            ):
                database_links.append(
                    {
                        "path": self.ds.urls.database(name) + "/-/create",
                        "label": "Create table in {}".format(name),
                        "json": {
                            "table": "new_table",
                            "columns": [
                                {"name": "id", "type": "integer"},
                                {"name": "name", "type": "text"},
                            ],
                            "pk": "id",
                        },
                        "method": "POST",
                    }
                )
            if database_links or tables:
                databases.append(
                    {
                        "name": name,
                        "links": database_links,
                        "tables": tables,
                    }
                )
        # Sort so that mutable databases are first
        databases.sort(key=lambda d: not self.ds.databases[d["name"]].is_mutable)
        return databases

    async def get(self, request):
        visible, private = await self.ds.check_visibility(
            request.actor,
            permissions=["view-instance"],
        )
        if not visible:
            raise Forbidden("You do not have permission to view this instance")

        def api_path(link):
            return "/-/api#{}".format(
                urllib.parse.urlencode(
                    {
                        key: json.dumps(value, indent=2) if key == "json" else value
                        for key, value in link.items()
                        if key in ("path", "method", "json")
                    }
                )
            )

        return await self.render(
            ["api_explorer.html"],
            request,
            {
                "example_links": await self.example_links(request),
                "api_path": api_path,
                "private": private,
            },
        )


class TablesView(BaseView):
    """
    Simple endpoint that uses the new allowed_resources() API.
    Returns JSON list of all tables the actor can view.

    Supports ?q=foo+bar to filter tables matching .*foo.*bar.* pattern,
    ordered by shortest name first.
    """

    name = "tables"
    has_json_alternate = False

    async def get(self, request):
        # Get search query parameter
        q = request.args.get("q", "").strip()

        # Only return matches if there's a non-empty search query
        if not q:
            return Response.json({"matches": []})

        # Build SQL LIKE pattern from search terms
        # Split search terms by whitespace and build pattern: %term1%term2%term3%
        terms = q.split()
        pattern = "%" + "%".join(terms) + "%"

        # Get SQL for allowed resources using the permission system
        permission_sql, params = await self.ds.allowed_resources_sql(
            action="view-table", actor=request.actor
        )

        # Build query with CTE to filter by search pattern
        sql = f"""
        WITH allowed_tables AS (
            {permission_sql}
        )
        SELECT parent, child
        FROM allowed_tables
        WHERE child LIKE :pattern COLLATE NOCASE
        ORDER BY length(child), child
        """

        # Merge params from permission SQL with our pattern param
        all_params = {**params, "pattern": pattern}

        # Execute against internal database
        result = await self.ds.get_internal_database().execute(sql, all_params)

        # Build response
        matches = [
            {
                "name": f"{row['parent']}: {row['child']}",
                "url": self.ds.urls.table(row["parent"], row["child"]),
            }
            for row in result.rows
        ]

        return Response.json({"matches": matches})
