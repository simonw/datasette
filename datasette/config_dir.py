"""
Discovery of the files that make up a configuration directory.

Shared by ``datasette serve``, which reads a configuration directory, and
``datasette.build``, which packages one as a wheel.
"""

import importlib.metadata
from pathlib import Path

DATABASE_EXTENSIONS = ("db", "sqlite", "sqlite3")
CONFIG_FILENAMES = ("datasette.json", "datasette.yaml", "datasette.yml")
METADATA_FILENAMES = ("metadata.json", "metadata.yaml", "metadata.yml")
INSPECT_FILENAME = "inspect-data.json"
SUBDIRECTORIES = ("templates", "plugins", "static")

# Entry point group used by installed configuration directories
ENTRY_POINT_GROUP = "datasette.config_dir"
# An editable install contains just this file, holding the path to the
# real configuration directory
REDIRECT_FILENAME = ".datasette-config-dir"


def database_files(config_dir):
    "Database files at the top level of the directory, sorted by filename"
    config_dir = Path(config_dir)
    paths = []
    for ext in DATABASE_EXTENSIONS:
        paths.extend(config_dir.glob(f"*.{ext}"))
    return sorted(paths, key=lambda path: path.name)


def bundle_files(config_dir):
    """
    Every file Datasette would read from this configuration directory, as
    sorted paths relative to it. ``inspect-data.json`` is not included.
    """
    config_dir = Path(config_dir)
    paths = list(database_files(config_dir))
    for filename in CONFIG_FILENAMES + METADATA_FILENAMES:
        if (config_dir / filename).is_file():
            paths.append(config_dir / filename)
    for subdirectory in SUBDIRECTORIES:
        if (config_dir / subdirectory).is_dir():
            paths.extend(
                path
                for path in (config_dir / subdirectory).rglob("*")
                if path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix != ".pyc"
            )
    return sorted(path.relative_to(config_dir) for path in paths)


def installed_config_dirs():
    """
    Configuration directories installed as Python packages built using the
    ``datasette.build`` backend. Returns a ``{name: pathlib.Path}`` dictionary.
    """
    found = {}
    for entry_point in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP):
        if entry_point.dist is None:
            continue
        path = Path(entry_point.dist.locate_file(entry_point.value))
        redirect = path / REDIRECT_FILENAME
        if redirect.is_file():
            path = Path(redirect.read_text("utf-8").strip())
        if path.is_dir():
            found[entry_point.name] = path
    return found
