from datasette import hookimpl
from datasette.jump import JumpSQL

DEBUG_MENU_ITEMS = (
    ("/-/databases", "Databases"),
    ("/-/plugins", "Installed plugins"),
    ("/-/versions", "Version info"),
    ("/-/settings", "Settings"),
    ("/-/permissions", "Debug permissions"),
    ("/-/messages", "Debug messages"),
    ("/-/allow-debug", "Debug allow rules"),
    ("/-/threads", "Debug threads"),
    ("/-/actor", "Debug actor"),
    ("/-/patterns", "Pattern portfolio"),
)


@hookimpl
def jump_items_sql(datasette, actor, request):
    async def inner():
        if not await datasette.allowed(action="debug-menu", actor=actor):
            return []

        return [
            JumpSQL.menu_item(
                label=label,
                url=datasette.urls.path(path),
                description="Debug menu",
                source="datasette.default_debug_menu",
                sort_key=70 + index,
                item_type="debug",
            )
            for index, (path, label) in enumerate(DEBUG_MENU_ITEMS)
        ]

    return inner
