import pkg_resources

db_connectors = {}

def connector_method(func):
    print(func.__name__)
    return func

def load_connectors():
    for entry_point in pkg_resources.iter_entry_points('datasette.connectors'):
        db_connectors[entry_point.name] = entry_point.load()

def inspect(path):
    for connector in db_connectors.values():
        try:
            conn = connector(path)
            return conn.inspect()
        except:
            pass
    else:
        raise Exception("No database connector found for %s" % path)
