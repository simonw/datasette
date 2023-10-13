from datasette import hookimpl

# Test command:
# datasette fixtures.db \ --plugins-dir=demos/plugins/
#                       \ --static static:demos/plugins/static

# Create a set with view names that qualify for this JS, since plugins won't do anything on other pages
# Same pattern as in Nteract data explorer
# https://github.com/hydrosquall/datasette-nteract-data-explorer/blob/main/datasette_nteract_data_explorer/__init__.py#L77
PERMITTED_VIEWS = {"table", "query", "database"}


@hookimpl
def extra_js_urls(view_name):
    print(view_name)
    if view_name in PERMITTED_VIEWS:
        return [
            {
                "url": f"/static/table-example-plugins.js",
            }
        ]
