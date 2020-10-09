from asgiref.sync import async_to_sync
from urllib.parse import urlencode
import json

# These wrapper classes pre-date the introduction of
# datasette.client and httpx to Datasette. They could
# be removed if the Datasette tests are modified to
# call datasette.client directly.


class TestResponse:
    def __init__(self, httpx_response):
        self.httpx_response = httpx_response

    @property
    def status(self):
        return self.httpx_response.status_code

    @property
    def headers(self):
        return self.httpx_response.headers

    @property
    def body(self):
        return self.httpx_response.content

    @property
    def cookies(self):
        return dict(self.httpx_response.cookies)

    def cookie_was_deleted(self, cookie):
        return any(
            h
            for h in self.httpx_response.headers.get_list("set-cookie")
            if h.startswith('{}="";'.format(cookie))
        )

    @property
    def json(self):
        return json.loads(self.text)

    @property
    def text(self):
        return self.body.decode("utf8")


class TestClient:
    max_redirects = 5

    def __init__(self, ds):
        self.ds = ds

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
        body=None,
        allow_redirects=True,
        redirect_count=0,
        content_type="application/x-www-form-urlencoded",
        cookies=None,
        headers=None,
        csrftoken_from=None,
    ):
        cookies = cookies or {}
        post_data = post_data or {}
        assert not (post_data and body), "Provide one or other of body= or post_data="
        # Maybe fetch a csrftoken first
        if csrftoken_from is not None:
            assert body is None, "body= is not compatible with csrftoken_from="
            if csrftoken_from is True:
                csrftoken_from = path
            token_response = await self._request(csrftoken_from, cookies=cookies)
            csrftoken = token_response.cookies["ds_csrftoken"]
            cookies["ds_csrftoken"] = csrftoken
            post_data["csrftoken"] = csrftoken
        if post_data:
            body = urlencode(post_data, doseq=True)
        return await self._request(
            path=path,
            allow_redirects=allow_redirects,
            redirect_count=redirect_count,
            method="POST",
            cookies=cookies,
            headers=headers,
            post_body=body,
            content_type=content_type,
        )

    async def _request(
        self,
        path,
        allow_redirects=True,
        redirect_count=0,
        method="GET",
        cookies=None,
        headers=None,
        post_body=None,
        content_type=None,
    ):
        headers = headers or {}
        if content_type:
            headers["content-type"] = content_type
        httpx_response = await self.ds.client.request(
            method,
            path,
            allow_redirects=allow_redirects,
            cookies=cookies,
            headers=headers,
            content=post_body,
        )
        response = TestResponse(httpx_response)
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
