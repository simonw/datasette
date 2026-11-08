"""Loading CSV, JSON and other files into Datasette databases."""

# The states of a background import (datasette.imports.background.TableImport),
# defined here so that datasette.imports.terminal, which that module imports,
# can use them too, as does the script that polls the progress notices
# (datasette.imports.default_hooks). An import's last event is named after its
# finished state
ACTIVE_STATES = ("queued", "downloading", "importing")
FINISHED_STATES = ("done", "failed", "cancelled")
