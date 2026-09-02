"""
Two-way conformance between `datasette/telemetry_registry.py` and what
Datasette actually emits.

This is the test that makes the generated documentation trustworthy. cog
guarantees the docs match the registry; this guarantees the registry matches
the code. Without it, both could agree with each other and be wrong.

It checks both directions, and the second one is the one nothing else catches:

- **emitted but not registered** - instrumentation was added without
  documenting it, so the reference page silently omits it.
- **registered but never emitted** - the reference page describes a span or
  attribute that no longer exists, which is worse than omitting it, because a
  reader will build a dashboard on it.

Both of those directions compare the code against the registry. Neither can
catch a *rename*, because the call sites now take their names from the
registry - move `DB_NAMESPACE` to `"db.namespace2"` and code and registry
still agree with each other, while every existing dashboard breaks. So the
literal names live here too, spelled out, and are asserted against both the
registry and the wire. That is the one comparison in this file that is not
made against a value derived from the registry itself.
"""

import itertools

import pytest
import pytest_asyncio

pytest.importorskip("opentelemetry.sdk")

from datasette import telemetry_registry as reg
from datasette.app import Datasette
from datasette.database import QueryInterrupted
from datasette.utils.sqlite import sqlite3

# The names as they appear on the wire, written out rather than read from the
# registry. If a change to the registry makes one of these fail, that change
# is renaming something a user's dashboards and saved queries depend on -
# which is a decision to take deliberately, here, not a line to re-derive.
EXPECTED_ATTRIBUTES = {
    "db.query": {
        "db.system",
        "db.namespace",
        "db.query.text",
        "db.operation.name",
        "db.collection.name",
        "datasette.param_count",
        "datasette.param_sets",
        "datasette.time_limit_ms",
        "datasette.rows_returned",
        "datasette.truncated",
        "datasette.interrupted",
        "datasette.sql_error_suppressed",
        "datasette.executescript",
        "datasette.executemany",
    },
    "db.query.execute": set(),
    "db.write.queue_wait": set(),
    "db.write.execute": {
        "datasette.isolated_connection",
        "datasette.transaction",
    },
    "datasette.startup": set(),
}
EXPECTED_SPANS = set(EXPECTED_ATTRIBUTES)

# Named in-memory databases are shared-cache, so two Datasette instances using
# the same name share one SQLite database - and the second `create table`
# fails. Every workload below therefore gets its own name.
_names = itertools.count()


def _unique(prefix):
    return f"{prefix}{next(_names)}"


async def exercise():
    """
    Drive enough of Datasette to emit every span and attribute the registry
    claims exists.

    Each call is here because it is the only thing that produces some span or
    attribute - see the comments. If you add instrumentation on a path this
    does not reach, add the path rather than loosening the assertions.

    Returns the instance so the caller can close it; startup happens inside
    so that the `datasette.startup` span lands in the collected set.
    """
    name = _unique("registry")
    ds = Datasette(memory=True)
    ds.add_memory_database(name)
    # datasette.startup - and the internal catalog work nested under it
    await ds.invoke_startup()
    db = ds.get_database(name)

    # Writes: db.write.queue_wait, db.write.execute, db.query
    await db.execute_write("create table t (id integer primary key, v text)")
    # datasette.executemany, datasette.param_sets
    await db.execute_write_many(
        "insert into t (id, v) values (?, ?)", [[i, f"v{i}"] for i in range(30)]
    )
    # datasette.executescript
    await db.execute_write_script("create table t2 (id integer); drop table t2;")
    # datasette.transaction=False - VACUUM cannot run inside a transaction
    await db.execute_write("vacuum", transaction=False)
    # datasette.isolated_connection=True
    await db.execute_isolated_fn(lambda conn: conn.execute("select 1").fetchone())

    # Reads: db.query.execute, datasette.rows_returned, datasette.truncated,
    # datasette.param_count, datasette.time_limit_ms
    await db.execute("select * from t where id > :n", {"n": 5})
    await db.execute("select * from t", truncate=True)

    # datasette.sql_error_suppressed - the caller is probing and treats
    # failure as an expected answer
    with pytest.raises(sqlite3.OperationalError):
        await db.execute("select nope from t", log_sql_errors=False)

    # datasette.interrupted - only ever set when a query exceeds its time
    # limit, so the workload has to force one rather than exempt it. An
    # unbounded recursive CTE cannot finish, so 1ms is always exceeded.
    with pytest.raises(QueryInterrupted):
        await db.execute(
            "with recursive c(x) as (select 0 union all select x+1 from c) "
            "select * from c",
            custom_time_limit=1,
        )

    # db.collection.name - set only by views that already know their table
    assert (await ds.client.get(f"/{name}/t?_facet=v")).status_code == 200
    assert (await ds.client.get(f"/{name}/t/1.json")).status_code == 200
    return ds


