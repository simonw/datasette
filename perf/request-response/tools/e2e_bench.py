"""
Orchestrate end-to-end A/B runs against several `datasette serve` variants.

Set E2E_BASE_TREE and E2E_PATCHED_TREE to the two source trees, and
E2E_HTTPTOOLS_PYTHON to a venv's python that has httptools installed.
The report used: base, httptools, h11_nolog, patched, patched_httptools,
patched_best.

  python e2e_bench.py start            # start all variant servers (pids in server_PORT.pid)
  python e2e_bench.py stop             # kill them
  python e2e_bench.py tput  --rounds 3 --variants a,b --endpoints x,y --conns 8 --secs 8
  python e2e_bench.py lat   --rounds 3 ...   (1 connection)

Results are appended as JSON lines to final_results_<kind>.jsonl and a median
summary is printed.
"""

import argparse
import json
import os
import signal
import statistics
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# fixtures.db is created by bench_asgi.py on its first run
FIXTURES = os.path.join(HERE, "fixtures.db")
# Python with datasette's dependencies but no httptools (uvicorn falls back to h11)
SHARED_PY = os.environ.get("E2E_PYTHON", sys.executable)
# Python in a second venv that also has httptools (and uvloop) installed
SCRATCH_PY = os.environ.get("E2E_HTTPTOOLS_PYTHON", SHARED_PY)
LOADGEN_PY = SHARED_PY
# Source trees: cf113a5, and cf113a5 with patches/ALL-combined.diff applied
BASE_TREE = os.environ.get("E2E_BASE_TREE", "")
PATCHED = os.environ.get("E2E_PATCHED_TREE", "")
SERVE_ARGS = [FIXTURES, "--setting", "num_sql_threads", "3", "--secret", "yoursecret"]

# name: (port, python, use serve_variant.py?, env)
VARIANTS = {
    # plain `python -m datasette serve` from the shared venv: h11 + asyncio + access log
    "base": (8873, SHARED_PY, False, {"PYTHONPATH": BASE_TREE}),
    # scratch venv (httptools+uvloop installed) forced back to h11: A/A control for base
    "h11": (8874, SCRATCH_PY, True, {"E2E_HTTP": "h11"}),
    # plain `datasette serve` in the scratch venv: uvicorn http=auto picks httptools
    "httptools": (8875, SCRATCH_PY, False, {"PYTHONPATH": BASE_TREE}),
    "h11_nolog": (
        8876,
        SCRATCH_PY,
        True,
        {"E2E_HTTP": "h11", "E2E_ACCESS_LOG": "0", "PYTHONPATH": BASE_TREE},
    ),
    "httptools_nolog": (
        8877,
        SCRATCH_PY,
        True,
        {"E2E_HTTP": "httptools", "E2E_ACCESS_LOG": "0"},
    ),
    "h11_cl": (8879, SCRATCH_PY, True, {"E2E_HTTP": "h11", "E2E_CONTENT_LENGTH": "1"}),
    "h11_coalesce": (8880, SCRATCH_PY, True, {"E2E_HTTP": "h11", "E2E_COALESCE": "1"}),
    "h11_gcfreeze": (8881, SCRATCH_PY, True, {"E2E_HTTP": "h11", "E2E_GC_FREEZE": "1"}),
    "h11_uvloop": (8882, SCRATCH_PY, True, {"E2E_HTTP": "h11", "E2E_LOOP": "uvloop"}),
    "h11_cl_coalesce": (
        8883,
        SCRATCH_PY,
        True,
        {"E2E_HTTP": "h11", "E2E_CONTENT_LENGTH": "1", "E2E_COALESCE": "1"},
    ),
    "httptools_uvloop": (
        8884,
        SCRATCH_PY,
        True,
        {"E2E_HTTP": "httptools", "E2E_LOOP": "uvloop"},
    ),
    "h11_static1m": (
        8885,
        SCRATCH_PY,
        True,
        {"E2E_HTTP": "h11", "E2E_STATIC_CHUNK": "1048576"},
    ),
    "best": (
        8886,
        SCRATCH_PY,
        True,
        {
            "E2E_HTTP": "httptools",
            "E2E_ACCESS_LOG": "0",
            "E2E_LOOP": "uvloop",
            "E2E_CONTENT_LENGTH": "1",
            "E2E_GC_FREEZE": "1",
            "E2E_STATIC_CHUNK": "1048576",
        },
    ),
    "httptools_cl": (
        8887,
        SCRATCH_PY,
        True,
        {"E2E_HTTP": "httptools", "E2E_CONTENT_LENGTH": "1"},
    ),
    "h11_switch500us": (
        8888,
        SCRATCH_PY,
        True,
        {"E2E_HTTP": "h11", "E2E_SWITCH": "0.0005"},
    ),
    "h11_switch1ms": (
        8889,
        SCRATCH_PY,
        True,
        {"E2E_HTTP": "h11", "E2E_SWITCH": "0.001"},
    ),
    # other agents' in-process patches (ds_patched/, see apply_patches.py), unchanged server stack
    "patched": (8890, SHARED_PY, False, {"PYTHONPATH": PATCHED}),
    "patched_httptools": (8891, SCRATCH_PY, False, {"PYTHONPATH": PATCHED}),
    # only middleware-gc-3 (csrftoken closure <-> scope reference cycle removed)
    "patched_best": (
        8892,
        SCRATCH_PY,
        True,
        {
            "PYTHONPATH": PATCHED,
            "E2E_HTTP": "httptools",
            "E2E_ACCESS_LOG": "0",
            "E2E_CONTENT_LENGTH": "1",
        },
    ),
}

