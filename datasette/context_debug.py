"""
Builds the ?_context=1 template debug page, which lists every variable
that was passed to a template along with its documentation and value.

The documentation comes from the same places as docs/template_context.rst:
the Context dataclass for the page and TEMPLATE_BASE_CONTEXT in app.py.
"""

import dataclasses
import inspect
import json
import re
import sqlite3

from markupsafe import Markup, escape

from datasette.rows import Row

# Values that fit in this many characters are shown on a single line
COMPACT_LENGTH = 200
# Values longer than this many lines start out collapsed
COLLAPSE_LINES = 20
# Values longer than this many characters are cut short on the page
MAX_VALUE_LENGTH = 50_000
# Objects with more public members than this do not have them all listed
MAX_MEMBERS = 60

_inline_rst_re = re.compile(
    # Matched against escaped text, hence &lt; and &gt;
    r":ref:`(?P<label>[^`]+?)\s*&lt;(?P<target>[^`]+?)&gt;`"
    r"|:ref:`(?P<bare>[^`]+)`"
    r"|``(?P<code>[^`]+)``"
)


def rst_to_html(text):
    "Convert the inline reStructuredText used in context documentation to HTML"

    def replace(match):
        if match.group("code"):
            return "<code>{}</code>".format(match.group("code"))
        if match.group("label"):
            return match.group("label")
        bare = match.group("bare")
        if bare.startswith("plugin_hook_"):
            return "<code>{}()</code> plugin hook".format(
                bare.removeprefix("plugin_hook_")
            )
        return "{} documentation".format(bare.replace("_", " "))

    # Escape first - the markup characters matched above are not affected
    return Markup(_inline_rst_re.sub(replace, str(escape(text or ""))))


def _json_default(value):
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
    if isinstance(value, (Row, sqlite3.Row)):
        # Iterating over a Row gives its values, not its keys
        return {key: value[key] for key in value.keys()}  # noqa: SIM118
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    if type(value).__repr__ is object.__repr__:
        # "<Row object at 0x...>" says nothing useful, so show the public
        # attributes of simple objects that only hold data
        attributes = {
            key: attribute
            for key, attribute in getattr(value, "__dict__", {}).items()
            if not key.startswith("_")
        }
        if attributes and all(_is_data(v) for v in attributes.values()):
            return attributes
    return repr(value)


def format_value(value):
    "Pretty-printed representation of a context value"
    try:
        compact = json.dumps(value, default=_json_default)
        if len(compact) <= COMPACT_LENGTH:
            return compact
        return json.dumps(value, default=_json_default, indent=2)
    except (TypeError, ValueError):
        # For example a dictionary with keys that are not strings
        return repr(value)


def context_as_json(template_context):
    "The ?_context_json=1 view: the whole context as indented JSON"

    def dump(value):
        return json.dumps(value, default=_json_default, indent=4)

    try:
        return dump(template_context)
    except (TypeError, ValueError):
        # One of the values cannot be serialized, for example a dictionary
        # with tuples for keys. Fall back to the repr() of just those values
        safe = {}
        for key, value in template_context.items():
            try:
                dump(value)
                safe[key] = value
            except (TypeError, ValueError):
                safe[key] = repr(value)
        return dump(safe)


def _signature(obj):
    try:
        return str(inspect.signature(obj))
    except (TypeError, ValueError):
        return "(...)"


def _docstring(obj):
    # A lambda or closure has no docstring of its own, and getdoc() on an
    # instance would return the docstring of its class
    if isinstance(obj, (str, bytes, int, float, bool, type(None))):
        return ""
    doc = getattr(obj, "__doc__", None)
    return rst_to_html(inspect.cleandoc(doc)) if isinstance(doc, str) else ""


def _is_data(value):
    "Is this a value we display as JSON, rather than as an object with members?"
    if value is None or isinstance(
        value,
        (
            str,
            bytes,
            int,
            float,
            bool,
            dict,
            list,
            tuple,
            set,
            frozenset,
            Row,
            sqlite3.Row,
        ),
    ):
        return True
    return dataclasses.is_dataclass(value) and not isinstance(value, type)


