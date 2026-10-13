"""
Interleaved A/B timing of bench_asgi.py endpoints across source trees.

    python measure_ab.py --tree base=/home/user/datasette --tree new=/path/wt \
        --endpoints a,b --n 500 --rounds 5 --out results.json

Each round runs every tree once (in rotating order) in a fresh subprocess,
so slow drift and noise affect all trees equally. Reports the median of the
per-round medians.
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def run_once(tree, endpoints, n, extra_env):
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as fp:
        out = fp.name
    env = dict(os.environ, PYTHONPATH=tree, **extra_env)
    subprocess.run(
        [
            sys.executable,
            os.path.join(HERE, "bench_asgi.py"),
            "time",
            "--endpoints",
            ",".join(endpoints),
            "--n",
            str(n),
            "--rounds",
            "1",
            "--json-out",
            out,
        ],
        env=env,
        check=True,
        stdout=subprocess.DEVNULL,
        cwd=HERE,
    )
    with open(out) as fp:
        data = json.load(fp)
    os.unlink(out)
    return data


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tree", action="append", required=True, help="name=path")
    p.add_argument("--endpoints", required=True)
    p.add_argument("--n", type=int, default=500)
    p.add_argument("--rounds", type=int, default=5)
    p.add_argument("--out")
    args = p.parse_args()
    trees = [t.split("=", 1) for t in args.tree]
    endpoints = args.endpoints.split(",")
    samples = {name: {ep: [] for ep in endpoints} for name, _ in trees}
    cpu = {name: {ep: [] for ep in endpoints} for name, _ in trees}
    statuses = {name: {} for name, _ in trees}
    for r in range(args.rounds):
        order = trees[r % len(trees) :] + trees[: r % len(trees)]
        for name, path in order:
            data = run_once(path, endpoints, args.n, {})
            for ep in endpoints:
                samples[name][ep].append(data[ep]["median_us"])
                if "cpu_us" in data[ep]:
                    cpu[name][ep].append(data[ep]["cpu_us"])
                statuses[name][ep] = (data[ep]["status"], data[ep]["bytes"])
        print(f"round {r + 1}/{args.rounds} done", file=sys.stderr, flush=True)
    result = {}
    base_name = trees[0][0]
    header = f"{'endpoint':22s}" + "".join(f"{name:>16s}" for name, _ in trees)
    print(header)
    for ep in endpoints:
        row = f"{ep:22s}"
        base = statistics.median(samples[base_name][ep])
        result[ep] = {}
        for name, _ in trees:
            med = statistics.median(samples[name][ep])
            result[ep][name] = {
                "median_us": med,
                "runs": samples[name][ep],
                "status_bytes": statuses[name][ep],
                "cpu_us": statistics.median(cpu[name][ep]) if cpu[name][ep] else None,
                "cpu_runs": cpu[name][ep],
            }
            if name == base_name:
                row += f"{med:14.1f}us"
            else:
                row += f"{med:9.1f}us {100 * (med - base) / base:+5.0f}%"
        print(row)
    if any(cpu[n][e] for n, _ in trees for e in endpoints):
        print("\nCPU time per request (process_time, all threads):")
        print(header)
        for ep in endpoints:
            row = f"{ep:22s}"
            base = statistics.median(cpu[base_name][ep])
            for name, _ in trees:
                med = statistics.median(cpu[name][ep])
                if name == base_name:
                    row += f"{med:14.1f}us"
                else:
                    row += f"{med:9.1f}us {100 * (med - base) / base:+5.0f}%"
            print(row)
    for ep in endpoints:
        sb = {name: statuses[name][ep] for name, _ in trees}
        if len(set(sb.values())) > 1:
            print(f"WARNING: status/bytes differ for {ep}: {sb}")
    if args.out:
        with open(args.out, "w") as fp:
            json.dump(result, fp, indent=1)


if __name__ == "__main__":
    main()