ENDPOINTS = {
    "actor": ("/-/actor.json", True),
    "versions": ("/-/versions.json", False),
    "static_css": ("/-/static/app.css", False),
    "facetable": ("/fixtures/facetable.json?_size=5", False),
}


def _read(path):
    with open(path) as fp:
        return fp.read()


def cookie():
    # ds_actor cookie for {"id": "root"}, signed with the --secret in SERVE_ARGS
    from itsdangerous import URLSafeSerializer

    return URLSafeSerializer("yoursecret", "actor").dumps({"a": {"id": "root"}})


def start(names):
    for name in names:
        port, py, use_variant, env = VARIANTS[name]
        pidfile = os.path.join(HERE, f"server_{port}.pid")
        if os.path.exists(pidfile):
            try:
                os.kill(int(_read(pidfile)), 0)
                print(f"{name}: already running on {port}")
                continue
            except (OSError, ValueError):
                pass
        if use_variant:
            cmd = [py, os.path.join(HERE, "serve_variant.py"), "serve"]
        else:
            cmd = [py, "-m", "datasette", "serve"]
        cmd += SERVE_ARGS + ["-p", str(port)]
        full_env = dict(os.environ)
        full_env.pop("PYTHONPATH", None)
        full_env.update(env)
        log = open(  # noqa: SIM115 - handed to the server process
            os.path.join(HERE, f"server_{port}.log"), "w"
        )
        proc = subprocess.Popen(
            cmd,
            cwd=HERE,
            env=full_env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        with open(pidfile, "w") as fp:
            fp.write(str(proc.pid))
        print(f"{name}: started pid {proc.pid} on {port}")
    for name in names:
        port = VARIANTS[name][0]
        for _ in range(100):
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/-/versions.json", timeout=2
                ).read()
                break
            except OSError:
                time.sleep(0.2)
        else:
            print(f"{name}: did not come up")


def stop(names):
    for name in names:
        port = VARIANTS[name][0]
        pidfile = os.path.join(HERE, f"server_{port}.pid")
        if not os.path.exists(pidfile):
            continue
        pid = int(_read(pidfile))
        try:
            os.kill(pid, signal.SIGINT)
            for _ in range(50):
                time.sleep(0.1)
                os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        os.remove(pidfile)
        print(f"{name}: stopped {pid}")


CLK = os.sysconf("SC_CLK_TCK")


def cpu_ticks(pid):
    """(process utime+stime, main-thread utime+stime) in clock ticks."""

    def read(path):
        with open(path) as fp:
            fields = fp.read().rsplit(")", 1)[1].split()
        # fields[0] is state (field 3); utime=field 14, stime=field 15
        return int(fields[11]) + int(fields[12])

    return read(f"/proc/{pid}/stat"), read(f"/proc/{pid}/task/{pid}/stat")


def run_load(name, endpoint, conns, secs, procs, warmup=1.0):
    port = VARIANTS[name][0]
    pid = int(_read(os.path.join(HERE, f"server_{port}.pid")))
    before = cpu_ticks(pid)
    res = _run_load(name, endpoint, conns, secs, procs, warmup)
    after = cpu_ticks(pid)
    n = res.get("requests_total") or res["requests"]
    res["cpu_us_per_req"] = round((after[0] - before[0]) / CLK * 1e6 / n, 1)
    res["main_cpu_us_per_req"] = round((after[1] - before[1]) / CLK * 1e6 / n, 1)
    return res


