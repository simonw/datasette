from datasette import hookimpl


@hookimpl
def menu_links(datasette, actor):
    async def inner():
        if not await datasette.permission_allowed(actor, "debug-menu"):
            return []

        return [
            {"href": datasette.urls.path("/-/databases"), "label": "Databases"},
            {
                "href": datasette.urls.path("/-/plugins"),
                "label": "Installed plugins",
            },
            {
                "href": datasette.urls.path("/-/versions"),
                "label": "Version info",
            },
            {
                "href": datasette.urls.path("/-/metadata"),
                "label": "Metadata",
            },
            {
                "href": datasette.urls.path("/-/settings"),
                "label": "Settings",
            },
            {
                "href": datasette.urls.path("/-/permissions"),
                "label": "Debug permissions",
            },
            {
                "href": datasette.urls.path("/-/messages"),
                "label": "Debug messages",
            },
            {
                "href": datasette.urls.path("/-/allow-debug"),
                "label": "Debug allow rules",
            },
            {"href": datasette.urls.path("/-/threads"), "label": "Debug threads"},
            {"href": datasette.urls.path("/-/actor"), "label": "Debug actor"},
            {"href": datasette.urls.path("/-/patterns"), "label": "Pattern portfolio"},
        ]

    return inner
