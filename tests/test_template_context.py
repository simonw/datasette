"""
Tests for the documented template context - the contract that custom
template authors can rely on for Datasette 1.0.
"""

from dataclasses import dataclass, field

import pytest

from datasette.views import Context
from datasette.views.database import DatabaseContext, QueryContext


def test_documented_fields():
    @dataclass
    class DemoContext(Context):
        name: str = field(metadata={"help": "The name"})
        count: int = field(metadata={"help": "How many there are"})

    fields = DemoContext.documented_fields()
    assert [(f.name, f.type_name, f.help) for f in fields] == [
        ("name", "str", "The name"),
        ("count", "int", "How many there are"),
    ]


@pytest.mark.parametrize("klass", (DatabaseContext, QueryContext))
def test_context_dataclass_fields_all_have_help(klass):
    for context_field in klass.documented_fields():
        assert context_field.help, "{}.{} is missing help metadata".format(
            klass.__name__, context_field.name
        )
