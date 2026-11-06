"""
Does opening several connections at once to a fresh is_temp_disk Database
race on PRAGMA journal_mode=WAL? Database.connect() runs it on whichever
connection comes first while _wal_enabled is False, without a lock, so
two threads connecting together both run it and one can fail with
"database is locked". Concurrent imports into a new scratch database do
exactly that.

    uv run python prototypes/data_csv/temp_disk_wal_race.py
"""

import asyncio
import collections

from datasette.app import Datasette
from datasette.database import Database


async def once():
    ds = Datasette()
    db = ds.add_database(Database(ds, is_temp_disk=True), name="scratch")
    try:
        results = await asyncio.gather(
            *[db.execute("select 1") for _ in range(4)],
            db.execute_write("create table if not exists x (a)"),
            return_exceptions=True,
        )
        return [
            type(r).__name__ + ": " + str(r)
            for r in results
            if isinstance(r, BaseException)
        ]
    finally:
        db.close()


async def main():
    errors = collections.Counter()
    runs = 100
    for _ in range(runs):
        for error in await once():
            errors[error] += 1
    print(
        f"{runs} fresh databases, 4 reads + 1 write at once: {dict(errors) or 'no errors'}"
    )


asyncio.run(main())
