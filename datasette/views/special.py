import json
import logging
from datasette.jump import JumpSQL, namespace_sql_params
from datasette.plugins import pm
from datasette.events import LogoutEvent, LoginEvent, CreateTokenEvent
from datasette.resources import DatabaseResource, TableResource
from datasette.utils.asgi import Response, Forbidden
from datasette.utils import (
    UNSTABLE_API_MESSAGE,
    actor_matches_allow,
    add_cors_headers,
    await_me_maybe,
    error_body,
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
            data = await await_me_maybe(self.data_callback(request))
        else:
            data = await await_me_maybe(self.data_callback())

        # Return JSON or HTML depending on format parameter
        as_format = request.url_vars.get("format")
        if as_format:
            headers = {}
            if self.ds.cors:
                add_cors_headers(headers)
            if isinstance(data, dict):
                data = {"ok": True, **data}
            return Response.json(data, headers=headers)
        else:
            context = {
                "filename": self.filename,
                "data": data,
                "data_json": json.dumps(data, indent=2, default=repr),
            }
            # Add has_debug_permission if this view requires permissions-debug
            if self.permission == "permissions-debug":
                context["has_debug_permission"] = True
            return await self.render(
                [self.template],
                request=request,
                context=context,
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


class AutocompleteDebugView(BaseView):
    name = "autocomplete_debug"
    has_json_alternate = False

    async def _suggested_tables(self, request):
        scanned = 0
        reached_scan_limit = False
        suggestions = []
        for database_name, db in self.ds.databases.items():
            if scanned >= 100 or len(suggestions) >= 5:
                break
            remaining = 100 - scanned
            results = await db.execute(
                "select name from sqlite_master where type = 'table' order by name limit ?",
                [remaining],
            )
            for row in results.rows:
                table_name = row["name"]
                scanned += 1
                if scanned >= 100:
                    reached_scan_limit = True
                visible, _ = await self.ds.check_visibility(
                    request.actor,
                    action="view-table",
                    resource=TableResource(database=database_name, table=table_name),
                )
                if not visible:
                    if scanned >= 100:
                        break
                    continue
                label_column = await db.label_column_for_table(table_name)
                if label_column:
                    suggestions.append(
                        {
                            "database": database_name,
                            "table": table_name,
                            "label_column": label_column,
                            "url": self.ds.urls.path(
                                "-/debug/autocomplete?"
                                + urllib.parse.urlencode(
                                    {
                                        "database": database_name,
                                        "table": table_name,
                                    }
                                )
                            ),
                        }
                    )
                    if len(suggestions) >= 5:
                        break
                if scanned >= 100:
                    break
        return suggestions, scanned, reached_scan_limit

    async def get(self, request):
        await self.ds.ensure_permission(action="view-instance", actor=request.actor)
        database_name = request.args.get("database")
        table_name = request.args.get("table")
        context = {
            "database_name": database_name,
            "table_name": table_name,
        }

        if database_name or table_name:
            if not database_name or not table_name:
                context["error"] = "Both database and table are required."
            elif database_name not in self.ds.databases:
                context["error"] = "Database not found."
            else:
                db = self.ds.databases[database_name]
                if not await db.table_exists(table_name):
                    context["error"] = "Table not found."
                else:
                    await self.ds.ensure_permission(
                        action="view-table",
                        resource=TableResource(
                            database=database_name,
                            table=table_name,
                        ),
                        actor=request.actor,
                    )
                    context.update(
                        {
                            "autocomplete_url": "{}/-/autocomplete".format(
                                self.ds.urls.table(database_name, table_name)
                            ),
                            "label_column": await db.label_column_for_table(table_name),
                        }
                    )
        else:
            suggestions, scanned, reached_scan_limit = await self._suggested_tables(
                request
            )
            context.update(
                {
                    "suggestions": suggestions,
                    "scanned": scanned,
                    "reached_scan_limit": reached_scan_limit,
                }
            )

        return await self.render(["debug_autocomplete.html"], request, context)


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
            ["debug_permissions_playground.html"],
            request,
            # list() avoids error if check is performed during template render:
            {
                "permission_checks": permission_checks,
                "filter": filter_,
                "has_debug_permission": True,
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
        form = await request.form()
        actor = json.loads(form["actor"])
        permission = form["permission"]
        parent = form.get("resource_1") or None
        child = form.get("resource_2") or None

        response, status = await _check_permission_for_actor(
            self.ds, permission, parent, child, actor
        )
        if response.get("ok"):
            response = {
                "ok": True,
                "unstable": UNSTABLE_API_MESSAGE,
                **response,
            }
        return Response.json(response, status=status)


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

        payload, status = await self._allowed_payload(request, has_debug_permission)
        headers = {}
        if self.ds.cors:
            add_cors_headers(headers)
        return Response.json(payload, status=status, headers=headers)

    async def _allowed_payload(self, request, has_debug_permission):
        action = request.args.get("action")
        if not action:
            return error_body("action parameter is required", 400), 400
        if action not in self.ds.actions:
            return error_body(f"Unknown action: {action}", 404), 404

        actor = request.actor if isinstance(request.actor, dict) else None
        actor_id = actor.get("id") if actor else None
        parent_filter = request.args.get("parent")
        child_filter = request.args.get("child")
        if child_filter and not parent_filter:
            return (
                error_body("parent must be provided when child is specified", 400),
                400,
            )

        try:
            page = int(request.args.get("page", "1"))
            page_size = int(request.args.get("page_size", "50"))
        except ValueError:
            return error_body("page and page_size must be integers", 400), 400
        if page < 1:
            return error_body("page must be >= 1", 400), 400
        if page_size < 1:
            return error_body("page_size must be >= 1", 400), 400
        max_page_size = 200
        if page_size > max_page_size:
            page_size = max_page_size
        offset = (page - 1) * page_size

        # Use the simplified allowed_resources method
        # Collect all resources with optional reasons for debugging
        try:
            allowed_rows = []
            result = await self.ds.allowed_resources(
                action=action,
                actor=actor,
                parent=parent_filter,
                include_reasons=has_debug_permission,
            )
            async for resource in result.all():
                parent_val = resource.parent
                child_val = resource.child

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

                # Add reason if we have it (from include_reasons=True)
                if has_debug_permission and hasattr(resource, "reasons"):
                    row["reason"] = resource.reasons

                allowed_rows.append(row)
        except Exception:
            # If catalog tables don't exist yet, return empty results
            return (
                {
                    "ok": True,
                    "action": action,
                    "actor_id": actor_id,
                    "page": page,
                    "page_size": page_size,
                    "total": 0,
                    "items": [],
                },
                200,
            )

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
            "ok": True,
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

        return response, 200


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
                    "has_debug_permission": True,
                },
            )

        # JSON API - action parameter is required
        action = request.args.get("action")
        if not action:
            return Response.json(
                error_body("action parameter is required", 400), status=400
            )
        if action not in self.ds.actions:
            return Response.json(
                error_body(f"Unknown action: {action}", 404), status=404
            )

        actor = request.actor if isinstance(request.actor, dict) else None

        try:
            page = int(request.args.get("page", "1"))
            page_size = int(request.args.get("page_size", "50"))
        except ValueError:
            return Response.json(
                error_body("page and page_size must be integers", 400), status=400
            )
        if page < 1:
            return Response.json(error_body("page must be >= 1", 400), status=400)
        if page_size < 1:
            return Response.json(error_body("page_size must be >= 1", 400), status=400)
        max_page_size = 200
        if page_size > max_page_size:
            page_size = max_page_size
        offset = (page - 1) * page_size

        from datasette.utils.actions_sql import build_permission_rules_sql

        union_sql, union_params, restriction_sqls = await build_permission_rules_sql(
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
            "ok": True,
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


async def _check_permission_for_actor(ds, action, parent, child, actor):
    """Shared logic for checking permissions. Returns a dict with check results."""
    if action not in ds.actions:
        return error_body(f"Unknown action: {action}", 404), 404

    if child and not parent:
        return error_body("parent is required when child is provided", 400), 400

    # Use the action's properties to create the appropriate resource object
    action_obj = ds.actions.get(action)
    if not action_obj:
        return error_body(f"Unknown action: {action}", 400), 400

    # Global actions (no resource_class) don't have a resource
    if action_obj.resource_class is None:
        resource_obj = None
    elif action_obj.takes_parent and action_obj.takes_child:
        # Child-level resource (e.g., TableResource, QueryResource). The child
        # argument is named differently per resource class (table, query, ...),
        # so pass positionally - https://github.com/simonw/datasette/issues/2756
        resource_obj = action_obj.resource_class(parent, child)
    elif action_obj.takes_parent:
        # Parent-level resource (e.g., DatabaseResource)
        resource_obj = action_obj.resource_class(parent)
    else:
        # This shouldn't happen given validation in Action.__post_init__
        return error_body(f"Invalid action configuration: {action}", 500), 500

    allowed = await ds.allowed(action=action, resource=resource_obj, actor=actor)

    response = {
        "ok": True,
        "action": action,
        "allowed": bool(allowed),
        "resource": {
            "parent": parent,
            "child": child,
            "path": _resource_path(parent, child),
        },
    }

    if actor and "id" in actor:
        response["actor_id"] = actor["id"]

    return response, 200


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
                    "has_debug_permission": True,
                },
            )

        # JSON API - action parameter is required
        action = request.args.get("action")
        if not action:
            return Response.json(
                error_body("action parameter is required", 400), status=400
            )

        parent = request.args.get("parent")
        child = request.args.get("child")

        response, status = await _check_permission_for_actor(
            self.ds, action, parent, child, request.actor
        )
        return Response.json(response, status=status)


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
                "has_debug_permission": await self.ds.allowed(
                    action="permissions-debug", actor=request.actor
                ),
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
        form = await request.form()
        message = form.get("message", "")
        message_type = form.get("message_type") or "INFO"
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
        db_page = await self.ds.allowed_resources("view-database", request.actor)
        allowed_databases = [r async for r in db_page.all()]

        table_page = await self.ds.allowed_resources("view-table", request.actor)
        allowed_tables = [r async for r in table_page.all()]

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
        form = await request.form()
        errors = []
        expires_after = None
        if form.get("expire_type"):
            duration_string = form.get("expire_duration")
            if (
                not duration_string
                or not duration_string.isdigit()
                or not int(duration_string) > 0
            ):
                errors.append("Invalid expire duration")
            else:
                unit = form["expire_type"]
                if unit == "minutes":
                    expires_after = int(duration_string) * 60
                elif unit == "hours":
                    expires_after = int(duration_string) * 60 * 60
                elif unit == "days":
                    expires_after = int(duration_string) * 60 * 60 * 24
                else:
                    errors.append("Invalid expire duration unit")

        # Are there any restrictions?
        from datasette.tokens import TokenRestrictions

        restrictions = TokenRestrictions()

        for key in form:
            if key.startswith("all:") and key.count(":") == 1:
                restrictions.allow_all(key.split(":")[1])
            elif key.startswith("database:") and key.count(":") == 2:
                bits = key.split(":")
                restrictions.allow_database(tilde_decode(bits[1]), bits[2])
            elif key.startswith("resource:") and key.count(":") == 3:
                bits = key.split(":")
                restrictions.allow_resource(
                    tilde_decode(bits[1]), tilde_decode(bits[2]), bits[3]
                )

        token = await self.ds.create_token(
            request.actor["id"],
            expires_after=expires_after,
            restrictions=restrictions,
            handler="signed",
        )
        token_bits = self.ds.unsign(token[len("dstok_") :], namespace="token")
        await self.ds.track_event(
            CreateTokenEvent(
                actor=request.actor,
                expires_after=expires_after,
                restrict_all=restrictions.all,
                restrict_database=restrictions.database,
                restrict_resource=restrictions.resource,
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
                                            column: "<{}{}>".format(
                                                column,
                                                (
                                                    " (primary key)"
                                                    if column in (pks or ["rowid"])
                                                    else ""
                                                ),
                                            )
                                            for column in (
                                                (["rowid"] if not pks else [])
                                                + await db.table_columns(table)
                                            )
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
            return "{}#{}".format(
                self.ds.urls.path("/-/api"),
                urllib.parse.urlencode(
                    {
                        key: json.dumps(value, indent=2) if key == "json" else value
                        for key, value in link.items()
                        if key in ("path", "method", "json")
                    }
                ),
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


class JumpView(BaseView):
    """
    Endpoint for the jump menu. Returns JSON navigation items the actor can use.
    """

    name = "jump"
    has_json_alternate = False

    async def _fragments(self, request):
        fragments = []
        for hook in pm.hook.jump_items_sql(
            datasette=self.ds,
            actor=request.actor,
            request=request,
        ):
            value = await await_me_maybe(hook)
            if value is None:
                continue
            if isinstance(value, JumpSQL):
                fragments.append(value)
            elif isinstance(value, (list, tuple)):
                for fragment in value:
                    if fragment is not None:
                        assert isinstance(
                            fragment, JumpSQL
                        ), "jump_items_sql must return JumpSQL instances"
                        fragments.append(fragment)
            else:
                raise TypeError("jump_items_sql must return JumpSQL instances")
        return fragments

    def _resolve_url(self, url):
        if not url or url.startswith("/"):
            return url

        descriptor = json.loads(url)
        if not isinstance(descriptor, dict):
            raise TypeError("jump item url JSON must be an object")
        method_name = descriptor.get("method")
        if not isinstance(method_name, str) or not method_name:
            raise TypeError("jump item url JSON must include a method")
        if method_name.startswith("_"):
            raise AttributeError(f"datasette.urls has no method named {method_name!r}")
        try:
            method = getattr(self.ds.urls, method_name)
        except AttributeError as ex:
            raise AttributeError(
                f"datasette.urls has no method named {method_name!r}"
            ) from ex
        if not callable(method):
            raise TypeError(f"datasette.urls.{method_name} is not callable")
        kwargs = {key: value for key, value in descriptor.items() if key != "method"}
        try:
            return method(**kwargs)
        except TypeError as ex:
            raise TypeError(
                f"Invalid arguments for datasette.urls.{method_name}(): {ex}"
            ) from ex

    def _sort_key(self, row, q):
        display_label = row["display_name"] or row["label"]
        display_label_lower = display_label.lower()
        q_lower = q.lower()
        if display_label_lower == q_lower:
            relevance = 0
        elif display_label_lower.startswith(q_lower):
            relevance = 1
        else:
            relevance = 2
        type_sort = {
            "database": 10,
            "table": 20,
            "view": 25,
            "query": 30,
        }.get(row["type"], 50)
        return (relevance, type_sort, len(display_label), row["label"])

    async def _rows_for_database(self, database_name, indexed_fragments, q, pattern):
        params = {"q": q, "pattern": pattern}
        union_parts = []
        for index, fragment in indexed_fragments:
            fragment_sql, fragment_params = namespace_sql_params(
                fragment.sql,
                fragment.params or {},
                f"jump_{index}",
            )
            union_parts.append(f"""
                SELECT
                    type,
                    label,
                    description,
                    url,
                    search_text,
                    display_name
                FROM (
                    {fragment_sql}
                )
            """)
            params.update(fragment_params)
        sql = f"""
        WITH jump_items AS (
            {" UNION ALL ".join(union_parts)}
        )
        SELECT
            type,
            label,
            description,
            url,
            search_text,
            display_name
        FROM jump_items
        WHERE :q = ''
           OR search_text LIKE :pattern COLLATE NOCASE
        ORDER BY
            CASE
                WHEN lower(COALESCE(display_name, label)) = lower(:q) THEN 0
                WHEN lower(COALESCE(display_name, label)) LIKE lower(:q || '%') THEN 1
                ELSE 2
            END,
            CASE type
                WHEN 'database' THEN 10
                WHEN 'table' THEN 20
                WHEN 'view' THEN 25
                WHEN 'query' THEN 30
                ELSE 50
            END,
            length(COALESCE(display_name, label)),
            label
        LIMIT 101
        """
        db = (
            self.ds.get_internal_database()
            if database_name is None
            else self.ds.get_database(database_name)
        )
        result = await db.execute(sql, params)
        return list(result.rows)

    async def get(self, request):
        q = request.args.get("q", "").strip()
        terms = q.split()
        pattern = "%" + "%".join(terms) + "%" if terms else "%"
        fragments = await self._fragments(request)

        fragments_by_database = {}
        for index, fragment in enumerate(fragments):
            fragments_by_database.setdefault(fragment.database, []).append(
                (index, fragment)
            )

        rows = []
        truncated = False
        for database_name, indexed_fragments in fragments_by_database.items():
            database_rows = await self._rows_for_database(
                database_name, indexed_fragments, q, pattern
            )
            if len(database_rows) > 100:
                truncated = True
                database_rows = database_rows[:100]
            rows.extend(database_rows)
        rows.sort(key=lambda row: self._sort_key(row, q))

        if len(rows) > 100:
            truncated = True
            rows = rows[:100]

        matches = []
        for row in rows:
            match = {
                "name": row["label"],
                "url": self._resolve_url(row["url"]),
                "type": row["type"],
                "description": row["description"],
            }
            if row["display_name"]:
                match["display_name"] = row["display_name"]
            matches.append(match)

        return Response.json({"ok": True, "matches": matches, "truncated": truncated})


class SchemaBaseView(BaseView):
    """Base class for schema views with common response formatting."""

    has_json_alternate = False

    async def get_database_schema(self, database_name):
        """Get schema SQL for a database."""
        db = self.ds.databases[database_name]
        result = await db.execute(
            "select group_concat(sql, ';' || CHAR(10)) as schema from sqlite_master where sql is not null"
        )
        row = result.first()
        return row["schema"] if row and row["schema"] else ""

    def format_json_response(self, data):
        """Format data as JSON response with CORS headers if needed."""
        headers = {}
        if self.ds.cors:
            add_cors_headers(headers)
        return Response.json({"ok": True, **data}, headers=headers)

    def format_error_response(self, error_message, format_, status=404):
        """Format error response based on requested format."""
        if format_ == "json":
            headers = {}
            if self.ds.cors:
                add_cors_headers(headers)
            return Response.json(
                error_body(error_message, status), status=status, headers=headers
            )
        else:
            return Response.text(error_message, status=status)

    def format_markdown_response(self, heading, schema):
        """Format schema as Markdown response."""
        md_output = f"# {heading}\n\n```sql\n{schema}\n```\n"
        return Response.text(
            md_output, headers={"content-type": "text/markdown; charset=utf-8"}
        )

    async def format_html_response(
        self, request, schemas, is_instance=False, table_name=None
    ):
        """Format schema as HTML response."""
        context = {
            "schemas": schemas,
            "is_instance": is_instance,
        }
        if table_name:
            context["table_name"] = table_name
        return await self.render(["schema.html"], request=request, context=context)


class InstanceSchemaView(SchemaBaseView):
    """
    Displays schema for all databases in the instance.
    Supports HTML, JSON, and Markdown formats.
    """

    name = "instance_schema"

    async def get(self, request):
        format_ = request.url_vars.get("format") or "html"

        # Get all databases the actor can view
        allowed_databases_page = await self.ds.allowed_resources(
            "view-database",
            request.actor,
        )
        allowed_databases = [r.parent async for r in allowed_databases_page.all()]

        # Get schema for each database
        schemas = []
        for database_name in allowed_databases:
            schema = await self.get_database_schema(database_name)
            schemas.append({"database": database_name, "schema": schema})

        if format_ == "json":
            return self.format_json_response({"schemas": schemas})
        elif format_ == "md":
            md_parts = [
                f"# Schema for {item['database']}\n\n```sql\n{item['schema']}\n```"
                for item in schemas
            ]
            return Response.text(
                "\n\n".join(md_parts),
                headers={"content-type": "text/markdown; charset=utf-8"},
            )
        else:
            return await self.format_html_response(request, schemas, is_instance=True)


class DatabaseSchemaView(SchemaBaseView):
    """
    Displays schema for a specific database.
    Supports HTML, JSON, and Markdown formats.
    """

    name = "database_schema"

    async def get(self, request):
        database_name = request.url_vars["database"]
        format_ = request.url_vars.get("format") or "html"

        # Permission check comes first, so actors without view-database
        # cannot distinguish existing databases from missing ones
        await self.ds.ensure_permission(
            action="view-database",
            resource=DatabaseResource(database=database_name),
            actor=request.actor,
        )

        if database_name not in self.ds.databases:
            return self.format_error_response("Database not found", format_)

        schema = await self.get_database_schema(database_name)

        if format_ == "json":
            return self.format_json_response(
                {"database": database_name, "schema": schema}
            )
        elif format_ == "md":
            return self.format_markdown_response(f"Schema for {database_name}", schema)
        else:
            schemas = [{"database": database_name, "schema": schema}]
            return await self.format_html_response(request, schemas)


class TableSchemaView(SchemaBaseView):
    """
    Displays schema for a specific table.
    Supports HTML, JSON, and Markdown formats.
    """

    name = "table_schema"

    async def get(self, request):
        database_name = request.url_vars["database"]
        table_name = request.url_vars["table"]
        format_ = request.url_vars.get("format") or "html"

        # Check view-table permission
        await self.ds.ensure_permission(
            action="view-table",
            resource=TableResource(database=database_name, table=table_name),
            actor=request.actor,
        )

        if database_name not in self.ds.databases:
            return self.format_error_response("Database not found", format_)

        # Get schema for the table
        db = self.ds.databases[database_name]
        result = await db.execute(
            "select sql from sqlite_master where name = ? and sql is not null",
            [table_name],
        )
        row = result.first()

        # Return 404 if table doesn't exist
        if not row or not row["sql"]:
            return self.format_error_response("Table not found", format_)

        schema = row["sql"]

        if format_ == "json":
            return self.format_json_response(
                {"database": database_name, "table": table_name, "schema": schema}
            )
        elif format_ == "md":
            return self.format_markdown_response(
                f"Schema for {database_name}.{table_name}", schema
            )
        else:
            schemas = [{"database": database_name, "schema": schema}]
            return await self.format_html_response(
                request, schemas, table_name=table_name
            )