@pytest_asyncio.fixture
async def emitted(otel_spans):
    "Every span name and (span name, attribute key) pair a broad workload emits."
    # otel_spans has already cleared the exporter, and nothing is cleared
    # after this point: the workload's own startup emits datasette.startup.
    ds = await exercise()
    spans = otel_spans.get_finished_spans()
    assert spans, "no spans captured - the fixture is not exercising anything"
    names = set()
    pairs = set()
    for span in spans:
        # str() because span.name is the registry's SpanName instance, and a
        # set of those would compare equal to literals but read confusingly
        # in a failure message.
        names.add(str(span.name))
        for key in span.attributes or {}:
            pairs.add((str(span.name), str(key)))
    ds.close()
    return {"names": names, "pairs": pairs}


def _keys_by_span(pairs):
    by_span = {}
    for span_name, key in pairs:
        by_span.setdefault(span_name, set()).add(key)
    return by_span


@pytest.mark.asyncio
async def test_workload_emits_exactly_the_expected_names(emitted):
    """
    The wire format, pinned to literals.

    Not derived from the registry, so this is what catches a rename that the
    registry and the call sites make together.
    """
    assert emitted["names"] == EXPECTED_SPANS
    by_span = _keys_by_span(emitted["pairs"])
    assert {name: by_span.get(name, set()) for name in emitted["names"]} == (
        EXPECTED_ATTRIBUTES
    )


def test_registry_matches_the_expected_names():
    "The other half of the rename check: the registry against the same literals."
    assert {str(span) for span in reg.SPANS} == EXPECTED_SPANS
    for span in reg.SPANS:
        assert {str(attribute) for attribute in span.attributes} == EXPECTED_ATTRIBUTES[
            str(span)
        ], f"{span} attributes have drifted"


@pytest.mark.asyncio
async def test_every_emitted_span_is_registered(emitted):
    "A span added without a registry entry would be missing from the docs."
    unregistered = sorted(
        name for name in emitted["names"] if reg.span_for(name) is None
    )
    assert (
        not unregistered
    ), f"these spans are emitted but not in telemetry_registry.SPANS: {unregistered}"


@pytest.mark.asyncio
async def test_every_emitted_attribute_is_registered(emitted):
    "An attribute added without a registry entry would be missing from the docs."
    unregistered = sorted(
        f"{span_name} -> {key}"
        for span_name, key in emitted["pairs"]
        if not reg.attribute_allowed(reg.span_for(span_name), key)
    )
    assert (
        not unregistered
    ), "these span attributes are emitted but not registered: " + ", ".join(
        unregistered
    )


@pytest.mark.asyncio
async def test_every_registered_span_is_emitted(emitted):
    """
    The direction nothing else catches: the docs must not describe a span that
    no longer exists.
    """
    missing = sorted(
        str(span)
        for span in reg.SPANS
        if not any(reg.span_for(name) is span for name in emitted["names"])
    )
    assert not missing, (
        f"these spans are documented but never emitted by the workload: {missing}. "
        "Either the instrumentation was removed, or exercise() no longer reaches it."
    )


@pytest.mark.asyncio
async def test_every_registered_attribute_is_emitted(emitted):
    """
    Every registered attribute, optional or not, must actually be set at least
    once by the workload.

    `optional` describes whether a reader should expect it on every span, not
    whether the code still sets it - so an attribute deleted from the code but
    left in the docs has to fail here even when it is marked optional. If a
    new attribute only appears in some rare case, extend exercise() to reach
    that case.
    """
    by_span = _keys_by_span(emitted["pairs"])
    missing = []
    for span in reg.SPANS:
        emitted_keys = by_span.get(str(span), set())
        for attribute in span.attributes:
            if attribute not in emitted_keys:
                missing.append(f"{span} -> {attribute}")
    assert not missing, (
        "these attributes are documented but never emitted by the workload: "
        + ", ".join(sorted(missing))
    )


def test_registry_has_no_duplicate_names():
    assert len(set(reg.SPANS)) == len(reg.SPANS)
    for span in reg.SPANS:
        assert len(set(span.attributes)) == len(
            span.attributes
        ), f"{span} lists an attribute twice"


def test_registry_entries_are_documented():
    "Every entry carries a description - the docs are generated from these."
    for span in reg.SPANS:
        assert span.description.strip(), f"{span} has no description"
        for attribute in span.attributes:
            assert attribute.description.strip(), f"{span} -> {attribute} has none"


def test_registry_entries_are_usable_as_plain_strings():
    "The str subclassing is the whole reason call sites need no wrapper API."
    assert isinstance(reg.DB_QUERY, str)
    assert isinstance(reg.DB_NAMESPACE, str)
    assert reg.DB_QUERY == "db.query"
    assert reg.DB_NAMESPACE == "db.namespace"
    assert f"{reg.DB_QUERY}.execute" == "db.query.execute"


def test_span_and_attribute_lookup():
    assert reg.span_for("db.query") is reg.DB_QUERY
    assert reg.span_for("datasette.startup") is reg.STARTUP
    assert reg.span_for("not.a.datasette.span") is None
    assert reg.attribute_allowed(reg.DB_QUERY, "db.namespace")
    assert not reg.attribute_allowed(reg.DB_QUERY, "db.namespace.extra")
    assert not reg.attribute_allowed(reg.DB_QUERY, "datasette.isolated_connection")
    assert not reg.attribute_allowed(None, "db.namespace")
