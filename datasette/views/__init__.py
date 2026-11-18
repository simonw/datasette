import dataclasses
import re
import types
import typing
from dataclasses import dataclass


@dataclass(frozen=True)
class ContextField:
    name: str
    type_name: str
    help: str
    from_extra: bool = False
    # True if the JSON version of the page returns a key of this name
    json: bool = False


def _type_name(type_):
    if type_ is type(None):
        return "None"
    origin = typing.get_origin(type_)
    args = typing.get_args(type_)
    if origin in (typing.Union, types.UnionType):
        return " | ".join(_type_name(arg) for arg in args)
    if origin is not None:
        name = getattr(origin, "__name__", str(origin).removeprefix("typing."))
        return "{}[{}]".format(name, ", ".join(_type_name(arg) for arg in args))
    return getattr(type_, "__name__", str(type_).removeprefix("typing."))


def select_templates_field():
    """
    The select_templates field of a Context rendered by BaseView.render(),
    which fills it in. Has a default, so declare it after the other fields.
    """
    return dataclasses.field(
        default=None,
        metadata={
            "help": "The template names Datasette considered, with an asterisk next to the one that was used"
        },
    )


def alternate_url_json_field():
    """
    The alternate_url_json field of a Context rendered by BaseView.render()
    for a view that has a JSON alternate. Has a default, so declare it after
    the other fields.
    """
    return dataclasses.field(
        default=None,
        metadata={"help": "URL of the JSON version of this page"},
    )


def from_extra():
    """
    Declare a Context dataclass field whose value comes from a registered
    Extra of the same name - its documentation is the Extra description,
    so the doc string lives next to the resolve() code rather than being
    duplicated on the dataclass.
    """
    return dataclasses.field(metadata={"from_extra": True})


class Context:
    "Base class for all documented contexts"

    # Set on subclasses whose from_extra() fields should be resolved
    # against the extras registry for this scope
    extras_scope = None

    # False for debugging tools. Their variables are documented, but are
    # not part of the contract that custom templates can rely on until the
    # next major version
    stable = True

    # Heading used for this page in the documentation. Defaults to the
    # class name, so QueryListContext becomes "Query list page"
    documented_title = None

    @classmethod
    def page_title(cls):
        "Title of this page in the template context documentation"
        if cls.documented_title:
            return cls.documented_title
        words = re.findall(r"[A-Z][a-z0-9]*", cls.__name__.removesuffix("Context"))
        return " ".join([words[0]] + [w.lower() for w in words[1:]]) + " page"

    @classmethod
    def documented_fields(cls):
        "List of ContextField describing the documented fields of this context"
        documented = []
        for f in dataclasses.fields(cls):
            if f.name.startswith("_"):
                continue
            is_from_extra = bool(f.metadata.get("from_extra"))
            if is_from_extra:
                help_text = cls._extra_description(f.name)
            else:
                help_text = f.metadata.get("help", "")
            documented.append(
                ContextField(
                    name=f.name,
                    type_name=_type_name(f.type),
                    help=help_text,
                    from_extra=is_from_extra,
                    json=bool(f.metadata.get("json")),
                )
            )
        return documented

    @classmethod
    def json_keys(cls):
        """
        Names of the fields declared with ``"json": True`` in their metadata,
        which the JSON version of the page returns under the same key.
        """
        return [f.name for f in dataclasses.fields(cls) if f.metadata.get("json")]

    @classmethod
    def _extra_description(cls, name):
        # Imported lazily - table_extras is not needed just to define
        # Context subclasses
        from datasette.views.table_extras import table_extra_registry

        try:
            extra_class = table_extra_registry.classes_by_name[name]
        except KeyError:
            raise KeyError(
                f"{cls.__name__}.{name} is declared with from_extra() but there is no "
                "registered extra of that name"
            )
        if cls.extras_scope is not None and not extra_class.available_for(
            cls.extras_scope
        ):
            raise ValueError(
                f"{cls.__name__}.{name} is declared with from_extra() but the {name} extra is "
                f"not available for scope {cls.extras_scope}"
            )
        return extra_class.description or ""
