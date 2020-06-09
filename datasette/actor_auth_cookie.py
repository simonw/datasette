from datasette import hookimpl
from itsdangerous import BadSignature


@hookimpl
def actor_from_request(datasette, request):
    if "ds_actor" not in request.cookies:
        return None
    try:
        return datasette.unsign(request.cookies["ds_actor"], "actor")
    except BadSignature:
        return None
