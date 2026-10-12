"""
A fast, fixed-seed subset of the connection torture harness
(tests/connection_torture.py). Each scenario runs in its own process, so a
segfault or a hang fails one test instead of killing the test run.

Several event loops (threads) drive one Datasette with extreme settings
(smallest pool, 1ms idle timeout, 10ms schema polling) through reads,
writes of every kind, DDL, isolated functions, analyze_sql, cancellation,
add/remove_database, external schema changes, files
replaced and deleted, Database.close() and Datasette.close() mid-flight,
and non-threaded mode. See the harness docstring for what is checked.
"""

import json
import os
import signal
import subprocess
import sys

import pytest

HARNESS = os.path.join(os.path.dirname(__file__), "connection_torture.py")


@pytest.mark.parametrize(
    "scenario,seed",
    [
        ("mixed", 101),
        ("lifecycle", 102),
        ("close", 103),
        ("cancel", 104),
        ("pool", 105),
        ("nothreads", 106),
        ("writes", 107),
    ],
)
def test_connection_torture(scenario, seed):
    try:
        proc = subprocess.run(
            [
                sys.executable,
                HARNESS,
                scenario,
                "--seed",
                str(seed),
                "--duration",
                "1.5",
            ],
            capture_output=True,
            text=True,
            timeout=150,
            check=False,
        )
    except subprocess.TimeoutExpired as e:  # pragma: no cover
        pytest.fail(f"{scenario} hung:\n{(e.stderr or '')[-4000:]}")
    if proc.returncode < 0:  # pragma: no cover
        pytest.fail(
            f"{scenario} crashed with {signal.Signals(-proc.returncode).name}:\n"
            f"{proc.stderr[-4000:]}"
        )
    lines = proc.stdout.strip().splitlines()
    if not lines:
        pytest.fail(
            f"{scenario} exited {proc.returncode} without a summary:\n"
            f"{proc.stderr[-4000:]}"
        )
    summary = json.loads(lines[-1])
    problems = "\n".join(
        f"[{p['kind']}] {p['message'][:600]}" for p in summary["problems"]
    )
    assert proc.returncode == 0, f"{scenario} seed {seed}:\n{problems}"
    assert summary["ok"]
    # It did something (the slow-query scenarios do a few dozen operations
    # in 1.5s on a busy machine, the others hundreds)
    assert sum(summary["counts"].values()) > 10
