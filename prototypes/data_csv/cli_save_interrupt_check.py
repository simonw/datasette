"""
Interrupt ``datasette FILE --save EXISTING.db`` part way through loading and
check what is left in EXISTING.db: the same tables and rows as before,
``integrity_check`` ok, and the free pages the dropped partial table left
behind (which the command reports on stderr).

    uv run python prototypes/data_csv/cli_save_interrupt_check.py FILE WORKDIR [--after 6] [--signal INT]
"""

import argparse
import os
import signal
import sqlite3
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("work")
    parser.add_argument("--after", type=float, default=6.0)
    parser.add_argument("--signal", default="INT")
    parser.add_argument("--port", type=int, default=8799)
    options = parser.parse_args()
    os.makedirs(options.work, exist_ok=True)
    save = os.path.join(options.work, "existing.db")
    for suffix in ("", "-journal", "-wal", "-shm"):
        if os.path.exists(save + suffix):
            os.unlink(save + suffix)
    conn = sqlite3.connect(save)
    conn.execute("create table keep (id integer, v text)")
    conn.executemany("insert into keep values (?, ?)", [(i, "x") for i in range(10)])
    conn.commit()
    conn.close()
    before = os.path.getsize(save)
    env = dict(os.environ, DATASETTE_SECRET="not_a_secret")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "datasette",
            options.file,
            "--save",
            save,
            "-p",
            str(options.port),
        ],
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        env=env,
        preexec_fn=lambda: signal.signal(
            signal.SIGINT, signal.SIG_DFL
        ),  # noqa: PLW1509
    )
    time.sleep(options.after)
    sent = time.monotonic()
    process.send_signal(getattr(signal, "SIG" + options.signal))
    _, err = process.communicate(timeout=120)
    print(f"exit code {process.returncode} after {time.monotonic() - sent:.2f}s")
    for line in err.decode().splitlines()[-4:]:
        print("   ", line)
    conn = sqlite3.connect(save)
    print("tables:", [r[0] for r in conn.execute("select name from sqlite_master")])
    print("keep rows:", conn.execute("select count(*) from keep").fetchone()[0])
    print("integrity_check:", conn.execute("pragma integrity_check").fetchone()[0])
    print(
        f"file {before:,} bytes before, {os.path.getsize(save):,} after;",
        "freelist_count",
        conn.execute("pragma freelist_count").fetchone()[0],
    )
    conn.close()


if __name__ == "__main__":
    main()
