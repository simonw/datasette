from datasette import hookimpl
import pint

ureg = pint.UnitRegistry()


@hookimpl
def prepare_jinja2_environment(env):
    env.filters['uppercase'] = lambda u: u.upper()


@hookimpl
def prepare_connection(conn):
    def convert_units(amount, from_, to_):
        "select convert_units(100, 'm', 'ft');"
        return (amount * ureg(from_)).to(to_).to_tuple()[0]
    conn.create_function('convert_units', 3, convert_units)
