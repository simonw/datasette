from datasette import hookimpl
from itsdangerous import BadSignature
import baseconv
import time


@hookimpl
def actor_from_request(datasette, request):
    if "ds_actor" not in request.cookies:
        return None
    try:
        decoded = datasette.unsign(request.cookies["ds_actor"], "actor")
        # If it has "e" and "a" keys process the "e" expiry
        if not isinstance(decoded, dict) or "a" not in decoded:
            return None
        expires_at = decoded.get("e")
        if expires_at:
            timestamp = int(baseconv.base62.decode(expires_at))
            if time.time() > timestamp:
                return None
        return decoded["a"]
    except BadSignature:
        return None
