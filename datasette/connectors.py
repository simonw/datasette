import pkg_resources

db_connectors = {}

def load_connectors():
    for entry_point in pkg_resources.iter_entry_points('datasette.connectors'):
        db_connectors[entry_point.name] = entry_point.load()

def inspect(path):
    for connector in db_connectors.values():
        try:
            return connector.inspect(path)
        except:
            pass
    else:
        raise Exception("No database connector found for %s" % path)

def connect(path, dbtype):
    try:
        return db_connectors[dbtype].Connection(path)
    except:
        raise Exception("No database connector found for %s" % path)