def _run_load(name, endpoint, conns, secs, procs, warmup=1.0):
    port = VARIANTS[name][0]
    path, needs_cookie = ENDPOINTS[endpoint]
    cmd = [
        LOADGEN_PY,
        os.path.join(HERE, "load.py"),
        "--port",
        str(port),
        "--path",
        path,
        "-c",
        str(conns),
        "-d",
        str(secs),
        "--warmup",
        str(warmup),
        "--procs",
        str(procs),
        "--json",
        "--label",
        name,
    ]
    if needs_cookie:
        cmd += ["--cookie", "ds_actor=" + cookie()]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def bench(kind, variants, endpoints, rounds, conns, secs, procs):
    results_path = os.path.join(HERE, f"final_results_{kind}.jsonl")
    collected = {}
    for r in range(rounds):
        for ep in endpoints:
            order = list(variants)
            if r % 2:
                order.reverse()
            for v in order:
                res = run_load(v, ep, conns, secs, procs)
                res.update(
                    {
                        "variant": v,
                        "endpoint": ep,
                        "round": r,
                        "kind": kind,
                        "ts": time.time(),
                    }
                )
                with open(results_path, "a") as fp:
                    fp.write(json.dumps(res) + "\n")
                collected.setdefault((ep, v), []).append(res)
                print(
                    f"r{r} {ep:10s} {v:24s} {res['rps']:8.1f} req/s  p50 {res['lat_us']['p50']:8.1f}us "
                    f"p99 {res['lat_us']['p99']:8.1f}us  cpu {res.get('cpu_us_per_req')}us/req "
                    f"main {res.get('main_cpu_us_per_req')}us/req status={res['status']}",
                    flush=True,
                )
    summarize(collected, variants, endpoints)


def summarize(collected, variants, endpoints):
    print("\nMedians (us/request = 1e6 / req/s; latency p50/p99 in us):")
    for ep in endpoints:
        base = None
        for v in variants:
            rs = collected.get((ep, v))
            if not rs:
                continue
            rps = statistics.median(x["rps"] for x in rs)
            p50 = statistics.median(x["lat_us"]["p50"] for x in rs)
            p99 = statistics.median(x["lat_us"]["p99"] for x in rs)
            us = 1e6 / rps
            cpus = [x["cpu_us_per_req"] for x in rs if x.get("cpu_us_per_req")]
            mains = [
                x["main_cpu_us_per_req"] for x in rs if x.get("main_cpu_us_per_req")
            ]
            cpu = statistics.median(cpus) if cpus else float("nan")
            mcpu = statistics.median(mains) if mains else float("nan")
            if base is None:
                base = us
                base_cpu = cpu
            print(
                f"  {ep:10s} {v:24s} rps {rps:8.1f}  {us:8.1f} us/req ({(us - base) / base:+6.1%})"
                f"  cpu {cpu:7.1f} us/req ({(cpu - base_cpu) / base_cpu:+6.1%}) main {mcpu:7.1f}"
                f"  p50 {p50:8.1f}  p99 {p99:8.1f}   runs={[x['rps'] for x in rs]}"
            )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("cmd")
    p.add_argument("--variants", default=",".join(VARIANTS))
    p.add_argument("--endpoints", default=",".join(ENDPOINTS))
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--conns", type=int, default=8)
    p.add_argument("--secs", type=float, default=8)
    p.add_argument("--procs", type=int, default=2)
    args = p.parse_args()
    variants = args.variants.split(",")
    endpoints = args.endpoints.split(",")
    if args.cmd == "start":
        start(variants)
    elif args.cmd == "stop":
        stop(variants)
    elif args.cmd == "tput":
        bench(
            "tput", variants, endpoints, args.rounds, args.conns, args.secs, args.procs
        )
    elif args.cmd == "lat":
        bench("lat", variants, endpoints, args.rounds, 1, args.secs, 1)
    elif args.cmd == "summary":
        kind = args.endpoints if False else None
        for kind in ("tput", "lat"):
            path = os.path.join(HERE, f"final_results_{kind}.jsonl")
            if not os.path.exists(path):
                continue
            collected = {}
            for line in _read(path).splitlines():
                x = json.loads(line)
                if x["variant"] in variants and x["endpoint"] in endpoints:
                    collected.setdefault((x["endpoint"], x["variant"]), []).append(x)
            print(f"== {kind}")
            summarize(collected, variants, endpoints)


if __name__ == "__main__":
    main()
