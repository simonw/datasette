"""Cog helper for documenting setting defaults from Datasette's registry."""


def setting_default(cog, name):
    from datasette.app import DEFAULT_SETTINGS

    default = DEFAULT_SETTINGS[name]
    if isinstance(default, bool):
        default = "on" if default else "off"
    cog.out(f"\nDefault: ``{default}``\n\n")
