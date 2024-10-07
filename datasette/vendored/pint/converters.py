"""
    pint.converters
    ~~~~~~~~~~~~~~~

    Functions and classes related to unit conversions.

    :copyright: 2016 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import fields as dc_fields
from typing import Any, ClassVar

from ._typing import Magnitude
from .compat import HAS_NUMPY, Self, exp, log  # noqa: F401


@dataclass(frozen=True)
class Converter:
    """Base class for value converters."""

    _subclasses: ClassVar[list[type[Converter]]] = []
    _param_names_to_subclass: ClassVar[dict[frozenset[str], type[Converter]]] = {}

    @property
    def is_multiplicative(self) -> bool:
        return True

    @property
    def is_logarithmic(self) -> bool:
        return False

    def to_reference(self, value: Magnitude, inplace: bool = False) -> Magnitude:
        return value

    def from_reference(self, value: Magnitude, inplace: bool = False) -> Magnitude:
        return value

    def __init_subclass__(cls, **kwargs: Any):
        # Get constructor parameters
        super().__init_subclass__(**kwargs)
        cls._subclasses.append(cls)

    @classmethod
    def get_field_names(cls, new_cls: type) -> frozenset[str]:
        return frozenset(p.name for p in dc_fields(new_cls))

    @classmethod
    def preprocess_kwargs(cls, **kwargs: Any) -> dict[str, Any] | None:
        return None

    @classmethod
    def from_arguments(cls, **kwargs: Any) -> Converter:
        kwk = frozenset(kwargs.keys())
        try:
            new_cls = cls._param_names_to_subclass[kwk]
        except KeyError:
            for new_cls in cls._subclasses:
                p_names = frozenset(p.name for p in dc_fields(new_cls))
                if p_names == kwk:
                    cls._param_names_to_subclass[kwk] = new_cls
                    break
            else:
                params = "(" + ", ".join(tuple(kwk)) + ")"
                raise ValueError(
                    f"There is no class registered for parameters {params}"
                )

        kw = new_cls.preprocess_kwargs(**kwargs)
        if kw is None:
            return new_cls(**kwargs)
        return cls.from_arguments(**kw)
