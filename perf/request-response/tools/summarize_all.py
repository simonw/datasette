import collections
import json
import sys

import analyze_collapsed as A

eps = sys.argv[1].split(",")
prefix = sys.argv[2] if len(sys.argv) > 2 else "profiles/"
out = {}
for ep in eps:
    stacks = A.load(
        f"{prefix}{ep}.collapsed",
        "_run_once",
        r"(Driver\.setup|invoke_startup|_startup_sequence|make_ds)",
    )
    total = sum(c for _, c in stacks)
    b = collections.Counter()
    for f, c in stacks:
        b[A.bucket_for(f)] += c
    idle = b.pop(A.IDLE, 0)
    busy = total - idle
    out[ep] = {"idle_pct": idle / total, "busy": {k: v / busy for k, v in b.items()}}
    top = ", ".join(f"{k} {v/busy:.0%}" for k, v in b.most_common(7))
    print(f"{ep:18s} idle {idle/total:5.1%} | {top}")
with open(prefix.rstrip("/") + "_buckets.json", "w") as fp:
    json.dump(out, fp, indent=1)