def _members(value):
    "Public methods and properties of an object such as urls or request"
    klass = type(value)
    members = []
    for name in sorted(dir(klass)):
        if name.startswith("_"):
            continue
        attribute = inspect.getattr_static(klass, name)
        if isinstance(attribute, property):
            members.append(
                {
                    "name": name,
                    "signature": "",
                    "is_async": False,
                    "doc": _docstring(attribute),
                }
            )
            continue
        function = (
            attribute.__func__
            if isinstance(attribute, (staticmethod, classmethod))
            else attribute
        )
        if not inspect.isfunction(function):
            continue
        # Signature of the bound method, so "self" is left out
        members.append(
            {
                "name": name,
                "signature": _signature(getattr(value, name)),
                "is_async": inspect.iscoroutinefunction(function),
                "doc": _docstring(function),
            }
        )
    return members


def describe_variable(name, value, *, help="", type_name=None):
    "Everything the debug page shows about a single context variable"
    variable = {
        "name": name,
        "help": rst_to_html(help),
        "documented_type": type_name,
        "actual_type": type(value).__name__,
        "snippet": "{{ " + name + " }}",
        "kind": "value",
        "value": None,
        "value_length": 0,
        "collapsed": False,
        "truncated": False,
        "signature": None,
        "is_async": False,
        "doc": "",
        "members": [],
        "members_truncated": False,
    }
    if inspect.isroutine(value) or inspect.isclass(value):
        variable["kind"] = "function"
        variable["signature"] = _signature(value)
        variable["is_async"] = inspect.iscoroutinefunction(value)
        variable["doc"] = _docstring(value)
        variable["snippet"] = "{{ " + name + "() }}"
        return variable
    if not _is_data(value):
        variable["kind"] = "object"
        variable["doc"] = _docstring(type(value))
        members = _members(value)
        variable["members_truncated"] = len(members) > MAX_MEMBERS
        variable["members"] = members[:MAX_MEMBERS]
        formatted = repr(value)
    else:
        formatted = format_value(value)
    variable["value_length"] = len(formatted)
    variable["collapsed"] = formatted.count("\n") >= COLLAPSE_LINES
    if len(formatted) > MAX_VALUE_LENGTH:
        variable["truncated"] = True
        formatted = formatted[:MAX_VALUE_LENGTH]
    variable["value"] = formatted
    return variable


def _page_intro(context_class):
    if context_class is None:
        return ""
    intro = Markup("Documented fields of <code>{}</code>.").format(
        context_class.__name__
    )
    if context_class.stable:
        intro += " These are a stable contract for custom templates."
    return intro


def build_context_debug(template_context, *, context_class, plugin_keys, base_docs):
    """
    Group the variables in a rendered template context into the sections
    shown on the debug page.

    ``context_class`` is the Context dataclass for the page, or None if the
    page passed a plain dictionary. ``plugin_keys`` are the keys that came
    from the extra_template_vars() plugin hook, and ``base_docs`` is
    TEMPLATE_BASE_CONTEXT.
    """
    documented = {}
    if context_class is not None:
        documented = {f.name: f for f in context_class.documented_fields()}

    page, undocumented, plugins, base = [], [], [], []
    for name, value in template_context.items():
        # Plugins can replace any other variable, and render_template()
        # replaces anything the view passed using the name of a base variable
        if name in plugin_keys:
            plugins.append(describe_variable(name, value))
        elif name in base_docs:
            base.append(describe_variable(name, value, help=base_docs[name]))
        elif name in documented:
            field = documented[name]
            page.append(
                describe_variable(
                    name, value, help=field.help, type_name=field.type_name
                )
            )
        else:
            undocumented.append(describe_variable(name, value))

    sections = [
        {
            "id": "page",
            "title": "Variables for this page",
            "intro": _page_intro(context_class),
            # The template shows a warning for the variables of a debugging tool
            "unstable": context_class is not None and not context_class.stable,
            "variables": page,
        },
        {
            "id": "undocumented",
            "title": "Undocumented variables",
            "intro": (
                "Passed to this template but not part of the documented "
                "contract, so they may change or be removed in any release."
            ),
            "variables": undocumented,
        },
        {
            "id": "plugins",
            "title": "Variables added by plugins",
            "intro": Markup(
                "Added by plugins using the <code>extra_template_vars()</code> "
                "plugin hook."
            ),
            "variables": plugins,
        },
        {
            "id": "base",
            "title": "Variables available on every page",
            "intro": Markup(
                "Added by <code>datasette.render_template()</code>, including "
                "for pages rendered by plugins."
            ),
            "variables": base,
        },
    ]
    return [section for section in sections if section["variables"]]
