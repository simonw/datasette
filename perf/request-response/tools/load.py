"""
Minimal HTTP/1.1 keep-alive load generator (stdlib only, raw asyncio sockets).

Each of N connections is an asyncio.Protocol that sends one GET, parses the
response (Content-Length or chunked), records latency, and immediately sends
the next request on the same connection (closed-loop, like wrk -c N).

Usage:
    python load.py --port 8801 --path /-/versions.json -c 8 -d 10 [--warmup 1]
        [--cookie 'ds_actor=...'] [--header 'If-None-Match: "abc"']
        [--procs 2] [--json]

--procs P splits the N connections over P worker processes so that the load
generator itself is not the bottleneck.
"""

import argparse
import asyncio
import json
import multiprocessing
import statistics
import time

HEADERS, BODY_CL, CHUNK_SIZE, CHUNK_DATA, CHUNK_END = range(5)


class Conn(asyncio.Protocol):
    def __init__(self, request, stats, deadline_holder, done):
        self.request = request
        self.stats = stats
        self.deadline = deadline_holder
        self.done = done
        self.buf = bytearray()
        self.state = HEADERS
        self.remaining = 0
        self.status = 0
        self.body_bytes = 0
        self.t0 = 0

    def connection_made(self, transport):
        self.transport = transport
        self.send()

    def send(self):
        self.state = HEADERS
        self.body_bytes = 0
        self.t0 = time.perf_counter_ns()
        self.transport.write(self.request)

    def connection_lost(self, exc):
        if not self.done.done():
            self.stats["errors"] += 1
            self.done.set_result(None)

    def data_received(self, data):
        self.buf += data
        while self._parse():
            now = time.perf_counter_ns()
            st = self.stats
            st["all"] += 1
            if now >= st["measure_from"]:
                st["lat"].append(now - self.t0)
                st["status"][self.status] = st["status"].get(self.status, 0) + 1
                st["bytes"] += self.body_bytes
            if now >= self.deadline[0]:
                if not self.done.done():
                    self.done.set_result(None)
                self.transport.close()
                return
            self.send()

    def _parse(self):
        """Advance the state machine. Return True when a full response is in."""
        buf = self.buf
        while True:
            if self.state == HEADERS:
                idx = buf.find(b"\r\n\r\n")
                if idx < 0:
                    return False
                head = bytes(buf[:idx])
                del buf[: idx + 4]
                self.status = int(head[9:12])
                lower = head.lower()
                cl = lower.find(b"\r\ncontent-length:")
                if cl >= 0:
                    end = lower.find(b"\r\n", cl + 2)
                    if end < 0:
                        end = len(lower)
                    self.remaining = int(lower[cl + 17 : end].strip())
                    self.state = BODY_CL
                elif b"\r\ntransfer-encoding: chunked" in lower:
                    self.state = CHUNK_SIZE
                else:
                    # 304 / 204 / no body
                    self.state = HEADERS
                    return True
            elif self.state == BODY_CL:
                n = min(self.remaining, len(buf))
                del buf[:n]
                self.remaining -= n
                self.body_bytes += n
                if self.remaining:
                    return False
                self.state = HEADERS
                return True
            elif self.state == CHUNK_SIZE:
                idx = buf.find(b"\r\n")
                if idx < 0:
                    return False
                size = int(bytes(buf[:idx]).split(b";")[0], 16)
                del buf[: idx + 2]
                if size == 0:
                    self.state = CHUNK_END
                else:
                    self.remaining = size + 2
                    self.body_bytes += size
                    self.state = CHUNK_DATA
            elif self.state == CHUNK_DATA:
                n = min(self.remaining, len(buf))
                del buf[:n]
                self.remaining -= n
                if self.remaining:
                    return False
                self.state = CHUNK_SIZE
            elif self.state == CHUNK_END:
                # assume no trailers
                if len(buf) < 2:
                    return False
                del buf[:2]
                self.state = HEADERS
                return True


