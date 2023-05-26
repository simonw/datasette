from datasette.views.base import View
from datasette import Request, Response
from datasette.app import Datasette
import json
import pytest


class GetView(View):
    async def get(self, request, datasette):
        return Response.json(
            {
                "absolute_url": datasette.absolute_url(request, "/"),
                "request_path": request.path,
            }
        )


class GetAndPostView(GetView):
    async def post(self, request, datasette):
        return Response.json(
            {
                "method": request.method,
                "absolute_url": datasette.absolute_url(request, "/"),
                "request_path": request.path,
            }
        )


@pytest.mark.asyncio
async def test_get_view():
    v = GetView()
    datasette = Datasette()
    response = await v(Request.fake("/foo"), datasette)
    assert json.loads(response.body) == {
        "absolute_url": "http://localhost/",
        "request_path": "/foo",
    }
    # Try a HEAD request
    head_response = await v(Request.fake("/foo", method="HEAD"), datasette)
    assert head_response.body == ""
    assert head_response.status == 200
    # And OPTIONS
    options_response = await v(Request.fake("/foo", method="OPTIONS"), datasette)
    assert options_response.body == "ok"
    assert options_response.status == 200
    assert options_response.headers["allow"] == "HEAD, GET"
    # And POST
    post_response = await v(Request.fake("/foo", method="POST"), datasette)
    assert post_response.body == "Method not allowed"
    assert post_response.status == 405
    # And POST with .json extension
    post_json_response = await v(Request.fake("/foo.json", method="POST"), datasette)
    assert json.loads(post_json_response.body) == {
        "ok": False,
        "error": "Method not allowed",
    }
    assert post_json_response.status == 405


@pytest.mark.asyncio
async def test_post_view():
    v = GetAndPostView()
    datasette = Datasette()
    response = await v(Request.fake("/foo"), datasette)
    assert json.loads(response.body) == {
        "absolute_url": "http://localhost/",
        "request_path": "/foo",
    }
    # Try a HEAD request
    head_response = await v(Request.fake("/foo", method="HEAD"), datasette)
    assert head_response.body == ""
    assert head_response.status == 200
    # And OPTIONS
    options_response = await v(Request.fake("/foo", method="OPTIONS"), datasette)
    assert options_response.body == "ok"
    assert options_response.status == 200
    assert options_response.headers["allow"] == "HEAD, GET, POST"
    # And POST
    post_response = await v(Request.fake("/foo", method="POST"), datasette)
    assert json.loads(post_response.body) == {
        "method": "POST",
        "absolute_url": "http://localhost/",
        "request_path": "/foo",
    }
