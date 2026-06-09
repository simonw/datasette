def table_extras(cog):
    from datasette.extras import ExtraScope
    from datasette.views.table_extras import table_extra_registry

    cog.out("\n.. list-table::\n")
    cog.out("   :header-rows: 1\n\n")
    cog.out("   * - Extra\n")
    cog.out("     - Description\n")
    for cls in table_extra_registry.public_classes_for_scope(ExtraScope.TABLE):
        description = cls.description or ""
        notes = []
        if cls.expensive:
            notes.append("May execute additional queries.")
        if cls.docs_note:
            notes.append(cls.docs_note)
        if notes:
            description = "{} ({})".format(description, " ".join(notes)).strip()
        cog.out("   * - ``{}``\n".format(cls.key()))
        cog.out("     - {}\n".format(description))
    cog.out("\n")
