from dataclasses import dataclass
import dataclasses


@dataclass(frozen=True)
class ContextField:
    name: str
    type_name: str
    help: str


class Context:
    "Base class for all documented contexts"

    @classmethod
    def documented_fields(cls):
        "List of ContextField describing the documented fields of this context"
        documented = []
        for f in dataclasses.fields(cls):
            documented.append(
                ContextField(
                    name=f.name,
                    type_name=getattr(f.type, "__name__", str(f.type)),
                    help=f.metadata.get("help", ""),
                )
            )
        return documented
