"""
How expensive is widening a column, depending on how it is done?

Part 1 - cost of one widening on a table that already holds N rows:

- sqlite-utils ``table.transform(types=...)`` (a full table rebuild)
- the inserter's own rebuild (CREATE new / INSERT SELECT / DROP / RENAME)
- the inserter's in-place schema edit (PRAGMA writable_schema): integer ->
  float is a metadata change only; integer -> text also runs an UPDATE that
  converts the stored numbers to text

for a named shared-cache in-memory database opened the way Datasette opens
``memory_name`` databases, and for an on-disk WAL database. Times include
the COMMIT.

Part 2 - end to end: the inserter run over N rows of big-1gb.csv in which a
column turns float, and another text, late (at 90% and 95% of the rows),
once per widening strategy.

    uv run python prototypes/data_csv/bench_inserter_widening.py \\
        --csv /path/to/big-1gb.csv --rows 100000 1000000 --work /tmp/work
"""

import argparse
import csv
import functools
import itertools
import os
import resource
import sqlite3
import sys
import time
import uuid

import sqlite_utils

from datasette.imports.inserter import TypedInserter, insert_rows

COLUMNS = (
    ("id", "INTEGER"),
    ("name", "TEXT"),
    ("city", "TEXT"),
    ("qty", "INTEGER"),
    ("price", "REAL"),
    ("created", "TEXT"),
    ("late_float", "INTEGER"),
    ("late_text", "INTEGER"),
    ("maybe_empty", "INTEGER"),
    ("notes", "TEXT"),
)


def open_db(storage, work):
    """Return (writer, keepalive, label) for the given storage."""
    if storage == "memory":
        # Exactly how datasette/database.py connects to memory_name databases
        uri = f"file:{uuid.uuid4().hex}?mode=memory&cache=shared"
        writer = sqlite3.connect(
            uri, uri=True, check_same_thread=False, isolation_level="IMMEDIATE"
        )
        reader = sqlite3.connect(uri, uri=True, check_same_thread=False)
        reader.execute("PRAGMA query_only=1")
        return writer, reader, None
    path = os.path.join(work, f"widen-{uuid.uuid4().hex[:8]}.db")
    writer = sqlite3.connect(path, isolation_level="IMMEDIATE")
    writer.execute("PRAGMA journal_mode=WAL")
    return writer, None, path


def build_table(conn, csv_path, rows):
    conn.execute(
        "CREATE TABLE t ({})".format(", ".join(f'"{n}" {t}' for n, t in COLUMNS))
    )
    numeric = [i for i, (_, t) in enumerate(COLUMNS) if t != "TEXT"]
    with open(csv_path, newline="") as fp:
        reader = csv.reader(fp)
        next(reader)

        def values():
            for row in itertools.islice(reader, rows):
                for i in numeric:
                    if row[i] == "":
                        row[i] = None
                yield row

        start = time.perf_counter()
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany(
            "INSERT INTO t VALUES ({})".format(", ".join("?" * len(COLUMNS))),
            values(),
        )
        conn.execute("COMMIT")
    return time.perf_counter() - start


def declared_sql(types):
    return "CREATE TABLE t ({})".format(
        ", ".join(f'"{n}" {types.get(n, t)}' for n, t in COLUMNS)
    )


def schema_edit(conn, types):
    version = conn.execute("PRAGMA schema_version").fetchone()[0]
    conn.execute("PRAGMA writable_schema=ON")
    conn.execute(
        "UPDATE sqlite_master SET sql = ? WHERE type='table' AND name='t'",
        [declared_sql(types)],
    )
    conn.execute(f"PRAGMA schema_version={version + 1}")
    conn.execute("PRAGMA writable_schema=OFF")


def timed(conn, fn):
    start = time.perf_counter()
    conn.execute("BEGIN IMMEDIATE")
    fn()
    conn.execute("COMMIT")
    return time.perf_counter() - start


