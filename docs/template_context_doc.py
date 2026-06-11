"""
Cog helpers for generating docs/template_context.rst from the manifest
in datasette/template_contexts.py - same pattern as json_api_doc.py.
"""


def template_context(cog):
    from datasette.template_contexts import BASE_CONTEXT_KEYS, PAGES

    cog.out("\n")
    _section(
        cog,
        "Base context",
        (
            "These variables are available on every page rendered by "
            "Datasette, including pages rendered by plugins that use "
            ":ref:`datasette.render_template() <datasette_render_template>`. "
            "Plugins can add additional variables using the "
            ":ref:`plugin_hook_extra_template_vars` hook."
        ),
    )
    _untyped_keys(cog, BASE_CONTEXT_KEYS)

    for page in PAGES.values():
        _section(
            cog,
            "{} page".format(page.title),
            "{} Rendered using the ``{}`` template.".format(
                page.description, page.template
            ),
        )
        if page.context_class is not None:
            for f in sorted(
                page.context_class.documented_fields(), key=lambda f: f.name
            ):
                cog.out("``{}`` - ``{}``\n".format(f.name, f.type_name))
                cog.out("    {}\n\n".format(f.help))
        else:
            cog.out(
                "Many of these keys are shared with the :ref:`JSON API "
                "<json_api>` for this page.\n\n"
            )
            _untyped_keys(cog, page.documented_keys())


def _section(cog, title, intro):
    cog.out("{}\n{}\n\n".format(title, "-" * len(title)))
    cog.out("{}\n\n".format(intro))


def _untyped_keys(cog, keys):
    for key in keys:
        cog.out("``{}``\n".format(key.name))
        cog.out("    {}\n\n".format(key.doc))
