from datasette import hookimpl
from datasette.jump import JumpSQL

DEBUG_MENU_ITEMS = (
    (
        "/-/databases",
        "Databases",
        "List of databases known to this Datasette instance.",
    ),
    (
        "/-/plugins",
        "Installed plugins",
        "Review loaded plugins, their versions and their registered hooks.",
    ),
    (
        "/-/versions",
        "Version info",
        "Check the Python, SQLite and dependency versions used by this server.",
    ),
    (
        "/-/settings",
        "Settings",
        "Inspect the active Datasette settings and configuration values.",
    ),
    (
        "/-/permissions",
        "Debug permissions",
        "Test permission checks for actors, actions and resources.",
    ),
    (
        "/-/messages",
        "Debug messages",
        "Try out temporary flash messages shown to users.",
    ),
    (
        "/-/allow-debug",
        "Debug allow rules",
        "Explore how allow blocks match actors against permission rules.",
    ),
    (
        "/-/threads",
        "Debug threads",
        "Inspect worker threads and database tasks.",
    ),
    (
        "/-/actor",
        "Debug actor",
        "View the actor object for the current signed-in user.",
    ),
    (
        "/-/patterns",
        "Pattern portfolio",
        "Browse Datasette UI patterns.",
    ),
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
                description=description,
                search_text=f"debug {label} {description}",
                item_type="debug",
            )
            for path, label, description in DEBUG_MENU_ITEMS
        ]

    return inner
