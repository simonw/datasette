"""
Cog helpers for generating docs/template_context.rst from the Context
dataclasses and TEMPLATE_BASE_CONTEXT - same pattern as json_api_doc.py.
"""


def template_context(cog):
    from datasette.app import TEMPLATE_BASE_CONTEXT
    from datasette.template_contexts import PAGES

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
    for name, doc in TEMPLATE_BASE_CONTEXT.items():
        cog.out("``{}``\n".format(name))
        cog.out("    {}\n\n".format(doc))

    for klass in PAGES.values():
        title = "{} page".format(klass.__name__.removesuffix("Context"))
        intro = "{} Rendered using the ``{}`` template.".format(
            klass.__doc__, klass.documented_template
        )
        _section(cog, title, intro)
        if klass.extras_scope is not None:
            cog.out(
                "Many of these keys are shared with the :ref:`JSON API "
                "<json_api>` for this page.\n\n"
            )
        for f in sorted(klass.documented_fields(), key=lambda f: f.name):
            cog.out("``{}`` - ``{}``\n".format(f.name, f.type_name))
            cog.out("    {}\n\n".format(f.help))


def _section(cog, title, intro):
    cog.out("{}\n{}\n\n".format(title, "-" * len(title)))
    cog.out("{}\n\n".format(intro))
