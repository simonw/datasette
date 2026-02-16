from datasette import hookimpl


@hookimpl
def menu_links(datasette, actor):
    async def inner():
        if not await datasette.allowed(action="debug-menu", actor=actor):
            return []

        return [
            {"href": datasette.urls.path("/-/debug"), "label": "Debug"},
        ]

    return inner
