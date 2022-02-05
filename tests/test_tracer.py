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
    # There should be a mix of different types of SQL statement
    expected = (
        "CREATE TABLE ",
        "PRAGMA ",
        "INSERT OR REPLACE INTO ",
        "INSERT INTO",
        "select ",
    )
    for prefix in expected:
        assert any(
            sql.startswith(prefix) for sql in sqls
        ), "No trace beginning with: {}".format(prefix)

    # Should be at least one executescript
    assert any(trace for trace in traces if trace.get("executescript"))
    # And at least one executemany
    execute_manys = [trace for trace in traces if trace.get("executemany")]
    assert execute_manys
    assert all(isinstance(trace["count"], int) for trace in execute_manys)
