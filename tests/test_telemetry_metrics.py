"""
Tests for the OpenTelemetry metrics emitted by Datasette. Gauge callbacks are
called directly, since the pool gauges have no attributes to tell instances apart.
"""

import asyncio
import threading
import weakref

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
    # Other Datasette instances may also be reporting:
    assert 7 in values


@pytest.mark.asyncio
async def test_no_thread_gauges_in_non_threaded_mode():
    "Pool gauges skip instances with num_sql_threads=0, which have no pool."
    ds = Datasette(memory=True, settings={"num_sql_threads": 0})
    try:
        assert ds.executor is None
        # Pool gauges have no attributes, so observe only this instance:
        original = telemetry._live_datasettes
        telemetry._live_datasettes = weakref.WeakSet([ds])
        try:
            assert list(telemetry.observe_sql_thread_limit()) == []
            assert list(telemetry.observe_sql_thread_queue_depth()) == []
        finally:
            telemetry._live_datasettes = original
        # Per-database gauges do not depend on the pool:
        assert observations(telemetry.observe_pending_queries, ds)
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_thread_queue_depth_gauge_reports_saturation():
    """
    Queue depth is above zero when reads queue behind num_sql_threads. Also
    fails if the private ThreadPoolExecutor._work_queue attribute goes away.
    """
    ds = Datasette(memory=True, settings={"num_sql_threads": 1})
    db = ds.add_memory_database("metrics_saturation_db")
    entered = threading.Event()
    release = threading.Event()

    def blocker(conn):
        entered.set()
        assert release.wait(timeout=10)
        return 1

    try:
        first = asyncio.ensure_future(db.execute_fn(blocker))
        # Wait until the blocker is using the only thread:
        await asyncio.get_running_loop().run_in_executor(None, entered.wait, 10)
        second = asyncio.ensure_future(db.execute_fn(lambda conn: 2))
        # The second query is queued on a later event loop turn, so poll:
        depths = []
        for _ in range(500):
            depths = [
                value
                for _, value in observations(telemetry.observe_sql_thread_queue_depth)
            ]
            if any(value >= 1 for value in depths):
                break
            await asyncio.sleep(0.01)
        assert any(value >= 1 for value in depths), depths
        release.set()
        assert await first == 1
        assert await second == 2
    finally:
        release.set()
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

    # Hold the worker thread until release is set:
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

    # No observation until the write queue has been created:
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
    assert open_connections() >= 1, "executing a query opens a tracked connection"


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
async def test_operation_duration_records_write_error_type(otel_metrics):
    "A failed write is still timed and records error.type."
    ds = Datasette(memory=True)
    ds.add_memory_database("duration_write_error_db")
    try:
        db = ds.get_database("duration_write_error_db")
        with pytest.raises(sqlite3.OperationalError):
            await db.execute_write("insert into nope values (1)")
        otel_metrics.collect()
        point = otel_metrics.point(
            "db.client.operation.duration",
            {"db.namespace": "duration_write_error_db", "datasette.operation": "write"},
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
    "Queries cancelled by sql_time_limit_ms are counted."
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
    "Gauge callbacks reach the metric reader as data points."
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
    Registering an instance does not keep it alive. Uses a stand-in object
    because an atexit handler in Database.__init__ keeps a real Datasette alive.
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


HISTOGRAM_PROBES = [
    # (instrument attribute on telemetry, metric name, isolating attributes)
    (
        "sql_operation_duration",
        "db.client.operation.duration",
        {"db.namespace": "bucket_probe_operation"},
    ),
    (
        "write_queue_wait",
        "datasette.write.queue_wait",
        {"db.namespace": "bucket_probe_queue_wait"},
    ),
]

# One value in each of six registry buckets. The SDK's default boundaries
# would put the first five in the same bucket.
SPREAD = [0.00005, 0.0003, 0.002, 0.03, 0.8, 7.0]


@pytest.mark.parametrize(
    "instrument_name,metric_name,attributes",
    HISTOGRAM_PROBES,
    ids=[metric for _, metric, _ in HISTOGRAM_PROBES],
)
def test_histograms_spread_values_across_buckets(
    otel_metrics, instrument_name, metric_name, attributes
):
    """
    The registry's bucket boundaries reach the SDK. Values are recorded
    directly because real test query durations would all share one bucket.
    """
    from datasette.telemetry_registry import METRICS

    metric = next(m for m in METRICS if m == metric_name)
    instrument = getattr(telemetry, instrument_name)
    for value in SPREAD:
        instrument.record(value, attributes)

    otel_metrics.collect()
    point = otel_metrics.point(metric_name, attributes)

    assert (
        tuple(point.explicit_bounds) == metric.buckets
    ), "the registry's boundaries did not reach the SDK"
    assert point.count == len(SPREAD)
    occupied = [count for count in point.bucket_counts if count]
    assert len(occupied) == len(SPREAD), (
        f"expected each of {SPREAD} in its own bucket, got bucket counts "
        f"{list(point.bucket_counts)} for bounds {list(point.explicit_bounds)}"
    )


@pytest.mark.asyncio
async def test_operation_duration_histogram_records_execute_fn(otel_metrics):
    "execute_fn() reads are recorded in the same histogram as SQL reads."
    ds = Datasette(memory=True)
    ds.add_memory_database("duration_fn_db")
    try:
        db = ds.get_database("duration_fn_db")

        def read_one(conn):
            return conn.execute("select 1").fetchone()[0]

        assert await db.execute_fn(read_one) == 1
        otel_metrics.collect()
        point = otel_metrics.point(
            "db.client.operation.duration",
            {"db.namespace": "duration_fn_db", "datasette.operation": "read"},
        )
        assert point.count == 1
        assert point.sum > 0
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_operation_duration_histogram_records_execute_write_fn(otel_metrics):
    "execute_write_fn() writes are recorded in the same histogram."
    ds = Datasette(memory=True)
    ds.add_memory_database("duration_write_fn_db")
    try:
        db = ds.get_database("duration_write_fn_db")

        def create_table(conn):
            conn.execute("create table t (id integer primary key)")

        await db.execute_write_fn(create_table)
        otel_metrics.collect()
        point = otel_metrics.point(
            "db.client.operation.duration",
            {"db.namespace": "duration_write_fn_db", "datasette.operation": "write"},
        )
        assert point.count == 1
        assert point.sum > 0
    finally:
        ds.close()


@pytest.mark.asyncio
async def test_operation_duration_records_callback_error_type(otel_metrics):
    "A callback that raises is still timed, with error.type from the exception."
    ds = Datasette(memory=True)
    ds.add_memory_database("duration_fn_error_db")
    try:
        db = ds.get_database("duration_fn_error_db")

        def boom(conn):
            raise ValueError("callback failed")

        with pytest.raises(ValueError):
            await db.execute_fn(boom)
        otel_metrics.collect()
        point = otel_metrics.point(
            "db.client.operation.duration",
            {"db.namespace": "duration_fn_error_db", "datasette.operation": "read"},
        )
        assert point.count == 1
        assert dict(point.attributes)["error.type"] == "ValueError"
    finally:
        ds.close()