def build_request(args):
    lines = [
        f"GET {args.path} HTTP/1.1",
        f"Host: {args.host}:{args.port}",
        "User-Agent: load.py",
        "Accept: */*",
        "Connection: keep-alive",
    ]
    if args.cookie:
        lines.append(f"Cookie: {args.cookie}")
    lines.extend(args.header or [])
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


async def run_worker(args, conns):
    loop = asyncio.get_running_loop()
    request = build_request(args)
    start = time.perf_counter_ns()
    stats = {
        "lat": [],
        "status": {},
        "bytes": 0,
        "errors": 0,
        "all": 0,
        "measure_from": start + int(args.warmup * 1e9),
    }
    deadline = [start + int((args.warmup + args.duration) * 1e9)]
    dones = []
    for _ in range(conns):
        done = loop.create_future()
        dones.append(done)
        await loop.create_connection(
            lambda d=done: Conn(request, stats, deadline, d), args.host, args.port
        )
    await asyncio.gather(*dones)
    end = time.perf_counter_ns()
    stats["elapsed"] = (end - stats["measure_from"]) / 1e9
    return stats


def worker_entry(payload):
    args, conns = payload
    return asyncio.run(run_worker(args, conns))


def pct(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    k = min(len(sorted_vals) - 1, round(p / 100 * (len(sorted_vals) - 1)))
    return sorted_vals[k]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--path", required=True)
    p.add_argument("-c", "--connections", type=int, default=8)
    p.add_argument("-d", "--duration", type=float, default=10)
    p.add_argument("--warmup", type=float, default=1.0)
    p.add_argument("--procs", type=int, default=1)
    p.add_argument("--cookie")
    p.add_argument("--header", action="append")
    p.add_argument("--json", action="store_true")
    p.add_argument("--label", default="")
    args = p.parse_args()

    procs = max(1, min(args.procs, args.connections))
    if procs == 1:
        results = [asyncio.run(run_worker(args, args.connections))]
    else:
        split = [args.connections // procs] * procs
        for i in range(args.connections % procs):
            split[i] += 1
        ctx = multiprocessing.get_context("fork")
        with ctx.Pool(procs) as pool:
            results = pool.map(worker_entry, [(args, n) for n in split])

    lat = sorted(x for r in results for x in r["lat"])
    status = {}
    for r in results:
        for k, v in r["status"].items():
            status[k] = status.get(k, 0) + v
    elapsed = statistics.mean(r["elapsed"] for r in results)
    n = len(lat)
    out = {
        "label": args.label,
        "path": args.path,
        "connections": args.connections,
        "procs": procs,
        "requests": n,
        "elapsed_s": round(elapsed, 3),
        "rps": round(n / elapsed, 1) if elapsed else 0,
        "lat_us": {
            "mean": round(statistics.mean(lat) / 1e3, 1) if lat else None,
            "p50": round(pct(lat, 50) / 1e3, 1),
            "p90": round(pct(lat, 90) / 1e3, 1),
            "p99": round(pct(lat, 99) / 1e3, 1),
            "p99.9": round(pct(lat, 99.9) / 1e3, 1),
            "max": round(lat[-1] / 1e3, 1) if lat else None,
        },
        "status": status,
        "avg_body_bytes": round(sum(r["bytes"] for r in results) / n) if n else 0,
        "errors": sum(r["errors"] for r in results),
        "requests_total": sum(r["all"] for r in results),
    }
    if args.json:
        print(json.dumps(out))
    else:
        l = out["lat_us"]
        print(
            f"{args.label or args.path}: c={args.connections} {out['rps']} req/s "
            f"n={n} p50={l['p50']}us p90={l['p90']}us p99={l['p99']}us "
            f"max={l['max']}us status={status} body={out['avg_body_bytes']}B "
            f"errors={out['errors']}"
        )


if __name__ == "__main__":
    main()
