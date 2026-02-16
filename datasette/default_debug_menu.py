from datasette import hookimpl
from datasette.permissions import DebugItem


@hookimpl
def debug_menu(datasette, actor):
    async def inner():
        items = []

        # Items visible to anyone
        items.append(
            DebugItem(
                title="Actor",
                description="Current authenticated actor",
                path="/-/actor",
            )
        )

        # Items requiring view-instance
        if await datasette.allowed(action="view-instance", actor=actor):
            items.extend(
                [
                    DebugItem(
                        title="Databases",
                        description="Connected databases",
                        path="/-/databases",
                    ),
                    DebugItem(
                        title="Installed plugins",
                        description="Plugins currently installed",
                        path="/-/plugins",
                    ),
                    DebugItem(
                        title="Version info",
                        description="Python, Datasette and SQLite versions",
                        path="/-/versions",
                    ),
                    DebugItem(
                        title="Settings",
                        description="Datasette configuration settings",
                        path="/-/settings",
                    ),
                    DebugItem(
                        title="Config",
                        description="Full configuration output",
                        path="/-/config",
                    ),
                    DebugItem(
                        title="Threads",
                        description="Active threads",
                        path="/-/threads",
                    ),
                    DebugItem(
                        title="Messages",
                        description="Debug the flash messaging system",
                        path="/-/messages",
                    ),
                    DebugItem(
                        title="Pattern portfolio",
                        description="Showcase of UI patterns and components",
                        path="/-/patterns",
                    ),
                ]
            )

        # Items requiring permissions-debug
        if await datasette.allowed(action="permissions-debug", actor=actor):
            items.extend(
                [
                    DebugItem(
                        title="Permissions",
                        description="Debug and test permission checks",
                        path="/-/permissions",
                    ),
                    DebugItem(
                        title="Allow rules",
                        description="Debug actor_matches_allow logic",
                        path="/-/allow-debug",
                    ),
                    DebugItem(
                        title="Actions",
                        description="Available permission actions",
                        path="/-/actions",
                    ),
                    DebugItem(
                        title="Permission rules",
                        description="Permission rules from all sources",
                        path="/-/rules",
                    ),
                ]
            )

        return items

    return inner
