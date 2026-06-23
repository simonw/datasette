from dataclasses import dataclass
import dataclasses


@dataclass(frozen=True)
class ContextField:
    name: str
    type_name: str
    help: str
    from_extra: bool = False


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
                    type_name=getattr(f.type, "__name__", str(f.type)),
                    help=help_text,
                    from_extra=is_from_extra,
                )
            )
        return documented

    @classmethod
    def _extra_description(cls, name):
        # Imported lazily - table_extras is not needed just to define
        # Context subclasses
        from datasette.views.table_extras import table_extra_registry

        try:
            extra_class = table_extra_registry.classes_by_name[name]
        except KeyError:
            raise KeyError(
                "{}.{} is declared with from_extra() but there is no "
                "registered extra of that name".format(cls.__name__, name)
            )
        if cls.extras_scope is not None and not extra_class.available_for(
            cls.extras_scope
        ):
            raise ValueError(
                "{}.{} is declared with from_extra() but the {} extra is "
                "not available for scope {}".format(
                    cls.__name__, name, name, cls.extras_scope
                )
            )
        return extra_class.description or ""
