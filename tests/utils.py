from datasette.utils.sqlite import sqlite3


def assert_footer_links(soup):
    footer_links = soup.find("footer").find_all("a")
    assert 4 == len(footer_links)
    datasette_link, license_link, source_link, about_link = footer_links
    assert "Datasette" == datasette_link.text.strip()
    assert "tests/fixtures.py" == source_link.text.strip()
    assert "Apache License 2.0" == license_link.text.strip()
    assert "About Datasette" == about_link.text.strip()
    assert "https://datasette.io/" == datasette_link["href"]
    assert (
        "https://github.com/simonw/datasette/blob/main/tests/fixtures.py"
        == source_link["href"]
    )
    assert (
        "https://github.com/simonw/datasette/blob/main/LICENSE" == license_link["href"]
    )
    assert "https://github.com/simonw/datasette" == about_link["href"]


def inner_html(soup):
    html = str(soup)
    # This includes the parent tag - so remove that
    inner_html = html.split(">", 1)[1].rsplit("<", 1)[0]
    return inner_html.strip()


def has_load_extension():
    conn = sqlite3.connect(":memory:")
    return hasattr(conn, "enable_load_extension")
