"""
Summarise a collapsed-stack profile from `python -m profiling.sampling --collapsed`.

Only stacks that pass through the steady-state request loop (default
frame substring "run_loop") are counted, so interpreter start-up, imports
and Datasette.__init__ are excluded.

Usage:
    python analyze_collapsed.py FILE [--root run_loop] [--top 40]
        [--children FUNC_SUBSTRING]   # show what FUNC spends its time on
        [--buckets]                   # attribute samples to components
"""

import argparse
import collections
import re


def load(path, root, exclude=None):
    stacks = []
    with open(path) as fp:
        lines = fp.read().splitlines()
    for line in lines:
        line = line.rstrip("\n")
        if not line:
            continue
        stack, _, count = line.rpartition(" ")
        frames = stack.split(";")
        if frames and frames[0].startswith("tid:"):
            frames = frames[1:]
        if exclude and any(re.search(exclude, f) for f in frames):
            continue
        if root:
            idx = next((i for i, f in enumerate(frames) if root in f), None)
            if idx is None:
                continue
            frames = frames[idx:]
        stacks.append((frames, int(count)))
    return stacks


def func_of(frame):
    # "file.py:Qual.name:123" -> "file.py:Qual.name"
    return frame.rsplit(":", 1)[0] if re.search(r":\d+$", frame) else frame


# Ordered: the first bucket whose pattern matches the deepest frame that
# matches *any* bucket wins (so GC under cookies counts as GC).
REQUEST_ROOT = "Driver.request"

# Ordered by priority: for each stack, walk from the leaf towards the
# request root and assign the first bucket that matches any frame.
BUCKETS = [
    ("GC", r"<GC>"),
    (
        "static file IO (aiofiles)",
        r"(aiofiles|asgi_send_file|calculate_etag|sha256_file)",
    ),
    (
        "sqlite/db execution",
        r"(sqlite3|Database\.(execute|_execute|execute_fn|_execute_fn|execute_write|table_counts|table_exists|view_exists|primary_keys|table_columns|fts_table|hidden_table_names|label_column_for_table|foreign_keys_for_table|get_all_foreign_keys|view_names|table_names)|detect_json1|_versions|populate_schema|refresh_schemas)",
    ),
    (
        "thread-pool handoff",
        r"(ThreadPoolExecutor|run_in_executor|_read_from_self|_ThreadSafeHandle|_copy_future_state|_chain_future|wrap_future)",
    ),
    (
        "jinja2 render",
        r"(jinja2|environment\.py|runtime\.py|\.html:|loaders\.py|async_utils|nativetypes|filters\.py:do_)",
    ),
    (
        "json encode/decode",
        r"(json/|encoder\.py|decoder\.py|JSONEncoder|json\.dumps|dumps$)",
    ),
    ("cookie parse (SimpleCookie)", r"(cookies\.py|Request\.cookies)"),
    ("Request.headers dict rebuild", r"Request\.headers"),
    (
        "itsdangerous sign/unsign",
        r"(itsdangerous|signer\.py|serializer\.py|url_safe\.py|_json\.py|encoding\.py|hmac\.py|Datasette\.(sign|unsign))",
    ),
    (
        "opentelemetry",
        r"(opentelemetry|_decorator\.py|trace/__init__|context/__init__|propagat|contextvars_context|ProxyTracer|NoOpTracer|telemetry\.py:(request_span|record_operation|_url_path|_first_header))",
    ),
    ("pluggy hook dispatch", r"(_hooks\.py|_callers\.py|_manager\.py|_result\.py)"),
    (
        "permissions",
        r"(permissions|actions_sql|default_permissions|_allowed_many|allowed_many|Datasette\.allowed)",
    ),
    (
        "url building/parsing",
        r"(url_builder|Urls\.|urljoin|urlunparse|urlsplit|urlparse|parse\.py|PrefixedUrlString|tilde_|path_with_format|absolute_url|Request\.url)",
    ),
    ("query string parse", r"(parse_qs|MultiParams|Request\.args)"),
    ("routing", r"(resolve_routes|_compiler)"),
    ("inspect/signature", r"(inspect\.py)"),
    (
        "asyncio machinery",
        r"(base_events|events\.py|tasks\.py|futures\.py|locks\.py|coroutines)",
    ),
]
IDLE = "event loop idle (awaiting threads)"


def bucket_for(frames):
    if any(re.search(r"(selectors\.py|EpollSelector)", f) for f in frames[-2:]):
        return IDLE
    root = next((i for i, f in enumerate(frames) if REQUEST_ROOT in f), None)
    scan = frames[root:] if root is not None else frames[-1:]
    for frame in reversed(scan):
        for name, pattern in BUCKETS:
            if re.search(pattern, frame):
                return name
    if root is None:
        return "asyncio machinery"
    return "datasette/other python"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("file")
    p.add_argument("--root", default="_run_once")
    p.add_argument(
        "--exclude", default=r"(Driver\.setup|invoke_startup|_startup_sequence|make_ds)"
    )
    p.add_argument("--top", type=int, default=40)
    p.add_argument("--children")
    p.add_argument("--buckets", action="store_true")
    p.add_argument("--leaf-buckets", action="store_true")
    args = p.parse_args()
    stacks = load(args.file, args.root, args.exclude)
    total = sum(c for _, c in stacks)
    print(f"steady-state samples: {total}")

    inclusive = collections.Counter()
    exclusive = collections.Counter()
    for frames, count in stacks:
        seen = set()
        for f in frames:
            fn = func_of(f)
            if fn not in seen:
                inclusive[fn] += count
                seen.add(fn)
        exclusive[func_of(frames[-1])] += count

    if args.children:
        child_counts = collections.Counter()
        parent_total = 0
        for frames, count in stacks:
            idx = next((i for i, f in enumerate(frames) if args.children in f), None)
            if idx is None:
                continue
            parent_total += count
            if idx + 1 < len(frames):
                child_counts[frames[idx + 1]] += count
            else:
                child_counts["<self>"] += count
        print(
            f"\n{args.children}: {parent_total} samples ({parent_total / total:.1%} of total)"
        )
        for child, c in child_counts.most_common(args.top):
            print(f"  {c / total:6.1%}  {c / parent_total:6.1%} of parent  {child}")
        return

    if args.buckets:
        b = collections.Counter()
        for frames, count in stacks:
            b[bucket_for(frames)] += count
        print("\nComponent attribution (deepest matching frame wins):")
        for name, c in b.most_common():
            print(f"  {c / total:6.1%}  {name}")

    print("\nTop inclusive:")
    for fn, c in inclusive.most_common(args.top):
        print(f"  {c / total:6.1%}  {fn}")
    print("\nTop exclusive (self):")
    for fn, c in exclusive.most_common(args.top):
        print(f"  {c / total:6.1%}  {fn}")


if __name__ == "__main__":
    main()
