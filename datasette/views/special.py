import json
import logging
from datasette.events import LogoutEvent, LoginEvent, CreateTokenEvent
from datasette.resources import DatabaseResource, TableResource, InstanceResource
from datasette.utils.asgi import Response, Forbidden
from datasette.utils import (
    actor_matches_allow,
    add_cors_headers,
    tilde_encode,
    tilde_decode,
)
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
    template = "show_json.html"  # Can be overridden in subclasses

    def __init__(
        self,
        datasette,
        filename,
        data_callback,
        needs_request=False,
        permission="view-instance",
        template=None,
    ):
        self.ds = datasette
        self.filename = filename
        self.data_callback = data_callback
        self.needs_request = needs_request
        self.permission = permission
        if template is not None:
            self.template = template

    async def get(self, request):
        if self.permission:
            await self.ds.ensure_permission(action=self.permission, actor=request.actor)
        if self.needs_request:
            data = self.data_callback(request)
        else:
            data = self.data_callback()

        # Return JSON or HTML depending on format parameter
        as_format = request.url_vars.get("format")
        if as_format:
            headers = {}
            if self.ds.cors:
                add_cors_headers(headers)
            return Response.json(data, headers=headers)
        else:
            return await self.render(
                [self.template],
                request=request,
                context={
                    "filename": self.filename,
                    "data": data,
                    "data_json": json.dumps(data, indent=4, default=repr),
                },
            )


class PatternPortfolioView(View):
    async def get(self, request, datasette):
        await datasette.ensure_permission(action="view-instance", actor=request.actor)
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
        await self.ds.ensure_permission(action="view-instance", actor=request.actor)
        await self.ds.ensure_permission(action="permissions-debug", actor=request.actor)
        filter_ = request.args.get("filter") or "all"
        permission_checks = list(reversed(self.ds._permission_checks))
        if filter_ == "exclude-yours":
            permission_checks = [
                check
                for check in permission_checks
                if (check.actor or {}).get("id") != request.actor["id"]
            ]
        elif filter_ == "only-yours":
            permission_checks = [
                check
                for check in permission_checks
                if (check.actor or {}).get("id") == request.actor["id"]
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
                        "takes_parent": p.takes_parent,
                        "takes_child": p.takes_child,
                    }
                    for p in self.ds.actions.values()
                ],
            },
        )

    async def post(self, request):
        await self.ds.ensure_permission(action="view-instance", actor=request.actor)
        await self.ds.ensure_permission(action="permissions-debug", actor=request.actor)
        vars = await request.post_vars()
        actor = json.loads(vars["actor"])
        permission = vars["permission"]
        resource_1 = vars["resource_1"]
        resource_2 = vars["resource_2"]

        # Use the action's properties to create the appropriate resource object
        action = self.ds.actions.get(permission)
        if not action:
            return Response.json({"error": f"Unknown action: {permission}"}, status=400)

        if action.takes_parent and action.takes_child:
            resource_obj = action.resource_class(database=resource_1, table=resource_2)
            resource_for_response = (resource_1, resource_2)
        elif action.takes_parent:
            resource_obj = action.resource_class(database=resource_1)
            resource_for_response = resource_1
        else:
            resource_obj = action.resource_class()
            resource_for_response = None

        result = await self.ds.allowed(
            action=permission, resource=resource_obj, actor=actor
        )
        return Response.json(
            {
                "actor": actor,
                "permission": permission,
                "resource": resource_for_response,
                "result": result,
            }
        )


