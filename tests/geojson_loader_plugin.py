"""
An example import loader plugin for GeoJSON, used by tests/test_imports_loaders.py
and prototypes/data_csv/sniff_experiment.py.

It is not in tests/plugins/ because everything there is loaded into the
ds_client fixtures, whose plugin lists other tests check exactly. Register it
with ``pm.register(sys.modules[__name__], name=...)`` instead.
"""

import json
import re

from datasette import hookimpl
from datasette.imports.loaders import Loader, Schema, peek_json

GEOMETRY_TYPES = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
    "GeometryCollection",
}


class GeoJSONLoader(Loader):
    """
    Reads a GeoJSON FeatureCollection (or a single Feature): one row per
    feature, with its properties as columns and its geometry as GeoJSON text.
    """

    name = "geojson"
    description = "GeoJSON FeatureCollection"

    def sniff(self, first_bytes):
        # Look at the structure, not for the string "FeatureCollection":
        # the "type" key can come after a "features" array of any length
        peek = peek_json(first_bytes)
        if peek is None or peek.trailing or not isinstance(peek.value, dict):
            return 0.0
        doc = peek.value
        if doc.get("type") == "FeatureCollection":
            return 0.95
        if doc.get("type") == "Feature":
            return 0.9
        features = doc.get("features")
        if isinstance(features, list) and features and isinstance(features[0], dict):
            first = features[0]
            if first.get("type") == "Feature":
                return 0.9
            geometry = first.get("geometry")
            if isinstance(geometry, dict) and geometry.get("type") in GEOMETRY_TYPES:
                return 0.8
        return 0.0

    def load(self, path):
        with open(path, "rb") as fp:
            doc = json.load(fp)
        features = [doc] if doc.get("type") == "Feature" else doc["features"]
        rows = []
        types = {}
        for feature in features:
            row = {}
            if "id" in feature:
                row["id"] = feature["id"]
            row.update(feature.get("properties") or {})
            geometry = feature.get("geometry")
            row["geometry"] = None if geometry is None else json.dumps(geometry)
            for key, value in row.items():
                types[key] = _widen(types.get(key), value)
            rows.append(row)
        # GeoJSON values carry their own types, so declare them all
        yield Schema({key: type_ or "text" for key, type_ in types.items()})
        yield from rows


def _widen(current, value):
    if value is None:
        return current
    if isinstance(value, int):  # Includes bool
        new = "integer"
    elif isinstance(value, float):
        new = "float"
    else:
        new = "text"
    order = [None, "integer", "float", "text"]
    return max(current, new, key=order.index)


class NaiveGeoJSONLoader(GeoJSONLoader):
    """Same loader, but sniff() searches for "type": "FeatureCollection"."""

    name = "geojson-naive"
    _type_re = re.compile(rb'"type"\s*:\s*"FeatureCollection"')

    def sniff(self, first_bytes):
        return 0.95 if self._type_re.search(first_bytes) else 0.0


@hookimpl
def register_import_loaders():
    return [GeoJSONLoader]
