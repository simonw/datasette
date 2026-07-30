"""
Tests for the OpenTelemetry metrics Datasette core emits.

Two layers are tested separately and deliberately:

- The gauge callbacks are plain generator functions, so they are called
  directly for exact-value assertions. Going through the SDK for those would
  be unreliable: the pool gauges carry no attribute identifying which
  Datasette produced them, and a pytest session has many live instances, so
  the SDK's last-value aggregation would report whichever one happened to be
  observed last.

- The SDK pipeline (instrument -> reader -> data points) is tested through
  the `otel_metrics` fixture, using metrics that carry `db.namespace` - a
  uniquely named in-memory database is enough to isolate those from every
  other instance alive in the session.
"""

import asyncio

import pytest

from datasette import telemetry
from datasette.app import Datasette
from datasette.database import Database
from datasette.utils.sqlite import sqlite3

pytestmark = pytest.mark.filterwarnings("ignore::ResourceWarning")


def observations(callback, datasette=None):
    """
    Run a gauge callback, optionally keeping only observations produced by one
    Datasette's databases. Returns a list of (attributes dict, value).
    """
    results = []
    names = None
    if datasette is not None:
        names = {db.name for db in telemetry._databases_of(datasette)}
    for observation in callback():
        attributes = dict(observation.attributes or {})
        namespace = attributes.get("db.namespace")
        if names is not None and namespace is not None and namespace not in names:
            continue
        results.append((attributes, observation.value))
    return results


@pytest.fixture
def metrics_ds():
    "A Datasette with a distinctive thread count and a uniquely named database."
    ds = Datasette(
        memory=True,
        settings={"num_sql_threads": 7},
    )
    ds.add_memory_database("metrics_test_db")
    try:
        yield ds
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_sql_thread_limit_gauge_reports_num_sql_threads(metrics_ds):
    values = [value for _, value in observations(telemetry.observe_sql_thread_limit)]
    # Other instances are alive in this session, so assert membership rather
    # than uniqueness - 7 is distinctive enough to only come from metrics_ds.
    assert 7 in values


@pytest.mark.asyncio
async def test_no_thread_gauges_in_non_threaded_mode():
    """
    num_sql_threads=0 means there is no pool at all, so the pool gauges must
    skip the instance rather than report a bogus limit of 0.

    The pool gauges carry no attributes, so the assertion is that adding this
    instance produces no *additional* observations.
    """
    before_limits = len(list(telemetry.observe_sql_thread_limit()))
    before_depths = len(list(telemetry.observe_sql_thread_queue_depth()))
    ds = Datasette(memory=True, settings={"num_sql_threads": 0})
    try:
        assert ds.executor is None
        assert len(list(telemetry.observe_sql_thread_limit())) == before_limits
        assert len(list(telemetry.observe_sql_thread_queue_depth())) == before_depths
        # Per-database gauges are unaffected - they do not depend on the pool.
        assert observations(telemetry.observe_pending_queries, ds)
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_pending_queries_gauge_tracks_in_flight_queries(metrics_ds):
    db = metrics_ds.get_database("metrics_test_db")
    attributes = {"db.namespace": "metrics_test_db"}

    def value():
        points = [
            v
            for a, v in observations(telemetry.observe_pending_queries, metrics_ds)
            if a == attributes
        ]
        assert len(points) == 1
        return points[0]

    assert value() == 0

    # sqlite3.sleep is not a thing, so block the worker thread on an event we
    # control from the event loop and sample the gauge while it is held.
    release = asyncio.Event()
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()

    def blocking_fn(conn):
        loop.call_soon_threadsafe(entered.set)
        asyncio.run_coroutine_threadsafe(release.wait(), loop).result()
        return "done"

    task = asyncio.ensure_future(db.execute_fn(blocking_fn))
    await entered.wait()
    assert value() == 1, "a query occupying a pool thread must be counted as pending"
    release.set()
    assert await task == "done"
    assert value() == 0, "the count must drop once the query completes"


@pytest.mark.asyncio
async def test_write_queue_depth_gauge(metrics_ds):
    db = metrics_ds.get_database("metrics_test_db")
    attributes = {"db.namespace": "metrics_test_db"}

    def depths():
        return [
            v
            for a, v in observations(telemetry.observe_write_queue_depth, metrics_ds)
            if a == attributes
        ]

    # No write has ever been queued, so there is no queue and no observation -
    # rather than a fabricated zero for a queue that does not exist.
    assert depths() == []

    await db.execute_write("create table t (id integer primary key)")
    assert depths() == [0], "an idle write queue reports zero, not nothing"


@pytest.mark.asyncio
async def test_open_connections_gauge(metrics_ds, tmp_path):
    path = str(tmp_path / "conns.db")
    sqlite3.connect(path).execute("create table t (id integer primary key)")
    db = metrics_ds.add_database(Database(metrics_ds, path=path), name="conns_db")
    attributes = {"db.namespace": "conns_db"}

    def open_connections():
        points = [
            v
            for a, v in observations(telemetry.observe_open_connections, metrics_ds)
            if a == attributes
        ]
        assert len(points) == 1
        return points[0]

    assert open_connections() == 0
    await db.execute("select 1")
    assert open_connections() >= 1, "executing a query opens a tracked file connection"


