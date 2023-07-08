import json
import textwrap
import yaml


def metadata_example(cog, example):
    cog.out("\n.. tab:: YAML\n\n")
    cog.out("    .. code-block:: yaml\n\n")
    cog.out(textwrap.indent(yaml.safe_dump(example, sort_keys=False), "        "))
    cog.out("\n\n.. tab:: JSON\n\n")
    cog.out("    .. code-block:: json\n\n")
    cog.out(textwrap.indent(json.dumps(example, indent=2), "        "))
    cog.out("\n")
