from datasette import hookimpl
from datasette.utils import escape_fts
import datetime
import os
import time


def header(key, request):
    key = key.replace("_", "-").encode("utf-8")
    headers_dict = dict(request.scope["headers"])
    return headers_dict.get(key, b"").decode("utf-8")


def actor(key, request):
    if request.actor is None:
        raise KeyError
    return request.actor[key]


def cookie(key, request):
    return request.cookies[key]


def now(key, request):
    if key == "epoch":
        return int(time.time())
    elif key == "date_utc":
        return datetime.datetime.utcnow().date().isoformat()
    elif key == "datetime_utc":
        return datetime.datetime.utcnow().strftime(r"%Y-%m-%dT%H:%M:%S") + "Z"
    else:
        raise KeyError


def random(key, request):
    if key.startswith("chars_") and key.split("chars_")[-1].isdigit():
        num_chars = int(key.split("chars_")[-1])
        if num_chars % 2 == 1:
            urandom_len = (num_chars + 1) / 2
        else:
            urandom_len = num_chars / 2
        return os.urandom(int(urandom_len)).hex()[:num_chars]
    else:
        raise KeyError


@hookimpl
def register_magic_parameters():
    return [
        ("header", header),
        ("actor", actor),
        ("cookie", cookie),
        ("now", now),
        ("random", random),
    ]
