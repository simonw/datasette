"""
A build backend that packages a configuration directory as a wheel.

Add this to a ``pyproject.toml`` in the configuration directory::

    [project]
    name = "my-datasette-app"
    version = "0.1"
    dependencies = ["datasette", "datasette-cluster-map"]

    [build-system]
    requires = ["datasette"]
    build-backend = "datasette.build"

``uv build`` or ``python -m build`` then produce a wheel containing the
databases, configuration, metadata, templates, plugins and static files.
Installing that wheel and running ``datasette`` serves them.
"""

import base64
import hashlib
import io
import json
import re
import tarfile
import tomllib
import zipfile
from pathlib import Path

from .config_dir import (
    ENTRY_POINT_GROUP,
    INSPECT_FILENAME,
    REDIRECT_FILENAME,
    bundle_files,
    database_files,
)
from .version import __version__

WHEEL_TAG = "py3-none-any"


class BuildError(Exception):
    pass


def _load_project(source_dir):
    pyproject = Path(source_dir) / "pyproject.toml"
    project = tomllib.loads(pyproject.read_text("utf-8")).get("project") or {}
    for key in ("name", "version"):
        if not isinstance(project.get(key), str):
            raise BuildError(f"pyproject.toml needs a [project] {key}")
    if project.get("dynamic"):
        raise BuildError("datasette.build does not support [project] dynamic")
    return project


def _normalize(name):
    # https://packaging.python.org/en/latest/specifications/binary-distribution-format/#escaping-and-unicode
    return re.sub(r"[-_.]+", "_", name).lower()


def _metadata(project):
    lines = [
        "Metadata-Version: 2.1",
        f"Name: {project['name']}",
        f"Version: {project['version']}",
    ]
    if project.get("description"):
        lines.append(f"Summary: {project['description']}")
    if project.get("requires-python"):
        lines.append(f"Requires-Python: {project['requires-python']}")
    for dependency in project.get("dependencies") or []:
        lines.append(f"Requires-Dist: {dependency}")
    return "\n".join(lines) + "\n"


def _inspect_data(paths):
    "Inspect data for these database files, keyed for use from their directory"
    from .cli import inspect_, run_sync

    data = run_sync(lambda: inspect_([str(path) for path in paths], []))
    for info in data.values():
        info["file"] = Path(info["file"]).name
    return data


def _write_wheel(wheel_directory, project, files):
    """
    files is a list of (path within the configuration directory, bytes).
    Returns the filename of the wheel that was written.
    """
    name = _normalize(project["name"])
    dist_info = f"{name}-{project['version']}.dist-info"
    wheel_name = f"{name}-{project['version']}-{WHEEL_TAG}.whl"
    contents = [(f"{name}/{path}", data) for path, data in files]
    contents.extend(
        [
            (f"{dist_info}/METADATA", _metadata(project).encode("utf-8")),
            (
                f"{dist_info}/WHEEL",
                (
                    "Wheel-Version: 1.0\n"
                    f"Generator: datasette.build ({__version__})\n"
                    "Root-Is-Purelib: true\n"
                    f"Tag: {WHEEL_TAG}\n"
                ).encode(),
            ),
            (
                f"{dist_info}/entry_points.txt",
                f"[{ENTRY_POINT_GROUP}]\n{name} = {name}\n".encode(),
            ),
        ]
    )
    record = []
    with zipfile.ZipFile(
        Path(wheel_directory) / wheel_name, "w", zipfile.ZIP_DEFLATED
    ) as wheel:
        for arcname, data in contents:
            wheel.writestr(arcname, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest())
            record.append(
                f"{arcname},sha256={digest.rstrip(b'=').decode()},{len(data)}"
            )
        record.append(f"{dist_info}/RECORD,,")
        wheel.writestr(f"{dist_info}/RECORD", "\n".join(record) + "\n")
    return wheel_name


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    source_dir = Path.cwd()
    project = _load_project(source_dir)
    files = [
        (path.as_posix(), (source_dir / path).read_bytes())
        for path in bundle_files(source_dir)
    ]
    databases = database_files(source_dir)
    if databases:
        # Packaged databases are served immutable, with precomputed counts
        files.append(
            (
                INSPECT_FILENAME,
                json.dumps(_inspect_data(databases), indent=2).encode("utf-8"),
            )
        )
    return _write_wheel(wheel_directory, project, files)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    # Nothing is copied: the installed package points back at this directory,
    # so databases stay mutable and edits take effect on the next restart
    source_dir = Path.cwd()
    project = _load_project(source_dir)
    return _write_wheel(
        wheel_directory,
        project,
        [(REDIRECT_FILENAME, str(source_dir.resolve()).encode("utf-8"))],
    )


def build_sdist(sdist_directory, config_settings=None):
    source_dir = Path.cwd()
    project = _load_project(source_dir)
    base = f"{_normalize(project['name'])}-{project['version']}"
    sdist_name = f"{base}.tar.gz"
    paths = [Path("pyproject.toml")] + bundle_files(source_dir)
    with tarfile.open(Path(sdist_directory) / sdist_name, "w:gz") as sdist:
        for path in paths:
            sdist.add(source_dir / path, arcname=f"{base}/{path.as_posix()}")
        pkg_info = _metadata(project).encode("utf-8")
        info = tarfile.TarInfo(f"{base}/PKG-INFO")
        info.size = len(pkg_info)
        sdist.addfile(info, io.BytesIO(pkg_info))
    return sdist_name


def get_requires_for_build_wheel(config_settings=None):
    return []


def get_requires_for_build_editable(config_settings=None):
    return []


def get_requires_for_build_sdist(config_settings=None):
    return []
