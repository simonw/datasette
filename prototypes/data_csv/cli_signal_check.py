"""
Send a signal to ``datasette`` while it downloads, loads or serves, then
report its exit code, the end of its stderr and any temporary files it
left behind.

    uv run python prototypes/data_csv/cli_signal_check.py --after 3 --signal INT -- URL_OR_FILE ...

``--after`` is in seconds. ``--when-serving`` waits until the server
answers instead. SIGINT is what Ctrl+C sends; the child is started with
SIGINT at its default action so it behaves like a foreground process.
"""

import argparse
import glob
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request


def leftovers():
    tmp = tempfile.gettempdir()
    return set(
        glob.glob(os.path.join(tmp, "datasette-import-*"))
        + glob.glob(os.path.join(tmp, "datasette-scratch-*"))
        + glob.glob(os.path.join(tmp, "datasette-reload-*"))
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--after", type=float, default=3.0)
    parser.add_argument("--signal", default="INT")
    parser.add_argument("--when-serving", action="store_true")
    parser.add_argument("--port", type=int, default=8798)
    parser.add_argument("args", nargs="+")
    options = parser.parse_args()
    before = leftovers()
    env = dict(os.environ, DATASETTE_SECRET="not_a_secret")
    process = subprocess.Popen(
        [sys.executable, "-m", "datasette", *options.args, "-p", str(options.port)],
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        env=env,
        preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL),
    )
    start = time.monotonic()
    if options.when_serving:
        while time.monotonic() - start < 600:
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{options.port}/-/versions.json", timeout=1
                )
                break
            except Exception:
                time.sleep(0.1)
    else:
        time.sleep(options.after)
    sent = time.monotonic()
    process.send_signal(getattr(signal, "SIG" + options.signal))
    _, err = process.communicate(timeout=60)
    print(f"signal SIG{options.signal} after {sent - start:.1f}s")
    print(f"exit code {process.returncode} after {time.monotonic() - sent:.2f}s")
    print("stderr (last lines):")
    for line in err.decode().splitlines()[-6:]:
        print("   ", line)
    time.sleep(0.2)
    left = leftovers() - before
    print(f"temporary files left behind: {sorted(left) or 'none'}")


if __name__ == "__main__":
    main()
