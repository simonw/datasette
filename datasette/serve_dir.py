from datasette import hookimpl
from pathlib import Path
from .database import Database
from .utils import escape_sqlite
import click


@hookimpl
def extra_serve_options():
    return [
        click.option(
            "-d",
            "--dir",
            type=click.Path(exists=True, file_okay=False, dir_okay=True),
            help="Directories to scan for SQLite databases",
            multiple=True,
        ),
        click.option(
            "--scan",
            is_flag=True,
            help="Continually scan directories for new database files",
        ),
    ]


cached_results = None


@hookimpl
def available_databases(datasette):
    global cached_results
    if cached_results is not None:
        return cached_results
    i = 0
    counts = {name: 0 for name in datasette._databases}
    results = []
    for directory in datasette.extra_serve_options.get("dir") or []:
        for filepath in Path(directory).glob("**/*"):
            if is_sqlite(filepath):
                name = filepath.stem
                if name in counts:
                    new_name = "{}_{}".format(name, counts[name] + 1)
                    counts[name] += 1
                    name = new_name
                try:
                    database = Database(datasette, str(filepath), comment=str(filepath))
                    conn = database.connect()
                    result = conn.execute(
                        "select name from sqlite_master where type = 'table'"
                    )
                    table_names = [r[0] for r in result]
                    for table_name in table_names:
                        conn.execute(
                            "PRAGMA table_info({});".format(escape_sqlite(table_name))
                        )
                except Exception as e:
                    print("Could not open {}".format(filepath))
                    print("  " + str(e))
                else:
                    results.append((name, database))

    cached_results = results
    return results


magic = b"SQLite format 3\x00"


def is_sqlite(path):
    if not path.is_file():
        return False
    try:
        with open(path, "rb") as fp:
            return fp.read(len(magic)) == magic
    except PermissionError:
        return False
