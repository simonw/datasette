import json
from datasette.utils.asgi import Response, Forbidden
from datasette.utils import actor_matches_allow
from .base import BaseView
import secrets


class JsonDataView(BaseView):
    name = "json_data"

    def __init__(self, datasette, filename, data_callback, needs_request=False):
        self.ds = datasette
        self.filename = filename
        self.data_callback = data_callback
        self.needs_request = needs_request

    async def get(self, request, as_format):
        await self.check_permission(request, "view-instance")
        if self.needs_request:
            data = self.data_callback(request)
        else:
            data = self.data_callback()
        if as_format:
            headers = {}
            if self.ds.cors:
                headers["Access-Control-Allow-Origin"] = "*"
            return Response(
                json.dumps(data),
                content_type="application/json; charset=utf-8",
                headers=headers,
            )

        else:
            return await self.render(
                ["show_json.html"],
                request=request,
                context={
                    "filename": self.filename,
                    "data_json": json.dumps(data, indent=4),
                },
            )


class PatternPortfolioView(BaseView):
    name = "patterns"

    async def get(self, request):
        await self.check_permission(request, "view-instance")
        return await self.render(["patterns.html"], request=request)


class AuthTokenView(BaseView):
    name = "auth_token"

    async def get(self, request):
        token = request.args.get("token") or ""
        if not self.ds._root_token:
            raise Forbidden("Root token has already been used")
        if secrets.compare_digest(token, self.ds._root_token):
            self.ds._root_token = None
            response = Response.redirect(self.ds.urls.instance())
            response.set_cookie(
                "ds_actor", self.ds.sign({"a": {"id": "root"}}, "actor")
            )
            return response
        else:
            raise Forbidden("Invalid token")


class LogoutView(BaseView):
    name = "logout"

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
        response.set_cookie("ds_actor", "", expires=0, max_age=0)
        self.ds.add_message(request, "You are now logged out", self.ds.WARNING)
        return response


class PermissionsDebugView(BaseView):
    name = "permissions_debug"

    async def get(self, request):
        await self.check_permission(request, "view-instance")
        if not await self.ds.permission_allowed(request.actor, "permissions-debug"):
            raise Forbidden("Permission denied")
        return await self.render(
            ["permissions_debug.html"],
            request,
            # list() avoids error if check is performed during template render:
            {"permission_checks": list(reversed(self.ds._permission_checks))},
        )


class AllowDebugView(BaseView):
    name = "allow_debug"

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

    async def get(self, request):
        await self.check_permission(request, "view-instance")
        return await self.render(["messages_debug.html"], request)

    async def post(self, request):
        await self.check_permission(request, "view-instance")
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
