import json
from datasette.utils.asgi import Response
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

    def __init__(self, datasette):
        self.ds = datasette

    async def get(self, request):
        await self.check_permission(request, "view-instance")
        return await self.render(["patterns.html"], request=request)


class AuthTokenView(BaseView):
    name = "auth_token"

    def __init__(self, datasette):
        self.ds = datasette

    async def get(self, request):
        token = request.args.get("token") or ""
        if not self.ds._root_token:
            return Response("Root token has already been used", status=403)
        if secrets.compare_digest(token, self.ds._root_token):
            self.ds._root_token = None
            response = Response.redirect("/")
            response.set_cookie(
                "ds_actor", self.ds.sign({"a": {"id": "root"}}, "actor")
            )
            return response
        else:
            return Response("Invalid token", status=403)


class PermissionsDebugView(BaseView):
    name = "permissions_debug"

    def __init__(self, datasette):
        self.ds = datasette

    async def get(self, request):
        await self.check_permission(request, "view-instance")
        if not await self.ds.permission_allowed(request.actor, "permissions-debug"):
            return Response("Permission denied", status=403)
        return await self.render(
            ["permissions_debug.html"],
            request,
            {"permission_checks": reversed(self.ds._permission_checks)},
        )


class MessagesDebugView(BaseView):
    name = "messages_debug"

    def __init__(self, datasette):
        self.ds = datasette

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
        return Response.redirect("/")