class AllowedResourcesView(BaseView):
    name = "allowed"
    has_json_alternate = False

    async def get(self, request):
        await self.ds.refresh_schemas()

        # Check if user has permissions-debug (to show sensitive fields)
        has_debug_permission = await self.ds.allowed(
            action="permissions-debug", actor=request.actor
        )

        # Check if this is a request for JSON (has .json extension)
        as_format = request.url_vars.get("format")

        if not as_format:
            # Render the HTML form (even if query parameters are present)
            # Put most common/interesting actions first
            priority_actions = [
                "view-instance",
                "view-database",
                "view-table",
                "view-query",
                "execute-sql",
                "insert-row",
                "update-row",
                "delete-row",
            ]
            actions = list(self.ds.actions.keys())
            # Priority actions first (in order), then remaining alphabetically
            sorted_actions = [a for a in priority_actions if a in actions]
            sorted_actions.extend(
                sorted(a for a in actions if a not in priority_actions)
            )

            return await self.render(
                ["debug_allowed.html"],
                request,
                {
                    "supported_actions": sorted_actions,
                    "has_debug_permission": has_debug_permission,
                },
            )

        # JSON API - action parameter is required
        action = request.args.get("action")
        if not action:
            return Response.json({"error": "action parameter is required"}, status=400)
        if action not in self.ds.actions:
            return Response.json({"error": f"Unknown action: {action}"}, status=404)

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

        # Use the simplified allowed_resources method
        # If user has debug permission, use the with_reasons variant
        try:
            if has_debug_permission:
                allowed_resources = await self.ds.allowed_resources_with_reasons(
                    action=action,
                    actor=actor,
                )
            else:
                allowed_resources = await self.ds.allowed_resources(
                    action=action,
                    actor=actor,
                    parent=parent_filter,
                )
        except Exception:
            # If catalog tables don't exist yet, return empty results
            headers = {}
            if self.ds.cors:
                add_cors_headers(headers)
            return Response.json(
                {
                    "action": action,
                    "actor_id": actor_id,
                    "page": page,
                    "page_size": page_size,
                    "total": 0,
                    "items": [],
                },
                headers=headers,
            )

        # Convert to list of dicts with resource path
        allowed_rows = []
        for item in allowed_resources:
            # Extract resource and reason depending on what we got back
            if has_debug_permission:
                # allowed_resources_with_reasons returns AllowedResource(resource, reason)
                resource = item.resource
                reason = item.reason
            else:
                # allowed_resources returns plain Resource objects
                resource = item
                reason = None

            parent_val = resource.parent
            child_val = resource.child

            # Apply parent filter if needed (when using with_reasons, we need to filter manually)
            if parent_filter is not None and parent_val != parent_filter:
                continue

            # Build resource path
            if parent_val is None:
                resource_path = "/"
            elif child_val is None:
                resource_path = f"/{parent_val}"
            else:
                resource_path = f"/{parent_val}/{child_val}"

            row = {
                "parent": parent_val,
                "child": child_val,
                "resource": resource_path,
            }

            # Add reason if we have it (it's already a list from allowed_resources_with_reasons)
            if reason is not None:
                row["reason"] = reason

            allowed_rows.append(row)

        # Apply child filter if specified
        if child_filter is not None:
            allowed_rows = [row for row in allowed_rows if row["child"] == child_filter]

        # Pagination
        total = len(allowed_rows)
        paged_rows = allowed_rows[offset : offset + page_size]

        # Items are already in the right format
        items = paged_rows

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
        await self.ds.ensure_permission(action="view-instance", actor=request.actor)
        await self.ds.ensure_permission(action="permissions-debug", actor=request.actor)

        # Check if this is a request for JSON (has .json extension)
        as_format = request.url_vars.get("format")

        if not as_format:
            # Render the HTML form (even if query parameters are present)
            return await self.render(
                ["debug_rules.html"],
                request,
                {
                    "sorted_actions": sorted(self.ds.actions.keys()),
                },
            )

        # JSON API - action parameter is required
        action = request.args.get("action")
        if not action:
            return Response.json({"error": "action parameter is required"}, status=400)
        if action not in self.ds.actions:
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

        from datasette.utils.actions_sql import build_permission_rules_sql

        union_sql, union_params = await build_permission_rules_sql(
            self.ds, actor, action
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
        SELECT parent, child, allow, reason
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
        await self.ds.ensure_permission(action="permissions-debug", actor=request.actor)
        as_format = request.url_vars.get("format")

        if not as_format:
            return await self.render(
                ["debug_check.html"],
                request,
                {
                    "sorted_actions": sorted(self.ds.actions.keys()),
                },
            )

        # JSON API - action parameter is required
        action = request.args.get("action")
        if not action:
            return Response.json({"error": "action parameter is required"}, status=400)
        if action not in self.ds.actions:
            return Response.json({"error": f"Unknown action: {action}"}, status=404)

        parent = request.args.get("parent")
        child = request.args.get("child")
        if child and not parent:
            return Response.json(
                {"error": "parent is required when child is provided"}, status=400
            )

        # Use the action's properties to create the appropriate resource object
        action_obj = self.ds.actions.get(action)
        if not action_obj:
            return Response.json({"error": f"Unknown action: {action}"}, status=400)

        if action_obj.takes_parent and action_obj.takes_child:
            resource_obj = action_obj.resource_class(database=parent, table=child)
            resource = (parent, child)
        elif action_obj.takes_parent:
            resource_obj = action_obj.resource_class(database=parent)
            resource = parent
        else:
            resource_obj = action_obj.resource_class()
            resource = None

        before_checks = len(self.ds._permission_checks)
        allowed = await self.ds.allowed(
            action=action, resource=resource_obj, actor=request.actor
        )

        info = None
        if len(self.ds._permission_checks) > before_checks:
            for check in reversed(self.ds._permission_checks):
                if (
                    check.actor == request.actor
                    and check.action == action
                    and check.parent == parent
                    and check.child == child
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
        await self.ds.ensure_permission(action="view-instance", actor=request.actor)
        return await self.render(["messages_debug.html"], request)

    async def post(self, request):
        await self.ds.ensure_permission(action="view-instance", actor=request.actor)
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
        allowed_databases = await self.ds.allowed_resources(
            "view-database", request.actor
        )
        allowed_tables = await self.ds.allowed_resources("view-table", request.actor)

        # Build database -> tables mapping
        database_with_tables = []
        for db_resource in allowed_databases:
            database_name = db_resource.parent
            if database_name == "_memory":
                continue

            # Find tables for this database
            tables = []
            for table_resource in allowed_tables:
                if table_resource.parent == database_name:
                    tables.append(
                        {
                            "name": table_resource.child,
                            "encoded": tilde_encode(table_resource.child),
                        }
                    )

            database_with_tables.append(
                {
                    "name": database_name,
                    "encoded": tilde_encode(database_name),
                    "tables": tables,
                }
            )
        return {
            "actor": request.actor,
            "all_actions": self.ds.actions.keys(),
            "database_actions": [
                key for key, value in self.ds.actions.items() if value.takes_parent
            ],
            "child_actions": [
                key for key, value in self.ds.actions.items() if value.takes_child
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
                request.actor,
                action="view-database",
                resource=DatabaseResource(database=name),
            )
            if not database_visible:
                continue
            tables = []
            table_names = await db.table_names()
            for table in table_names:
                visible, _ = await self.ds.check_visibility(
                    request.actor,
                    action="view-table",
                    resource=TableResource(database=name, table=table),
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

                if await self.ds.allowed(
                    action="insert-row",
                    resource=TableResource(database=name, table=table),
                    actor=request.actor,
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
                if await self.ds.allowed(
                    action="drop-table",
                    resource=TableResource(database=name, table=table),
                    actor=request.actor,
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
                await self.ds.allowed(
                    action="create-table",
                    resource=DatabaseResource(database=name),
                    actor=request.actor,
                )
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
            action="view-instance",
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

        # Get SQL for allowed resources using the permission system
        permission_sql, params = await self.ds.allowed_resources_sql(
            action="view-table", actor=request.actor
        )

        # Build query based on whether we have a search query
        if q:
            # Build SQL LIKE pattern from search terms
            # Split search terms by whitespace and build pattern: %term1%term2%term3%
            terms = q.split()
            pattern = "%" + "%".join(terms) + "%"

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
            all_params = {**params, "pattern": pattern}
        else:
            # No search query - return all tables, ordered by name
            # Fetch 101 to detect if we need to truncate
            sql = f"""
            WITH allowed_tables AS (
                {permission_sql}
            )
            SELECT parent, child
            FROM allowed_tables
            ORDER BY parent, child
            LIMIT 101
            """
            all_params = params

        # Execute against internal database
        result = await self.ds.get_internal_database().execute(sql, all_params)

        # Build response with truncation
        rows = list(result.rows)
        truncated = len(rows) > 100
        if truncated:
            rows = rows[:100]

        matches = [
            {
                "name": f"{row['parent']}: {row['child']}",
                "url": self.ds.urls.table(row["parent"], row["child"]),
            }
            for row in rows
        ]

        return Response.json({"matches": matches, "truncated": truncated})