@pytest.mark.asyncio
async def test_operation_duration_histogram_read(otel_metrics):
    ds = Datasette(memory=True)
    ds.add_memory_database("duration_read_db")
    try:
        db = ds.get_database("duration_read_db")
        await db.execute("select 1")
        otel_metrics.collect()
        point = otel_metrics.point(
            "db.client.operation.duration",
            {"db.namespace": "duration_read_db", "datasette.operation": "read"},
        )
        assert point.count == 1
        assert point.sum > 0
        assert dict(point.attributes)["db.system"] == "sqlite"
        assert "error.type" not in dict(point.attributes)
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_operation_duration_histogram_write(otel_metrics):
    ds = Datasette(memory=True)
    ds.add_memory_database("duration_write_db")
    try:
        db = ds.get_database("duration_write_db")
        await db.execute_write("create table t (id integer primary key)")
        otel_metrics.collect()
        point = otel_metrics.point(
            "db.client.operation.duration",
            {"db.namespace": "duration_write_db", "datasette.operation": "write"},
        )
        assert point.count == 1
        assert point.sum > 0
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_operation_duration_records_error_type(otel_metrics):
    "A failed query is still timed, and is separable from a successful one."
    ds = Datasette(memory=True)
    ds.add_memory_database("duration_error_db")
    try:
        db = ds.get_database("duration_error_db")
        with pytest.raises(sqlite3.OperationalError):
            await db.execute("select * from nope")
        otel_metrics.collect()
        point = otel_metrics.point(
            "db.client.operation.duration",
            {"db.namespace": "duration_error_db", "datasette.operation": "read"},
        )
        assert point.count == 1
        assert dict(point.attributes)["error.type"] == "OperationalError"
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_write_queue_wait_histogram(otel_metrics):
    ds = Datasette(memory=True)
    ds.add_memory_database("queue_wait_db")
    try:
        db = ds.get_database("queue_wait_db")
        await db.execute_write("create table t (id integer primary key)")
        await db.execute_write("insert into t (id) values (1)")
        otel_metrics.collect()
        point = otel_metrics.point(
            "datasette.write.queue_wait", {"db.namespace": "queue_wait_db"}
        )
        assert point.count == 2, "one measurement per write dequeued"
        assert point.sum >= 0
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_interrupted_queries_counter(otel_metrics):
    "The count of time-limit kills, which sampled traces cannot provide."
    ds = Datasette(memory=True, settings={"sql_time_limit_ms": 1})
    ds.add_memory_database("interrupted_db")
    try:
        db = ds.get_database("interrupted_db")
        from datasette.database import QueryInterrupted

        with pytest.raises(QueryInterrupted):
            await db.execute("""
                with recursive counter(x) as (
                    select 0 union all select x + 1 from counter
                )
                select * from counter
                """)
        otel_metrics.collect()
        point = otel_metrics.point(
            "datasette.sql.queries.interrupted", {"db.namespace": "interrupted_db"}
        )
        assert point.value == 1
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_metrics_are_reported_through_the_sdk_for_gauges(otel_metrics):
    "End-to-end: a gauge callback reaches the reader as a data point."
    ds = Datasette(memory=True)
    ds.add_memory_database("gauge_pipeline_db")
    try:
        await ds.get_database("gauge_pipeline_db").execute("select 1")
        otel_metrics.collect()
        point = otel_metrics.point(
            "datasette.sql.queries.pending", {"db.namespace": "gauge_pipeline_db"}
        )
        assert point.value == 0
        assert otel_metrics.points("datasette.sql.threads.limit")
    finally:
        ds.close()


def test_closed_datasette_stops_being_observed():
    ds = Datasette(memory=True)
    ds.add_memory_database("closed_db")
    assert observations(telemetry.observe_pending_queries, ds)
    ds.close()
    names = [
        attributes.get("db.namespace")
        for attributes, _ in observations(telemetry.observe_pending_queries)
    ]
    assert "closed_db" not in names


def test_registry_holds_instances_weakly():
    """
    Registering an instance must never be the thing that keeps it alive.

    A stand-in object is used rather than a real Datasette because a Datasette
    with a temp-disk internal database is pinned for the life of the process
    by `Database.__init__`'s `atexit.register(self._cleanup_temp_file)`, which
    holds the Database, which holds the Datasette. That is pre-existing and
    unrelated to telemetry; what is tested here is that this registry adds no
    reference of its own.
    """
    import gc
    import weakref

    class FakeDatasette:
        pass

    fake = FakeDatasette()
    telemetry.register_datasette(fake)
    assert fake in telemetry._live_instances()
    ref = weakref.ref(fake)
    del fake
    gc.collect()
    assert ref() is None
    assert not any(isinstance(ds, FakeDatasette) for ds in telemetry._live_instances())