def own_rebuild(conn, types, text_columns=()):
    current = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(t)")}
    current.update(types)
    conn.execute(
        "CREATE TABLE t_new ({})".format(
            ", ".join(f'"{n}" {current[n]}' for n, _ in COLUMNS)
        )
    )
    selects = ", ".join(
        (
            f"""CASE WHEN typeof("{n}") = 'real' AND "{n}" = CAST("{n}" AS INTEGER)
        THEN CAST(CAST("{n}" AS INTEGER) AS TEXT) ELSE CAST("{n}" AS TEXT) END"""
            if n in text_columns
            else f'"{n}"'
        )
        for n, _ in COLUMNS
    )
    names = ", ".join(f'"{n}"' for n, _ in COLUMNS)
    conn.execute(f"INSERT INTO t_new (rowid, {names}) SELECT rowid, {selects} FROM t")
    conn.execute("DROP TABLE t")
    conn.execute("ALTER TABLE t_new RENAME TO t")


def schema_text(conn, column):
    "In-place edit to TEXT plus an UPDATE converting the stored numbers."
    schema_edit(conn, {column: "TEXT"})
    conn.execute(
        f'UPDATE t SET "{column}" = CAST("{column}" AS TEXT) '
        f"""WHERE typeof("{column}") IN ('integer', 'real')"""
    )


METHODS = (
    ("schema edit int->float", lambda conn: schema_edit(conn, {"late_float": "REAL"})),
    ("schema edit + UPDATE int->text", lambda conn: schema_text(conn, "late_text")),
    (
        "own rebuild int->float",
        lambda conn: own_rebuild(conn, {"late_float": "REAL"}),
    ),
    (
        "own rebuild int->text",
        lambda conn: own_rebuild(conn, {"late_text": "TEXT"}, {"late_text"}),
    ),
    (
        "sqlite-utils transform int->float",
        lambda conn: sqlite_utils.Database(conn)
        .table("t")
        .transform(types={"late_float": float}),
    ),
    (
        "sqlite-utils transform int->text",
        lambda conn: sqlite_utils.Database(conn)
        .table("t")
        .transform(types={"late_text": str}),
    ),
)


def part1(csv_path, row_counts, storages, work):
    """
    Each method runs on its own fresh copy of the same N row table (copied
    with the SQLite backup API from a template), so earlier methods cannot
    leave a fragmented table behind for later ones.
    """
    print("## Part 1: cost of one widening on a table of N rows (incl. COMMIT)")
    print(
        "| rows | storage | load N rows | "
        + " | ".join(label for label, _ in METHODS)
        + " |"
    )
    print("|---|---|---|" + "---|" * len(METHODS))
    for rows in row_counts:
        template_path = os.path.join(work, f"template-{uuid.uuid4().hex[:8]}.db")
        template = sqlite3.connect(template_path, isolation_level="IMMEDIATE")
        template.execute("PRAGMA journal_mode=WAL")
        load = build_table(template, csv_path, rows)
        for storage in storages:
            results = []
            for label, method in METHODS:
                conn, keepalive, path = open_db(storage, work)
                template.backup(conn)
                if storage == "disk":
                    conn.execute("PRAGMA journal_mode=WAL")
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                results.append(timed(conn, functools.partial(method, conn)))
                check = conn.execute("PRAGMA quick_check").fetchone()[0]
                assert check == "ok", (label, check)
                conn.close()
                if keepalive is not None:
                    keepalive.close()
                if path:
                    remove_db(path)
            print(
                "| {:,} | {} | {:.2f}s | {} |".format(
                    rows,
                    storage,
                    load,
                    " | ".join(
                        f"{r * 1000:.2f}ms" if r < 0.1 else f"{r:.2f}s" for r in results
                    ),
                ),
                flush=True,
            )
        template.close()
        remove_db(template_path)


def remove_db(path):
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)


