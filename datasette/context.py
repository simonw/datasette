from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field


def doc(documentation):
    return field(metadata={"doc": documentation})


def is_builtin_type(obj):
    return isinstance(
        obj,
        tuple(
            x.__class__
            for x in (int, float, str, bool, bytes, list, tuple, dict, set, frozenset)
        ),
    )


def rst_docs_for_dataclass(klass: Any) -> str:
    """Generate reStructuredText (reST) docs for a dataclass."""
    docs = []

    # Class name and docstring
    docs.append(klass.__name__)
    docs.append("-" * len(klass.__name__))
    docs.append("")
    if klass.__doc__:
        docs.append(klass.__doc__)
        docs.append("")

    # Dataclass fields
    docs.append("Fields")
    docs.append("~~~~~~")
    docs.append("")

    for name, field_info in klass.__dataclass_fields__.items():
        if is_builtin_type(field_info.type):
            # <class 'int'>
            type_name = field_info.type.__name__
        else:
            # List[str]
            type_name = str(field_info.type).replace("typing.", "")
        docs.append(f':{name} - ``{type_name}``: {field_info.metadata.get("doc", "")}')

    return "\n".join(docs)


@dataclass
class ForeignKey:
    incoming: List[Dict]
    outgoing: List[Dict]


@dataclass
class Table:
    "A table is a useful thing"
    name: str = doc("The name of the table")
    columns: List[str] = doc("List of column names in the table")
    primary_keys: List[str] = doc("List of column names that are primary keys")
    count: int = doc("Number of rows in the table")
    hidden: bool = doc(
        "Should this table default to being hidden in the main database UI?"
    )
    fts_table: Optional[str] = doc(
        "If this table has FTS support, the accompanying FTS table name"
    )
    foreign_keys: ForeignKey = doc("List of foreign keys for this table")
    private: bool = doc("Private tables are not visible to signed-out anonymous users")


@dataclass
class View:
    name: str
    private: bool


@dataclass
class Query:
    title: str
    sql: str
    name: str
    private: bool


@dataclass
class Database:
    content: str
    private: bool
    path: str
    size: int
    tables: List[Table]
    hidden_count: int
    views: List[View]
    queries: List[Query]
    allow_execute_sql: bool
    table_columns: Dict[str, List[str]]
    query_ms: float
