from datasette.utils import MultiParams
from asgiref.testing import ApplicationCommunicator
from asgiref.sync import async_to_sync
from urllib.parse import unquote, quote, urlencode
from http.cookies import SimpleCookie
import json


class TestResponse:
    def __init__(self, status, headers, body):
        self.status = status
        self.headers = headers
        self.body = body

    @property
    def cookies(self):
        cookie = SimpleCookie()
        for header in self.headers.getlist("set-cookie"):
            cookie.load(header)
        return {key: value.value for key, value in cookie.items()}

    @property
    def json(self):
        return json.loads(self.text)

    @property
    def text(self):
        return self.body.decode("utf8")


class TestClient:
    max_redirects = 5

    def __init__(self, asgi_app):
        self.asgi_app = asgi_app

    def actor_cookie(self, actor):
        return self.ds.sign({"a": actor}, "actor")

    @async_to_sync
    async def get(
        self, path, allow_redirects=True, redirect_count=0, method="GET", cookies=None
    ):
        return await self._request(
            path, allow_redirects, redirect_count, method, cookies
        )

    @async_to_sync
    async def post(
        self,
        path,
        post_data=None,
        allow_redirects=True,
        redirect_count=0,
        content_type="application/x-www-form-urlencoded",
        cookies=None,
        csrftoken_from=None,
    ):
        cookies = cookies or {}
        post_data = post_data or {}
        # Maybe fetch a csrftoken first
        if csrftoken_from is not None:
            if csrftoken_from is True:
                csrftoken_from = path
            token_response = await self._request(csrftoken_from, cookies=cookies)
            csrftoken = token_response.cookies["ds_csrftoken"]
            cookies["ds_csrftoken"] = csrftoken
            post_data["csrftoken"] = csrftoken
        return await self._request(
            path,
            allow_redirects,
            redirect_count,
            "POST",
            cookies,
            post_data,
            content_type,
        )

    async def _request(
        self,
        path,
        allow_redirects=True,
        redirect_count=0,
        method="GET",
        cookies=None,
        post_data=None,
        content_type=None,
    ):
        query_string = b""
        if "?" in path:
            path, _, query_string = path.partition("?")
            query_string = query_string.encode("utf8")
        if "%" in path:
            raw_path = path.encode("latin-1")
        else:
            raw_path = quote(path, safe="/:,").encode("latin-1")
        headers = [[b"host", b"localhost"]]
        if content_type:
            headers.append((b"content-type", content_type.encode("utf-8")))
        if cookies:
            sc = SimpleCookie()
            for key, value in cookies.items():
                sc[key] = value
            headers.append([b"cookie", sc.output(header="").encode("utf-8")])
        scope = {
            "type": "http",
            "http_version": "1.0",
            "method": method,
            "path": unquote(path),
            "raw_path": raw_path,
            "query_string": query_string,
            "headers": headers,
        }
        instance = ApplicationCommunicator(self.asgi_app, scope)

        if post_data:
            body = urlencode(post_data, doseq=True).encode("utf-8")
            await instance.send_input({"type": "http.request", "body": body})
        else:
            await instance.send_input({"type": "http.request"})

        # First message back should be response.start with headers and status
        messages = []
        start = await instance.receive_output(2)
        messages.append(start)
        assert start["type"] == "http.response.start"
        response_headers = MultiParams(
            [(k.decode("utf8"), v.decode("utf8")) for k, v in start["headers"]]
        )
        status = start["status"]
        # Now loop until we run out of response.body
        body = b""
        while True:
            message = await instance.receive_output(2)
            messages.append(message)
            assert message["type"] == "http.response.body"
            body += message["body"]
            if not message.get("more_body"):
                break
        response = TestResponse(status, response_headers, body)
        if allow_redirects and response.status in (301, 302):
            assert (
                redirect_count < self.max_redirects
            ), "Redirected {} times, max_redirects={}".format(
                redirect_count, self.max_redirects
            )
            location = response.headers["Location"]
            return await self._request(
                location, allow_redirects=True, redirect_count=redirect_count + 1
            )
        return response
