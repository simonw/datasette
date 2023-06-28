from docutils import nodes
from sphinx.util.docutils import SphinxDirective
from importlib import import_module
import json


class JSONContextDirective(SphinxDirective):
    required_arguments = 1

    def run(self):
        module_path, class_name = self.arguments[0].rsplit(".", 1)
        try:
            module = import_module(module_path)
            dataclass = getattr(module, class_name)
        except ImportError:
            warning = f"Unable to import {self.arguments[0]}"
            return [nodes.error(None, nodes.paragraph(text=warning))]

        doc = json.dumps(
            dataclass.__annotations__, indent=4, sort_keys=True, default=repr
        )
        doc_node = nodes.literal_block(text=doc)

        return [doc_node]


def setup(app):
    app.add_directive("jsoncontext", JSONContextDirective)
