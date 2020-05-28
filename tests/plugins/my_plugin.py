from datasette import hookimpl
import base64
import pint
import json

ureg = pint.UnitRegistry()


@hookimpl
def prepare_connection(conn, database, datasette):
    def convert_units(amount, from_, to_):
        "select convert_units(100, 'm', 'ft');"
        return (amount * ureg(from_)).to(to_).to_tuple()[0]

    conn.create_function("convert_units", 3, convert_units)

    def prepare_connection_args():
        return 'database={}, datasette.plugin_config("name-of-plugin")={}'.format(
            database, datasette.plugin_config("name-of-plugin")
        )

    conn.create_function("prepare_connection_args", 0, prepare_connection_args)


@hookimpl
def extra_css_urls(template, database, table, datasette):
    return [
        "https://plugin-example.com/{}/extra-css-urls-demo.css".format(
            base64.b64encode(
                json.dumps(
                    {"template": template, "database": database, "table": table,}
                ).encode("utf8")
            ).decode("utf8")
        )
    ]


@hookimpl
def extra_js_urls():
    return [
        {"url": "https://plugin-example.com/jquery.js", "sri": "SRIHASH",},
        "https://plugin-example.com/plugin1.js",
    ]


@hookimpl
def extra_body_script(template, database, table, datasette):
    return "var extra_body_script = {};".format(
        json.dumps(
            {
                "template": template,
                "database": database,
                "table": table,
                "config": datasette.plugin_config(
                    "name-of-plugin", database=database, table=table,
                ),
            }
        )
    )


@hookimpl
def render_cell(value, column, table, database, datasette):
    # Render some debug output in cell with value RENDER_CELL_DEMO
    if value != "RENDER_CELL_DEMO":
        return None
    return json.dumps(
        {
            "column": column,
            "table": table,
            "database": database,
            "config": datasette.plugin_config(
                "name-of-plugin", database=database, table=table,
            ),
        }
    )


@hookimpl
def extra_template_vars(template, database, table, view_name, request, datasette):
    return {
        "extra_template_vars": json.dumps(
            {
                "template": template,
                "scope_path": request.scope["path"] if request else None,
            },
            default=lambda b: b.decode("utf8"),
        )
    }
