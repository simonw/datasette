import pytest

from .fixtures import make_app_client


@pytest.mark.parametrize("trace_debug", (True, False))
def test_trace(trace_debug):
    with make_app_client(settings={"trace_debug": trace_debug}) as client:
        response = client.get("/fixtures/simple_primary_key.json?_trace=1")
        assert response.status == 200

    data = response.json
    if not trace_debug:
        assert "_trace" not in data
        return

    assert "_trace" in data
    trace_info = data["_trace"]
    assert isinstance(trace_info["request_duration_ms"], float)
    assert isinstance(trace_info["sum_trace_duration_ms"], float)
    assert isinstance(trace_info["num_traces"], int)
    assert isinstance(trace_info["traces"], list)
    traces = trace_info["traces"]
    assert len(traces) == trace_info["num_traces"]
    for trace in traces:
        assert isinstance(trace["type"], str)
        assert isinstance(trace["start"], float)
        assert isinstance(trace["end"], float)
        assert trace["duration_ms"] == (trace["end"] - trace["start"]) * 1000
        assert isinstance(trace["traceback"], list)
        assert isinstance(trace["database"], str)
        assert isinstance(trace["sql"], str)
        assert isinstance(trace.get("params"), (list, dict, None.__class__))

    sqls = [trace["sql"] for trace in traces if "sql" in trace]
    # There should be SQL statements from request handling in the trace.
    # Note: CREATE TABLE, INSERT OR REPLACE, executescript, and executemany
    # are not expected here because internal tables are now created and
    # populated during invoke_startup(), before the request is traced.
    assert any(sql.startswith("select ") for sql in sqls), "No select statements traced"


def test_trace_silently_fails_for_large_page():
    # Max HTML size is 256KB
    with make_app_client(settings={"trace_debug": True}) as client:
        # Small response should have trace
        small_response = client.get("/fixtures/simple_primary_key.json?_trace=1")
        assert small_response.status == 200
        assert "_trace" in small_response.json

        # Big response should not
        big_response = client.get(
            "/fixtures/-/query.json",
            params={"_trace": 1, "sql": "select zeroblob(1024 * 256)"},
        )
        assert big_response.status == 200
        assert "_trace" not in big_response.json


def test_trace_query_errors():
    with make_app_client(settings={"trace_debug": True}) as client:
        response = client.get(
            "/fixtures/-/query.json",
            params={"_trace": 1, "sql": "select * from non_existent_table"},
        )
        assert response.status == 400

    data = response.json
    assert "_trace" in data
    trace_info = data["_trace"]
    assert trace_info["traces"][-1]["error"] == "no such table: non_existent_table"


@pytest.mark.asyncio
async def test_trace_child_tasks_resets_contextvar_on_exception():
    from datasette import tracer

    before = tracer.trace_task_id.get()
    with pytest.raises(ValueError), tracer.trace_child_tasks():
        assert tracer.trace_task_id.get() is not None
        raise ValueError("simulated error")
    # The contextvar must be reset even though the block raised
    assert tracer.trace_task_id.get() == before


def test_trace_parallel_queries():
    with make_app_client(settings={"trace_debug": True}) as client:
        response = client.get("/parallel-queries?_trace=1")
        assert response.status == 200

    data = response.json
    assert data["one"] == 1
    assert data["two"] == 2
    trace_info = data["_trace"]
    traces = [trace for trace in trace_info["traces"] if "sql" in trace]
    one, two = traces
    # "two" should have started before "one" ended
    assert two["start"] < one["end"]


def test_trace_includes_execute_fn_queries():
    # https://github.com/simonw/datasette/issues/1722
    with make_app_client(settings={"trace_debug": True}) as client:
        response = client.get("/fixtures/simple_primary_key.json?_trace=1")
        assert response.status == 200

    traces = response.json["_trace"]["traces"]
    fn_traces = [trace for trace in traces if "fn" in trace]
    fns = {trace["fn"] for trace in fn_traces}
    assert "Database.primary_keys.<locals>._primary_keys" in fns
    assert "Database.table_columns.<locals>._table_columns" in fns
    for trace in fn_traces:
        assert trace["type"] == "sql"
        assert trace["database"] == "fixtures"
    primary_keys_trace = next(
        trace
        for trace in fn_traces
        if trace["fn"] == "Database.primary_keys.<locals>._primary_keys"
    )
    assert "PRAGMA table_xinfo(simple_primary_key)" in primary_keys_trace["sql"]


@pytest.mark.asyncio
async def test_execute_fn_without_tracing_leaves_trace_callback_alone(ds_client):
    db = ds_client.ds.get_database("fixtures")

    def run_with_own_trace_callback(conn):
        # Without ?_trace=1 execute_fn should not touch the trace callback
        statements = []
        conn.set_trace_callback(statements.append)
        conn.execute("select 1").fetchall()
        conn.set_trace_callback(None)
        return statements

    assert await db.execute_fn(run_with_own_trace_callback) == ["select 1"]


@pytest.mark.asyncio
async def test_traced_execute_fn_preserves_existing_trace_callback():
    # A trace callback a plugin leaves installed on a pooled connection
    # should still fire during and after a traced execute_fn() call
    from datasette.app import Datasette
    from datasette.tracer import capture_traces

    # num_sql_threads=0 means every call uses the same connection
    ds = Datasette(settings={"num_sql_threads": 0})
    db = ds.add_memory_database("trace_callback_chain")
    plugin_statements = []

    def install_plugin_callback(conn):
        conn.set_trace_callback(plugin_statements.append)

    await db.execute_fn(install_plugin_callback)

    traces = []
    with capture_traces(traces):
        await db.execute_fn(lambda conn: conn.execute("select 1").fetchall())
    # Traced, and the plugin callback saw the statement too
    assert [t["sql"] for t in traces if "fn" in t] == ["select 1"]
    assert plugin_statements == ["select 1"]

    # The plugin callback is still installed after tracing finished
    await db.execute_fn(lambda conn: conn.execute("select 2").fetchall())
    assert plugin_statements == ["select 1", "select 2"]


@pytest.mark.asyncio
async def test_traced_execute_fn_keeps_callback_installed_by_fn():
    from datasette.app import Datasette
    from datasette.tracer import capture_traces

    ds = Datasette(settings={"num_sql_threads": 0})
    db = ds.add_memory_database("trace_callback_install")
    statements = []

    traces = []
    with capture_traces(traces):
        await db.execute_fn(lambda conn: conn.set_trace_callback(statements.append))
    # A callback installed by fn() during a traced call is left in place
    await db.execute_fn(lambda conn: conn.execute("select 3").fetchall())
    assert statements == ["select 3"]
