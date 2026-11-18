"""
Cog helpers for generating docs/template_context.rst from the Context
dataclasses and TEMPLATE_BASE_CONTEXT - same pattern as json_api_doc.py.
"""

import inspect


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
        cog.out(f"``{name}``\n")
        cog.out(f"    {doc}\n\n")

    for klass in PAGES.values():
        title = klass.page_title()
        # The first paragraph of the docstring introduces the page, any
        # further paragraphs follow the sentence naming its template
        first, *rest = [
            " ".join(paragraph.split())
            for paragraph in inspect.cleandoc(klass.__doc__).split("\n\n")
        ]
        intro = "\n\n".join(
            [f"{first} Rendered using the ``{klass.documented_template}`` template."]
            + rest
        )
        _section(cog, title, intro)
        if klass.extras_scope is not None:
            cog.out(
                "Many of these keys are shared with the :ref:`JSON API "
                "<json_api>` for this page.\n\n"
            )
        for f in sorted(klass.documented_fields(), key=lambda f: f.name):
            cog.out(f"``{f.name}`` - ``{f.type_name}``\n")
            cog.out(f"    {f.help}\n\n")


def _section(cog, title, intro):
    cog.out("{}\n{}\n\n".format(title, "-" * len(title)))
    cog.out(f"{intro}\n\n")
