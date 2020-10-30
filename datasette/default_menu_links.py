from datasette import hookimpl


@hookimpl
def menu_links(datasette, actor):
    if actor and actor.get("id") == "root":
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
                "href": datasette.urls.path("/-/config"),
                "label": "Config",
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
