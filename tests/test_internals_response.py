import json

import pytest

from datasette import hookimpl
from datasette.app import Datasette
from datasette.plugins import pm
from datasette.utils.asgi import Response


def test_response_html():
    response = Response.html("Hello from HTML")
    assert response.status == 200
    assert response.body == "Hello from HTML"
    assert response.content_type == "text/html; charset=utf-8"


def test_response_text():
    response = Response.text("Hello from text")
    assert response.status == 200
    assert response.body == "Hello from text"
    assert response.content_type == "text/plain; charset=utf-8"


def test_response_json():
    response = Response.json({"this_is": "json"})
    assert response.status == 200
    assert response.body == '{"this_is": "json"}'
    assert response.content_type == "application/json; charset=utf-8"


def test_response_redirect():
    response = Response.redirect("/foo")
    assert response.status == 302
    assert response.headers["Location"] == "/foo"


async def test_response_set_cookie():
    events = []

    async def send(event):
        events.append(event)

    response = Response.redirect("/foo")
    response.set_cookie("foo", "bar", max_age=10, httponly=True)
    await response.asgi_send(send)

    assert events == [
        {
            "type": "http.response.start",
            "status": 302,
            "headers": [
                [b"Location", b"/foo"],
                [b"content-type", b"text/plain"],
                [b"set-cookie", b"foo=bar; HttpOnly; Max-Age=10; Path=/; SameSite=lax"],
            ],
        },
        {"type": "http.response.body", "body": b""},
    ]


def test_response_error_single_message():
    response = Response.error("Method not allowed", 405)
    assert response.status == 405
    assert response.content_type == "application/json; charset=utf-8"
    assert json.loads(response.body) == {
        "ok": False,
        "error": "Method not allowed",
        "errors": ["Method not allowed"],
        "status": 405,
    }


def test_response_error_message_list_and_default_status():
    response = Response.error(["First problem", "Second problem"])
    assert response.status == 400
    assert json.loads(response.body) == {
        "ok": False,
        "error": "First problem; Second problem",
        "errors": ["First problem", "Second problem"],
        "status": 400,
    }


async def _collect(response):
    events = []

    async def send(event):
        events.append(event)

    await response.asgi_send(send)
    return events


async def test_response_stream():
    async def body():
        yield "one\n"
        yield b"two\n"
        # Empty chunks are skipped - an empty body would end the response
        yield ""
        yield "café\n"

    response = Response.stream(body(), headers={"x-demo": "1"})
    assert response.status == 200
    assert response.content_type == "text/plain; charset=utf-8"
    response.set_cookie("foo", "bar")
    assert await _collect(response) == [
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                [b"x-demo", b"1"],
                [b"content-type", b"text/plain; charset=utf-8"],
                [b"set-cookie", b"foo=bar; Path=/; SameSite=lax"],
            ],
        },
        {"type": "http.response.body", "body": b"one\n", "more_body": True},
        {"type": "http.response.body", "body": b"two\n", "more_body": True},
        {
            "type": "http.response.body",
            "body": "café\n".encode(),
            "more_body": True,
        },
        {"type": "http.response.body", "body": b""},
    ]


async def test_response_stream_status_and_content_type():
    async def body():
        yield "{}"

    response = Response.stream(body(), status=201, content_type="application/x-ndjson")
    events = await _collect(response)
    assert events[0]["status"] == 201
    assert [b"content-type", b"application/x-ndjson"] in events[0]["headers"]


async def test_response_stream_any_async_iterator():
    class Countdown:
        def __init__(self):
            self.n = 3

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.n:
                raise StopAsyncIteration
            self.n -= 1
            return str(self.n)

    events = await _collect(Response.stream(Countdown()))
    assert [event["body"] for event in events[1:]] == [b"2", b"1", b"0", b""]


@pytest.mark.parametrize("body", ("a string", b"bytes", ["a", "list"], None))
def test_response_stream_requires_async_iterator(body):
    with pytest.raises(TypeError, match="async iterator"):
        Response.stream(body)


async def test_response_stream_error_ends_without_final_chunk():
    cleaned_up = []

    async def body():
        try:
            yield "one"
            raise ValueError("went wrong")
        finally:
            cleaned_up.append(True)

    events = []

    async def send(event):
        events.append(event)

    with pytest.raises(ValueError, match="went wrong"):
        await Response.stream(body()).asgi_send(send)
    # No terminating chunk, so the client can tell the response is incomplete
    assert events[-1] == {
        "type": "http.response.body",
        "body": b"one",
        "more_body": True,
    }
    assert cleaned_up == [True]


async def test_response_stream_closes_generator_on_disconnect():
    cleaned_up = []

    async def body():
        try:
            yield "one"
            yield "two"
            yield "three"
        finally:
            cleaned_up.append(True)

    sent = []

    async def send(event):
        if event.get("body") == b"two":
            raise OSError("client disconnected")
        sent.append(event)

    with pytest.raises(OSError):
        await Response.stream(body()).asgi_send(send)
    assert cleaned_up == [True]
    assert [event.get("body") for event in sent[1:]] == [b"one"]


class _StreamRoutesPlugin:
    __name__ = "StreamRoutesPlugin"

    @hookimpl
    def register_routes(self):
        async def stream(request):
            async def body():
                for i in range(int(request.args.get("n", 3))):
                    yield f"line {i}\n"

            return Response.stream(body(), headers={"x-streamed": "yes"})

        async def broken(request):
            async def body():
                yield "partial"
                raise ValueError("stream failed")

            return Response.stream(body())

        async def broken_before_first_chunk(request):
            raise ValueError("failed before the response started")

        return [
            (r"^/-/stream-demo$", stream),
            (r"^/-/stream-broken$", broken),
            (r"^/-/stream-broken-early$", broken_before_first_chunk),
        ]


@pytest.fixture
def stream_routes():
    pm.register(_StreamRoutesPlugin(), name="stream_routes")
    try:
        yield
    finally:
        pm.unregister(name="stream_routes")


async def test_response_stream_from_view(stream_routes):
    ds = Datasette(memory=True)
    try:
        response = await ds.client.get("/-/stream-demo?n=4")
        assert response.status_code == 200
        assert response.headers["x-streamed"] == "yes"
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
        assert response.text == "line 0\nline 1\nline 2\nline 3\n"
        # A stream that yields nothing is an empty 200 response
        response = await ds.client.get("/-/stream-demo?n=0")
        assert response.status_code == 200
        assert response.text == ""
    finally:
        ds.close()


async def test_response_stream_error_in_view_is_not_an_error_page(stream_routes):
    ds = Datasette(memory=True)
    try:
        # Failing part way through cannot become a 500 page, because the
        # 200 status has already been sent - the exception reaches the server
        with pytest.raises(ValueError, match="stream failed"):
            await ds.client.get("/-/stream-broken")
        # An error before the response starts is still a regular 500
        response = await ds.client.get("/-/stream-broken-early")
        assert response.status_code == 500
    finally:
        ds.close()


def test_response_keyword_only_arguments():
    # status can be positional on the helpers, but not on the constructor
    assert Response.json({"ok": False}, 400).status == 400
    assert Response.redirect("/", 301).status == 301
    with pytest.raises(TypeError):
        Response("body", 400)
    with pytest.raises(TypeError):
        Response.json({}, 200, {"x-header": "1"})
    with pytest.raises(TypeError):
        Response.text("body").set_cookie("key", "value", 10)
