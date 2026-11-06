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
            if _is_geometry(first.get("geometry")):
                return 0.8
        if _is_geometry(doc.get("geometry")):
            # A single Feature with its "type" key last
            return 0.8
        return 0.0

    def load(self, path):
        with open(path, "rb") as fp:
            doc = json.load(fp)
        if doc.get("type") == "Feature" or "features" not in doc:
            features = [doc]
        else:
            features = doc["features"]
        rows = []
        types = {}
        for feature in features:
            row = {}
            if "id" in feature:
                row["id"] = feature["id"]
            geometry = feature.get("geometry")
            row["geometry"] = None if geometry is None else json.dumps(geometry)
            for key, value in (feature.get("properties") or {}).items():
                # A property called "id" or "geometry" must not overwrite the
                # feature's own id or geometry
                if key in row:
                    key = f"properties.{key}"
                row[key] = value
            for key, value in row.items():
                types[key] = _widen(types.get(key), value)
            rows.append(row)
        # GeoJSON values carry their own types, so declare them all
        yield Schema({key: type_ or "text" for key, type_ in types.items()})
        yield from rows


def _is_geometry(value):
    """
    A GeoJSON geometry, judged by its keys as well as its "type": with
    sorted keys "coordinates" comes before "type", and a big first polygon
    can fill the whole window before "type" appears.
    """
    if not isinstance(value, dict) or not value:
        return False
    if value.get("type") in GEOMETRY_TYPES:
        return True
    keys = set(value)
    return bool(keys & {"coordinates", "geometries"}) and keys <= {
        "type",
        "coordinates",
        "geometries",
        "bbox",
    }


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
