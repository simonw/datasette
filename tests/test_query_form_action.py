import pytest
from bs4 import BeautifulSoup as Soup


@pytest.mark.asyncio
async def test_ad_hoc_query_form_posts_to_query_route(ds_client):
    response = await ds_client.get("/fixtures/-/query?sql=select+1")
    assert response.status_code == 200

    form = Soup(response.text, "html.parser").select_one("form.sql.core")
    assert form is not None
    assert form["action"] == "/fixtures/-/query"
