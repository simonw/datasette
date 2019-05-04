from datasette.sql import Select, And, Or, Table


def test_select():
    s = Select(
        fields="foo",
        from_tables=Table("bar", alias="baz"),
        where=["a = 'b'", "c = 'd'"],
        order_by="foo DESC",
        limit=40,
        offset=10,
    )
    assert (
        s.generate()
        == "SELECT foo FROM bar AS baz WHERE (a = 'b') AND (c = 'd') ORDER BY foo DESC LIMIT 40 OFFSET 10"
    )

    s = Select(from_tables="bar", where=Or(["a = 'b'", "c = 'd'"]), group_by="foo")
    assert str(s) == "SELECT * FROM bar WHERE (a = 'b') OR (c = 'd') GROUP BY foo"


def test_nested_operatorlist():
    assert (
        str(And([Or(["a = b", "c = d"]), "e = f"]))
        == "((a = b) OR (c = d)) AND (e = f)"
    )
