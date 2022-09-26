from datasette.utils.asgi import Request
import json
import pytest


@pytest.mark.asyncio
async def test_request_post_vars():
    scope = {
        "http_version": "1.1",
        "method": "POST",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "scheme": "http",
        "type": "http",
        "headers": [[b"content-type", b"application/x-www-form-urlencoded"]],
    }

    async def receive():
        return {
            "type": "http.request",
            "body": b"foo=bar&baz=1&empty=",
            "more_body": False,
        }

    request = Request(scope, receive)
    assert {"foo": "bar", "baz": "1", "empty": ""} == await request.post_vars()


@pytest.mark.asyncio
async def test_request_post_body():
    scope = {
        "http_version": "1.1",
        "method": "POST",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "scheme": "http",
        "type": "http",
        "headers": [[b"content-type", b"application/json"]],
    }

    data = {"hello": "world"}

    async def receive():
        return {
            "type": "http.request",
            "body": json.dumps(data, indent=4).encode("utf-8"),
            "more_body": False,
        }

    request = Request(scope, receive)
    body = await request.post_body()
    assert isinstance(body, bytes)
    assert data == json.loads(body)


def test_request_args():
    request = Request.fake("/foo?multi=1&multi=2&single=3")
    assert "1" == request.args.get("multi")
    assert "3" == request.args.get("single")
    assert "1" == request.args["multi"]
    assert "3" == request.args["single"]
    assert ["1", "2"] == request.args.getlist("multi")
    assert [] == request.args.getlist("missing")
    assert "multi" in request.args
    assert "single" in request.args
    assert "missing" not in request.args
    expected = ["multi", "single"]
    assert expected == list(request.args.keys())
    for i, key in enumerate(request.args):
        assert expected[i] == key
    assert 2 == len(request.args)
    with pytest.raises(KeyError):
        request.args["missing"]


def test_request_fake_url_vars():
    request = Request.fake("/")
    assert request.url_vars == {}
    request = Request.fake("/", url_vars={"database": "fixtures"})
    assert request.url_vars == {"database": "fixtures"}


def test_request_repr():
    request = Request.fake("/foo?multi=1&multi=2&single=3")
    assert (
        repr(request)
        == '<asgi.Request method="GET" url="http://localhost/foo?multi=1&multi=2&single=3">'
    )


def test_request_url_vars():
    scope = {
        "http_version": "1.1",
        "method": "POST",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "scheme": "http",
        "type": "http",
        "headers": [[b"content-type", b"application/x-www-form-urlencoded"]],
    }
    assert {} == Request(scope, None).url_vars
    assert {"name": "cleo"} == Request(
        dict(scope, url_route={"kwargs": {"name": "cleo"}}), None
    ).url_vars


@pytest.mark.parametrize(
    "path,query_string,expected_full_path",
    [("/", "", "/"), ("/", "foo=bar", "/?foo=bar"), ("/foo", "bar", "/foo?bar")],
)
def test_request_properties(path, query_string, expected_full_path):
    path_with_query_string = path
    if query_string:
        path_with_query_string += "?" + query_string
    scope = {
        "http_version": "1.1",
        "method": "POST",
        "path": path,
        "raw_path": path_with_query_string.encode("latin-1"),
        "query_string": query_string.encode("latin-1"),
        "scheme": "http",
        "type": "http",
    }
    request = Request(scope, None)
    assert request.path == path
    assert request.query_string == query_string
    assert request.full_path == expected_full_path


def test_request_blank_values():
    request = Request.fake("/?a=b&foo=bar&foo=bar2&baz=")
    assert request.args._data == {"a": ["b"], "foo": ["bar", "bar2"], "baz": [""]}


def test_json_in_query_string_name():
    query_string = (
        '?_through.["roadside_attraction_characteristics"%2C"characteristic_id"]=1'
    )
    request = Request.fake("/" + query_string)
    assert (
        request.args[
            '_through.["roadside_attraction_characteristics","characteristic_id"]'
        ]
        == "1"
    )
