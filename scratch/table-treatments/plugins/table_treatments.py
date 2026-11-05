"""Loads the table treatment switcher and every treatment stylesheet on table pages.

Static files are mounted with --static tt:scratch/table-treatments/static.
Each URL carries the file's mtime so edits show up on a plain reload.
"""

import pathlib

from datasette import hookimpl

STATIC = pathlib.Path(__file__).resolve().parent.parent / "static"


def _versioned(datasette, relative):
    path = STATIC / relative
    version = path.stat().st_mtime_ns if path.exists() else 0
    return datasette.urls.path(f"/tt/{relative}?v={version}")


@hookimpl
def extra_css_urls(view_name, datasette):
    if view_name != "table":
        return []
    files = ["switcher.css"] + [
        f"treatments/{path.name}"
        for path in sorted((STATIC / "treatments").glob("*.css"))
    ]
    return [_versioned(datasette, relative) for relative in files]


@hookimpl
def extra_js_urls(view_name, datasette):
    if view_name != "table":
        return []
    files = ["switcher.js"] + [
        f"treatments/{path.name}"
        for path in sorted((STATIC / "treatments").glob("*.js"))
    ]
    return [_versioned(datasette, relative) for relative in files]