def late_rows(csv_path, rows):
    """rows of big-1gb.csv; qty turns float at 90%, id turns text at 95%."""
    float_at = int(rows * 0.9)
    text_at = int(rows * 0.95)
    with open(csv_path, newline="") as fp:
        for i, row in enumerate(itertools.islice(csv.DictReader(fp), rows)):
            if i == float_at:
                row["qty"] = row["qty"] + ".5"
            if i == text_at:
                row["id"] = "N/A"
            yield row


def part2(csv_path, row_counts, storages, work, batch_size):
    print()
    print(
        "## Part 2: whole import with late widenings "
        f"(qty -> float at 90%, id -> text at 95%; batch_size={batch_size})"
    )
    print("| rows | storage | strategy | total | time spent widening | widenings |")
    print("|---|---|---|---|---|---|")
    for rows in row_counts:
        for storage in storages:
            outputs = {}
            for widen in ("schema", "rebuild", "deferred"):
                conn, keepalive, path = open_db(storage, work)
                conn.isolation_level = None
                result = insert_rows(
                    conn,
                    "t",
                    late_rows(csv_path, rows),
                    widen=widen,
                    batch_size=batch_size,
                )
                if widen == "deferred":
                    # One rebuild in finish() covers every widening
                    widen_seconds = result.widenings[0].seconds
                else:
                    # Columns widened in the same batch share one timing
                    widen_seconds = sum(
                        {w.at_row: w.seconds for w in result.widenings}.values()
                    )
                print(
                    "| {:,} | {} | {} | {:.2f}s | {:.2f}s | {} |".format(
                        rows,
                        storage,
                        widen,
                        result.seconds,
                        widen_seconds,
                        ", ".join(
                            f"{w.column} {w.from_type}->{w.to_type} @{w.at_row:,}"
                            for w in result.widenings
                        ),
                    ),
                    flush=True,
                )
                outputs[widen] = conn.execute(
                    "select id, qty, typeof(id), typeof(qty) from t "
                    "where rowid in (1, ?, ?, ?) order by rowid",
                    [int(rows * 0.9) + 1, int(rows * 0.95) + 1, rows],
                ).fetchall()
                conn.close()
                if keepalive is not None:
                    keepalive.close()
                if path:
                    remove_db(path)
            same = len({repr(v) for v in outputs.values()}) == 1
            print(f"<!-- strategies produced identical sample rows: {same} -->")


def deferred_loss_demo():
    print()
    print("## Why not defer: values that only fit the wider type")
    for widen in ("schema", "rebuild", "deferred"):
        conn = sqlite3.connect(":memory:")
        inserter = TypedInserter("t", widen=widen)
        inserter.write(conn, [{"zip": "10001", "big": "1"}])
        inserter.write(conn, [{"zip": "02134", "big": "9223372036854775809"}])
        inserter.finish(conn)
        print(
            f"- {widen}: ",
            conn.execute("select zip, big from t where rowid = 2").fetchone(),
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", required=True)
    parser.add_argument("--rows", type=int, nargs="+", default=[100_000, 1_000_000])
    parser.add_argument("--part2-rows", type=int, nargs="+", default=None)
    parser.add_argument("--storage", nargs="+", default=["memory", "disk"])
    parser.add_argument("--work", required=True)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--skip-part1", action="store_true")
    parser.add_argument("--skip-part2", action="store_true")
    args = parser.parse_args()
    os.makedirs(args.work, exist_ok=True)
    print(f"SQLite {sqlite3.sqlite_version}, Python {sys.version.split()[0]}")
    if not args.skip_part1:
        part1(args.csv, args.rows, args.storage, args.work)
    if not args.skip_part2:
        part2(
            args.csv,
            args.part2_rows or args.rows,
            args.storage,
            args.work,
            args.batch_size,
        )
    deferred_loss_demo()
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"\nPeak RSS: {peak:,.0f} MB")


if __name__ == "__main__":
    main()
