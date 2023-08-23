import json
import textwrap

from ruamel.yaml import round_trip_load
from yaml import safe_dump


def metadata_example(cog, data=None, yaml=None):
    assert data or yaml, "Must provide data= or yaml="
    assert not (data and yaml), "Cannot use data= and yaml="
    output_yaml = None
    if yaml:
        # dedent it first
        yaml = textwrap.dedent(yaml).strip()
        # round_trip_load to preserve key order:
        data = round_trip_load(yaml)
        output_yaml = yaml
    else:
        output_yaml = safe_dump(data, sort_keys=False)
    cog.out("\n.. tab:: YAML\n\n")
    cog.out("    .. code-block:: yaml\n\n")
    cog.out(textwrap.indent(output_yaml, "        "))
    cog.out("\n\n.. tab:: JSON\n\n")
    cog.out("    .. code-block:: json\n\n")
    cog.out(textwrap.indent(json.dumps(data, indent=2), "        "))
    cog.out("\n")
