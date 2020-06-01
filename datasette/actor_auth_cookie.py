from datasette import hookimpl
from itsdangerous import BadSignature
from http.cookies import SimpleCookie


@hookimpl
def actor_from_request(datasette, request):
    cookies = SimpleCookie()
    cookies.load(
        dict(request.scope.get("headers") or []).get(b"cookie", b"").decode("utf-8")
    )
    if "ds_actor" not in cookies:
        return None
    ds_actor = cookies["ds_actor"].value
    try:
        return datasette.unsign(ds_actor, "actor")
    except BadSignature:
        return None
