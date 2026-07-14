from datasette.utils.asgi import PayloadTooLarge, Request
import json
import pytest


def _post_scope(headers=None):
    return {
        "http_version": "1.1",
        "method": "POST",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "scheme": "http",
        "type": "http",
        "headers": headers or [[b"content-type", b"application/json"]],
    }


def _receive_chunks(chunks):
    messages = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": i < len(chunks) - 1,
        }
        for i, chunk in enumerate(chunks)
    ]
    messages.reverse()

    async def receive():
        return messages.pop()

    return receive


def _form_request(body: bytes) -> Request:
    return Request(
        _post_scope(headers=[[b"content-type", b"application/x-www-form-urlencoded"]]),
        _receive_chunks([body]),
    )


@pytest.mark.asyncio
async def test_request_post_vars():
    request = _form_request(b"foo=bar&baz=1&empty=")
    post_vars = await request.post_vars()
    assert post_vars["foo"] == "bar"
    assert post_vars["baz"] == "1"
    assert post_vars["empty"] == ""
    assert post_vars.get("missing") is None
    assert set(post_vars.keys()) == {"foo", "baz", "empty"}
    assert dict(post_vars.items()) == {"foo": "bar", "baz": "1", "empty": ""}


@pytest.mark.asyncio
async def test_request_post_vars_multi():
    # post_vars() returns a MultiParams so multiple values for the same key are
    # preserved, matching the behaviour of request.args. See issue #2425.
    request = _form_request(b"multi=1&multi=2&single=3")
    post_vars = await request.post_vars()
    assert post_vars.get("multi") == "1"
    assert post_vars.get("single") == "3"
    assert post_vars["multi"] == "1"
    assert post_vars["single"] == "3"
    assert post_vars.getlist("multi") == ["1", "2"]
    assert post_vars.getlist("single") == ["3"]
    assert post_vars.getlist("missing") == []
    assert "multi" in post_vars
    assert "missing" not in post_vars
    assert list(post_vars.keys()) == ["multi", "single"]
    assert len(post_vars) == 2
    with pytest.raises(KeyError):
        post_vars["missing"]


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


@pytest.mark.asyncio
async def test_request_json():
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

    data = {"hello": "world", "items": [1, 2, 3]}

    async def receive():
        return {
            "type": "http.request",
            "body": json.dumps(data).encode("utf-8"),
            "more_body": False,
        }

    request = Request(scope, receive)
    assert data == await request.json()


@pytest.mark.asyncio
async def test_request_json_invalid():
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

    async def receive():
        return {
            "type": "http.request",
            "body": b"this is not JSON",
            "more_body": False,
        }

    request = Request(scope, receive)
    with pytest.raises(json.JSONDecodeError):
        await request.json()


@pytest.mark.asyncio
async def test_request_post_body_multiple_chunks():
    request = Request(_post_scope(), _receive_chunks([b"hello ", b"world"]))
    assert await request.post_body() == b"hello world"


@pytest.mark.asyncio
async def test_request_post_body_content_length_too_large():
    # Should reject based on content-length without reading the body
    async def receive():
        raise AssertionError("receive() should not be called")

    scope = _post_scope(
        headers=[
            [b"content-type", b"application/json"],
            [b"content-length", b"101"],
        ]
    )
    request = Request(scope, receive)
    with pytest.raises(PayloadTooLarge):
        await request.post_body(max_bytes=100)


@pytest.mark.asyncio
async def test_request_post_body_streaming_too_large():
    # No content-length header - limit enforced as chunks arrive
    chunks = [b"a" * 60, b"b" * 60, b"c" * 60]
    request = Request(_post_scope(), _receive_chunks(chunks))
    with pytest.raises(PayloadTooLarge):
        await request.post_body(max_bytes=100)


@pytest.mark.asyncio
async def test_request_post_body_limit_from_constructor():
    request = Request(
        _post_scope(), _receive_chunks([b"too much data"]), max_post_body_bytes=5
    )
    with pytest.raises(PayloadTooLarge):
        await request.post_body()


@pytest.mark.asyncio
async def test_request_post_body_limit_disabled():
    body = b"a" * (3 * 1024 * 1024)
    request = Request(_post_scope(), _receive_chunks([body]), max_post_body_bytes=0)
    assert await request.post_body() == body


@pytest.mark.asyncio
async def test_request_post_body_default_limit():
    # Bodies over 2MB are rejected by default
    request = Request(_post_scope(), _receive_chunks([b"a" * (2 * 1024 * 1024 + 1)]))
    with pytest.raises(PayloadTooLarge):
        await request.post_body()


@pytest.mark.asyncio
async def test_request_json_too_large():
    body = json.dumps({"rows": ["x" * 100]}).encode("utf-8")
    request = Request(_post_scope(), _receive_chunks([body]), max_post_body_bytes=50)
    with pytest.raises(PayloadTooLarge):
        await request.json()


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
