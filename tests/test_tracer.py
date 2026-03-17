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
