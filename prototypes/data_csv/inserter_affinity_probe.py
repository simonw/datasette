"""
Probe how SQLite type affinity treats CSV-style strings, compared with
sqlite_utils' TypeTracker, and check that changing a column's declared
type by editing sqlite_schema (PRAGMA writable_schema) behaves as the
inserter's in-place widening assumes.

    uv run python prototypes/data_csv/inserter_affinity_probe.py
"""

import decimal
import sqlite3
import uuid

from sqlite_utils.utils import ValueTracker

from datasette.imports.inserter import classify

PROBES = [
    "007", "00", "-01", "1.50", "1e3", "1E3", "1e+3", "3.0e+5", "1.0", "0.0",
    "-0.0", "1.", ".5", "0001.5", " 12", "12 ", "\t5", "5\n", "+5", "-0", "0",
    "-", "1e", "0x10", "1_000", "١٢", "nan", "inf", "Infinity",
    "1,000", "9223372036854775807", "9223372036854775808", "9223372036854775809",
    "-9223372036854775808", "-9223372036854775809", "", "1e400", "12abc",
    "1 2", "1.5e-3", "5005.5", "N/A-20000",
]  # fmt: skip


def type_tracker(value):
    tracker = ValueTracker()
    tracker.evaluate(value)
    return tracker.guessed_type


def value_changed(stored, text):
    "Did storing ``text`` as a number change its numeric value?"
    text = text.strip()
    try:
        exact = int(text)
    except ValueError:
        exact = None
    if exact is not None:
        return decimal.Decimal(stored) != exact
    if isinstance(stored, float):
        return stored != float(text)
    return decimal.Decimal(stored) != decimal.Decimal(text)


def affinity_table():
    conn = sqlite3.connect(":memory:")
    conn.execute("create table t (i INTEGER, r REAL, x TEXT)")
    conn.executemany("insert into t values (?, ?, ?)", [[p] * 3 for p in PROBES])
    rows = conn.execute("select i, typeof(i), r, typeof(r) from t").fetchall()
    print(
        "{:>24} | {:>32} | {:>32} | {:>11} | {}".format(
            "CSV string",
            "stored in INTEGER column",
            "stored in REAL column",
            "TypeTracker",
            "classify()",
        )
    )
    for probe, (i, ti, r, tr) in zip(PROBES, rows):
        notes = []
        for label, stored, kind in (("INTEGER", i, ti), ("REAL", r, tr)):
            if kind == "text":
                continue
            if value_changed(stored, probe):
                notes.append(f"{label}:value-changed")
            elif str(stored) != probe:
                notes.append(f"{label}:text-changed")
        print(
            "{:>24} | {:>22} {:>9} | {:>22} {:>9} | {:>11} | {:<8} {}".format(
                repr(probe),
                repr(i),
                ti,
                repr(r),
                tr,
                type_tracker(probe),
                str(classify(probe)),
                " ".join(notes),
            )
        )
    print()
    for value in [1.5, 2.5, True, [1], {"a": 1}, 0, 0.0, None]:
        print(
            f"Python value {repr(value)!r:>10}: TypeTracker -> {type_tracker(value):<8} classify() -> {classify(value)}"
        )


def writable_schema_checks():
    print("\nwritable_schema checks (shared-cache named memory DB, as Datasette)")
    name = uuid.uuid4().hex
    uri = f"file:{name}?mode=memory&cache=shared"
    writer = sqlite3.connect(uri, uri=True, isolation_level="IMMEDIATE")
    reader = sqlite3.connect(uri, uri=True)
    reader.execute("PRAGMA query_only=1")
    writer.execute('create table t ("a" INTEGER, "b" INTEGER)')
    writer.executemany("insert into t values (?, ?)", [(1, 1), (2, 2), ("5.5", "x")])
    writer.commit()
    print(
        "  before:",
        reader.execute("select a, typeof(a), b, typeof(b) from t").fetchall(),
    )
    writer.execute("BEGIN IMMEDIATE")
    version = writer.execute("PRAGMA schema_version").fetchone()[0]
    writer.execute("PRAGMA writable_schema=ON")
    writer.execute(
        "update sqlite_master set sql = ? where type = 'table' and name = 't'",
        ['CREATE TABLE t ("a" REAL, "b" TEXT)'],
    )
    writer.execute(f"PRAGMA schema_version={version + 1}")
    writer.execute("PRAGMA writable_schema=OFF")
    # Declared TEXT does not rewrite stored integers - fix those explicitly
    writer.execute("update t set b = cast(b as text) where typeof(b) != 'text'")
    writer.commit()
    print(
        "  after: ",
        reader.execute("select a, typeof(a), b, typeof(b) from t").fetchall(),
    )
    print(
        "  reader table_info:",
        [(r[1], r[2]) for r in reader.execute("PRAGMA table_info(t)")],
    )
    print("  integrity_check:", writer.execute("PRAGMA integrity_check").fetchone()[0])
    print(
        "  b = '1' finds row:",
        reader.execute("select count(*) from t where b = '1'").fetchone()[0],
    )


if __name__ == "__main__":
    affinity_table()
    writable_schema_checks()
