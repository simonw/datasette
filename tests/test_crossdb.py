import urllib
from .fixtures import app_client_two_attached_databases_crossdb_enabled


def test_crossdb_join(app_client_two_attached_databases_crossdb_enabled):
    app_client = app_client_two_attached_databases_crossdb_enabled
    sql = """
    select
      'extra database' as db,
      pk,
      text1,
      text2
    from
      [extra database].searchable
    union all
    select
      'fixtures' as db,
      pk,
      text1,
      text2
    from
      fixtures.searchable
    """
    response = app_client.get(
        "/_memory.json?" + urllib.parse.urlencode({"sql": sql, "_shape": "array"})
    )
    assert response.status == 200
    assert response.json == [
        {"db": "extra database", "pk": 1, "text1": "barry cat", "text2": "terry dog"},
        {"db": "extra database", "pk": 2, "text1": "terry dog", "text2": "sara weasel"},
        {"db": "fixtures", "pk": 1, "text1": "barry cat", "text2": "terry dog"},
        {"db": "fixtures", "pk": 2, "text1": "terry dog", "text2": "sara weasel"},
    ]
