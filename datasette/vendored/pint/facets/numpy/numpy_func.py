"""
    pint.facets.numpy.numpy_func
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    :copyright: 2022 by Pint Authors, see AUTHORS for more details.
    :license: BSD, see LICENSE for more details.
"""

from __future__ import annotations

import warnings
from inspect import signature
from itertools import chain

from ...compat import is_upcast_type, np, zero_or_nan
from ...errors import DimensionalityError, OffsetUnitCalculusError, UnitStrippedWarning
from ...util import iterable, sized

HANDLED_UFUNCS = {}
HANDLED_FUNCTIONS = {}


# Shared Implementation Utilities


def _is_quantity(obj):
    """Test for _units and _magnitude attrs.

    This is done in place of isinstance(Quantity, arg), which would cause a circular import.

    Parameters
    ----------
    obj : Object


    Returns
    -------
    bool
    """
    return hasattr(obj, "_units") and hasattr(obj, "_magnitude")


def _is_sequence_with_quantity_elements(obj):
    """Test for sequences of quantities.

    Parameters
    ----------
    obj : object


    Returns
    -------
    True if obj is a sequence and at least one element is a Quantity; False otherwise
    """
    if np is not None and isinstance(obj, np.ndarray) and not obj.dtype.hasobject:
        # If obj is a numpy array, avoid looping on all elements
        # if dtype does not have objects
        return False
    return (
        iterable(obj)
        and sized(obj)
        and not isinstance(obj, str)
        and any(_is_quantity(item) for item in obj)
    )


def _get_first_input_units(args, kwargs=None):
    """Obtain the first valid unit from a collection of args and kwargs."""
    kwargs = kwargs or {}
    for arg in chain(args, kwargs.values()):
        if _is_quantity(arg):
            return arg.units
        elif _is_sequence_with_quantity_elements(arg):
            return next(arg_i.units for arg_i in arg if _is_quantity(arg_i))
    raise TypeError("Expected at least one Quantity; found none")


def convert_arg(arg, pre_calc_units):
    """Convert quantities and sequences of quantities to pre_calc_units and strip units.

    Helper function for convert_to_consistent_units. pre_calc_units must be given as a
    pint Unit or None.
    """
    if isinstance(arg, bool):
        return arg
    if pre_calc_units is not None:
        if _is_quantity(arg):
            return arg.m_as(pre_calc_units)
        elif _is_sequence_with_quantity_elements(arg):
            return [convert_arg(item, pre_calc_units) for item in arg]
        elif arg is not None:
            if pre_calc_units.dimensionless:
                return pre_calc_units._REGISTRY.Quantity(arg).m_as(pre_calc_units)
            elif not _is_quantity(arg) and zero_or_nan(arg, True):
                return arg
            else:
                raise DimensionalityError("dimensionless", pre_calc_units)
    elif _is_quantity(arg):
        return arg.m
    elif _is_sequence_with_quantity_elements(arg):
        return [convert_arg(item, pre_calc_units) for item in arg]
    return arg


def convert_to_consistent_units(*args, pre_calc_units=None, **kwargs):
    """Prepare args and kwargs for wrapping by unit conversion and stripping.

    If pre_calc_units is not None, takes the args and kwargs for a NumPy function and
    converts any Quantity or Sequence of Quantities into the units of the first
    Quantity/Sequence of Quantities and returns the magnitudes. Other args/kwargs (except booleans) are
    treated as dimensionless Quantities. If pre_calc_units is None, units are simply
    stripped.
    """
    return (
        tuple(convert_arg(arg, pre_calc_units=pre_calc_units) for arg in args),
        {
            key: convert_arg(arg, pre_calc_units=pre_calc_units)
            for key, arg in kwargs.items()
        },
    )


def unwrap_and_wrap_consistent_units(*args):
    """Strip units from args while providing a rewrapping function.

    Returns the given args as parsed by convert_to_consistent_units assuming units of
    first arg with units, along with a wrapper to restore that unit to the output.

    """
    if all(not _is_quantity(arg) for arg in args):
        return args, lambda x: x

    first_input_units = _get_first_input_units(args)
    args, _ = convert_to_consistent_units(*args, pre_calc_units=first_input_units)
    return (
        args,
        lambda value: first_input_units._REGISTRY.Quantity(value, first_input_units),
    )


def get_op_output_unit(unit_op, first_input_units, all_args=None, size=None):
    """Determine resulting unit from given operation.

    Options for `unit_op`:

    - "sum": `first_input_units`, unless non-multiplicative, which raises
      OffsetUnitCalculusError
    - "mul": product of all units in `all_args`
    - "delta": `first_input_units`, unless non-multiplicative, which uses delta version
    - "delta,div": like "delta", but divided by all units in `all_args` except the first
    - "div": unit of first argument in `all_args` (or dimensionless if not a Quantity) divided
      by all following units
    - "variance": square of `first_input_units`, unless non-multiplicative, which raises
      OffsetUnitCalculusError
    - "square": square of `first_input_units`
    - "sqrt": square root of `first_input_units`
    - "reciprocal": reciprocal of `first_input_units`
    - "size": `first_input_units` raised to the power of `size`
    - "invdiv": inverse of `div`, product of all following units divided by first argument unit

    Parameters
    ----------
    unit_op :

    first_input_units :

    all_args :
         (Default value = None)
    size :
         (Default value = None)

    Returns
    -------

    """
    all_args = all_args or []

    if unit_op == "sum":
        result_unit = (1 * first_input_units + 1 * first_input_units).units
    elif unit_op == "mul":
        product = first_input_units._REGISTRY.parse_units("")
        for x in all_args:
            if hasattr(x, "units"):
                product *= x.units
        result_unit = product
    elif unit_op == "delta":
        result_unit = (1 * first_input_units - 1 * first_input_units).units
    elif unit_op == "delta,div":
        product = (1 * first_input_units - 1 * first_input_units).units
        for x in all_args[1:]:
            if hasattr(x, "units"):
                product /= x.units
        result_unit = product
    elif unit_op == "div":
        # Start with first arg in numerator, all others in denominator
        product = getattr(
            all_args[0], "units", first_input_units._REGISTRY.parse_units("")
        )
        for x in all_args[1:]:
            if hasattr(x, "units"):
                product /= x.units
        result_unit = product
    elif unit_op == "variance":
        result_unit = ((1 * first_input_units + 1 * first_input_units) ** 2).units
    elif unit_op == "square":
        result_unit = first_input_units**2
    elif unit_op == "sqrt":
        result_unit = first_input_units**0.5
    elif unit_op == "cbrt":
        result_unit = first_input_units ** (1 / 3)
    elif unit_op == "reciprocal":
        result_unit = first_input_units**-1
    elif unit_op == "size":
        if size is None:
            raise ValueError('size argument must be given when unit_op=="size"')
        result_unit = first_input_units**size
    elif unit_op == "invdiv":
        # Start with first arg in numerator, all others in denominator
        product = getattr(
            all_args[0], "units", first_input_units._REGISTRY.parse_units("")
        )
        for x in all_args[1:]:
            if hasattr(x, "units"):
                product /= x.units
        result_unit = product**-1
    else:
        raise ValueError(f"Output unit method {unit_op} not understood")

    return result_unit


def implements(numpy_func_string, func_type):
    """Register an __array_function__/__array_ufunc__ implementation for Quantity
    objects.

    """

    def decorator(func):
        if func_type == "function":
            HANDLED_FUNCTIONS[numpy_func_string] = func
        elif func_type == "ufunc":
            HANDLED_UFUNCS[numpy_func_string] = func
        else:
            raise ValueError(f"Invalid func_type {func_type}")
        return func

    return decorator


def implement_func(func_type, func_str, input_units=None, output_unit=None):
    """Add default-behavior NumPy function/ufunc to the handled list.

    Parameters
    ----------
    func_type : str
        "function" for NumPy functions, "ufunc" for NumPy ufuncs
    func_str : str
        String representing the name of the NumPy function/ufunc to add
    input_units : pint.Unit or str or None
        Parameter to control how the function downcasts to magnitudes of arguments. If
        `pint.Unit`, converts all args and kwargs to this unit before downcasting to
        magnitude. If "all_consistent", converts all args and kwargs to the unit of the
        first Quantity in args and kwargs before downcasting to magnitude. If some
        other string, the string is parsed as a unit, and all args and kwargs are
        converted to that unit. If None, units are stripped without conversion.
    output_unit : pint.Unit or str or None
        Parameter to control the unit of the output. If `pint.Unit`, output is wrapped
        with that unit. If "match_input", output is wrapped with the unit of the first
        Quantity in args and kwargs. If a string representing a unit operation defined
        in `get_op_output_unit`, output is wrapped by the unit determined by
        `get_op_output_unit`. If some other string, the string is parsed as a unit,
        which becomes the unit of the output. If None, the bare magnitude is returned.


    """
    # If NumPy is not available, do not attempt implement that which does not exist
    if np is None:
        return

    # Handle functions in submodules
    func_str_split = func_str.split(".")
    func = getattr(np, func_str_split[0], None)
    # If the function is not available, do not attempt to implement it
    if func is None:
        return
    for func_str_piece in func_str_split[1:]:
        func = getattr(func, func_str_piece)

    @implements(func_str, func_type)
    def implementation(*args, **kwargs):
        if func_str in ["multiply", "true_divide", "divide", "floor_divide"] and any(
            [
                not _is_quantity(arg) and _is_sequence_with_quantity_elements(arg)
                for arg in args
            ]
        ):
            # the sequence may contain different units, so fall back to element-wise
            return np.array(
                [func(*func_args) for func_args in zip(*args)], dtype=object
            )

        first_input_units = _get_first_input_units(args, kwargs)
        if input_units == "all_consistent":
            # Match all input args/kwargs to same units
            stripped_args, stripped_kwargs = convert_to_consistent_units(
                *args, pre_calc_units=first_input_units, **kwargs
            )
        else:
            if isinstance(input_units, str):
                # Conversion requires Unit, not str
                pre_calc_units = first_input_units._REGISTRY.parse_units(input_units)
            else:
                pre_calc_units = input_units

            # Match all input args/kwargs to input_units, or if input_units is None,
            # simply strip units
            stripped_args, stripped_kwargs = convert_to_consistent_units(
                *args, pre_calc_units=pre_calc_units, **kwargs
            )

        # Determine result through plain numpy function on stripped arguments
        result_magnitude = func(*stripped_args, **stripped_kwargs)

        if output_unit is None:
            # Short circuit and return magnitude alone
            return result_magnitude
        elif output_unit == "match_input":
            result_unit = first_input_units
        elif output_unit in (
            "sum",
            "mul",
            "delta",
            "delta,div",
            "div",
            "invdiv",
            "variance",
            "square",
            "sqrt",
            "cbrt",
            "reciprocal",
            "size",
        ):
            result_unit = get_op_output_unit(
                output_unit, first_input_units, tuple(chain(args, kwargs.values()))
            )
        else:
            result_unit = output_unit

        return first_input_units._REGISTRY.Quantity(result_magnitude, result_unit)


"""
Define ufunc behavior collections.

- `strip_unit_input_output_ufuncs`: units should be ignored on both input and output
- `matching_input_bare_output_ufuncs`: inputs are converted to matching units, but
   outputs are returned as-is
- `matching_input_set_units_output_ufuncs`: inputs are converted to matching units, and
  the output units are as set by the dict value
- `set_units_ufuncs`: dict values are specified as (in_unit, out_unit), so that inputs
  are converted to in_unit before having magnitude passed to NumPy ufunc, and outputs
  are set to have out_unit
- `matching_input_copy_units_output_ufuncs`: inputs are converted to matching units, and
  outputs are set to that unit
- `copy_units_output_ufuncs`: input units (except the first) are ignored, and output is
  set to that of the first input unit
- `op_units_output_ufuncs`: determine output unit from input unit as determined by
  operation (see `get_op_output_unit`)
"""
strip_unit_input_output_ufuncs = ["isnan", "isinf", "isfinite", "signbit", "sign"]
matching_input_bare_output_ufuncs = [
    "equal",
    "greater",
    "greater_equal",
    "less",
    "less_equal",
    "not_equal",
]
matching_input_set_units_output_ufuncs = {"arctan2": "radian"}
set_units_ufuncs = {
    "cumprod": ("", ""),
    "arccos": ("", "radian"),
    "arcsin": ("", "radian"),
    "arctan": ("", "radian"),
    "arccosh": ("", "radian"),
    "arcsinh": ("", "radian"),
    "arctanh": ("", "radian"),
    "exp": ("", ""),
    "expm1": ("", ""),
    "exp2": ("", ""),
    "log": ("", ""),
    "log10": ("", ""),
    "log1p": ("", ""),
    "log2": ("", ""),
    "sin": ("radian", ""),
    "cos": ("radian", ""),
    "tan": ("radian", ""),
    "sinh": ("radian", ""),
    "cosh": ("radian", ""),
    "tanh": ("radian", ""),
    "radians": ("degree", "radian"),
    "degrees": ("radian", "degree"),
    "deg2rad": ("degree", "radian"),
    "rad2deg": ("radian", "degree"),
    "logaddexp": ("", ""),
    "logaddexp2": ("", ""),
}
# TODO (#905 follow-up):
#   while this matches previous behavior, some of these have optional arguments that
#   should not be Quantities. This should be fixed, and tests using these optional
#   arguments should be added.
matching_input_copy_units_output_ufuncs = [
    "compress",
    "conj",
    "conjugate",
    "copy",
    "diagonal",
    "max",
    "mean",
    "min",
    "ptp",
    "ravel",
    "repeat",
    "reshape",
    "round",
    "squeeze",
    "swapaxes",
    "take",
    "trace",
    "transpose",
    "roll",
    "ceil",
    "floor",
    "hypot",
    "rint",
    "copysign",
    "nextafter",
    "trunc",
    "absolute",
    "positive",
    "negative",
    "maximum",
    "minimum",
    "fabs",
]
copy_units_output_ufuncs = ["ldexp", "fmod", "mod", "remainder"]
op_units_output_ufuncs = {
    "var": "square",
    "multiply": "mul",
    "true_divide": "div",
    "divide": "div",
    "floor_divide": "div",
    "sqrt": "sqrt",
    "cbrt": "cbrt",
    "square": "square",
    "reciprocal": "reciprocal",
    "std": "sum",
    "sum": "sum",
    "cumsum": "sum",
    "matmul": "mul",
}


# Perform the standard ufunc implementations based on behavior collections

for ufunc_str in strip_unit_input_output_ufuncs:
    # Ignore units
    implement_func("ufunc", ufunc_str, input_units=None, output_unit=None)

for ufunc_str in matching_input_bare_output_ufuncs:
    # Require all inputs to match units, but output plain ndarray/duck array
    implement_func("ufunc", ufunc_str, input_units="all_consistent", output_unit=None)

for ufunc_str, out_unit in matching_input_set_units_output_ufuncs.items():
    # Require all inputs to match units, but output in specified unit
    implement_func(
        "ufunc", ufunc_str, input_units="all_consistent", output_unit=out_unit
    )

for ufunc_str, (in_unit, out_unit) in set_units_ufuncs.items():
    # Require inputs in specified unit, and output in specified unit
    implement_func("ufunc", ufunc_str, input_units=in_unit, output_unit=out_unit)

for ufunc_str in matching_input_copy_units_output_ufuncs:
    # Require all inputs to match units, and output as first unit in arguments
    implement_func(
        "ufunc", ufunc_str, input_units="all_consistent", output_unit="match_input"
    )

for ufunc_str in copy_units_output_ufuncs:
    # Output as first unit in arguments, but do not convert inputs
    implement_func("ufunc", ufunc_str, input_units=None, output_unit="match_input")

for ufunc_str, unit_op in op_units_output_ufuncs.items():
    implement_func("ufunc", ufunc_str, input_units=None, output_unit=unit_op)


# Define custom ufunc implementations for atypical cases


@implements("modf", "ufunc")
def _modf(x, *args, **kwargs):
    (x,), output_wrap = unwrap_and_wrap_consistent_units(x)
    return tuple(output_wrap(y) for y in np.modf(x, *args, **kwargs))


@implements("frexp", "ufunc")
def _frexp(x, *args, **kwargs):
    (x,), output_wrap = unwrap_and_wrap_consistent_units(x)
    mantissa, exponent = np.frexp(x, *args, **kwargs)
    return output_wrap(mantissa), exponent


@implements("power", "ufunc")
def _power(x1, x2):
    if _is_quantity(x1):
        return x1**x2

    return x2.__rpow__(x1)


@implements("add", "ufunc")
def _add(x1, x2, *args, **kwargs):
    (x1, x2), output_wrap = unwrap_and_wrap_consistent_units(x1, x2)
    return output_wrap(np.add(x1, x2, *args, **kwargs))


@implements("subtract", "ufunc")
def _subtract(x1, x2, *args, **kwargs):
    (x1, x2), output_wrap = unwrap_and_wrap_consistent_units(x1, x2)
    return output_wrap(np.subtract(x1, x2, *args, **kwargs))


# Define custom function implementations


@implements("meshgrid", "function")
def _meshgrid(*xi, **kwargs):
    # Simply need to map input units to onto list of outputs
    input_units = (x.units for x in xi)
    res = np.meshgrid(*(x.m for x in xi), **kwargs)
    return [out * unit for out, unit in zip(res, input_units)]


@implements("full_like", "function")
def _full_like(a, fill_value, **kwargs):
    # Make full_like by multiplying with array from ones_like in a
    # non-multiplicative-unit-safe way
    if hasattr(fill_value, "_REGISTRY"):
        return fill_value._REGISTRY.Quantity(
            np.ones_like(a, **kwargs) * fill_value.m,
            fill_value.units,
        )

    return np.ones_like(a, **kwargs) * fill_value


@implements("interp", "function")
def _interp(x, xp, fp, left=None, right=None, period=None):
    # Need to handle x and y units separately
    (x, xp, period), _ = unwrap_and_wrap_consistent_units(x, xp, period)
    (fp, right, left), output_wrap = unwrap_and_wrap_consistent_units(fp, left, right)
    return output_wrap(np.interp(x, xp, fp, left=left, right=right, period=period))


@implements("where", "function")
def _where(condition, *args):
    if not getattr(condition, "_is_multiplicative", True):
        raise ValueError(
            "Invalid units of the condition: Boolean value of Quantity with offset unit is ambiguous."
        )

    condition = getattr(condition, "magnitude", condition)
    args, output_wrap = unwrap_and_wrap_consistent_units(*args)
    return output_wrap(np.where(condition, *args))


@implements("concatenate", "function")
def _concatenate(sequence, *args, **kwargs):
    sequence, output_wrap = unwrap_and_wrap_consistent_units(*sequence)
    return output_wrap(np.concatenate(sequence, *args, **kwargs))


@implements("stack", "function")
def _stack(arrays, *args, **kwargs):
    arrays, output_wrap = unwrap_and_wrap_consistent_units(*arrays)
    return output_wrap(np.stack(arrays, *args, **kwargs))


@implements("unwrap", "function")
def _unwrap(p, discont=None, axis=-1):
    # np.unwrap only dispatches over p argument, so assume it is a Quantity
    discont = np.pi if discont is None else discont
    return p._REGISTRY.Quantity(np.unwrap(p.m_as("rad"), discont, axis=axis), "rad").to(
        p.units
    )


@implements("copyto", "function")
def _copyto(dst, src, casting="same_kind", where=True):
    if _is_quantity(dst):
        if _is_quantity(src):
            src = src.m_as(dst.units)
        np.copyto(dst._magnitude, src, casting=casting, where=where)
    else:
        warnings.warn(
            "The unit of the quantity is stripped when copying to non-quantity",
            UnitStrippedWarning,
            stacklevel=2,
        )
        np.copyto(dst, src.m, casting=casting, where=where)


@implements("einsum", "function")
def _einsum(subscripts, *operands, **kwargs):
    operand_magnitudes, _ = convert_to_consistent_units(*operands, pre_calc_units=None)
    output_unit = get_op_output_unit("mul", _get_first_input_units(operands), operands)
    return np.einsum(subscripts, *operand_magnitudes, **kwargs) * output_unit


@implements("isin", "function")
def _isin(element, test_elements, assume_unique=False, invert=False):
    if not _is_quantity(element):
        raise ValueError(
            "Cannot test if unit-aware elements are in not-unit-aware array"
        )

    if _is_quantity(test_elements):
        try:
            test_elements = test_elements.m_as(element.units)
        except DimensionalityError:
            # Incompatible unit test elements cannot be in element
            return np.full(element.shape, False)
    elif _is_sequence_with_quantity_elements(test_elements):
        compatible_test_elements = []
        for test_element in test_elements:
            if not _is_quantity(test_element):
                pass
            try:
                compatible_test_elements.append(test_element.m_as(element.units))
            except DimensionalityError:
                # Incompatible unit test elements cannot be in element, but others in
                # sequence may
                pass
        test_elements = compatible_test_elements
    else:
        # Consider non-quantity like dimensionless quantity
        if not element.dimensionless:
            # Unit do not match, so all false
            return np.full(element.shape, False)
        else:
            # Convert to units of element
            element._REGISTRY.Quantity(test_elements).m_as(element.units)

    return np.isin(element.m, test_elements, assume_unique=assume_unique, invert=invert)


@implements("pad", "function")
def _pad(array, pad_width, mode="constant", **kwargs):
    def _recursive_convert(arg, unit):
        if iterable(arg):
            return tuple(_recursive_convert(a, unit=unit) for a in arg)
        elif not _is_quantity(arg):
            if arg == 0 or np.isnan(arg):
                arg = unit._REGISTRY.Quantity(arg, unit)
            else:
                arg = unit._REGISTRY.Quantity(arg, "dimensionless")

        return arg.m_as(unit)

    # pad only dispatches on array argument, so we know it is a Quantity
    units = array.units

    # Handle flexible constant_values and end_values, converting to units if Quantity
    # and ignoring if not
    for key in ("constant_values", "end_values"):
        if key in kwargs:
            kwargs[key] = _recursive_convert(kwargs[key], units)

    return units._REGISTRY.Quantity(
        np.pad(array._magnitude, pad_width, mode=mode, **kwargs), units
    )


@implements("any", "function")
def _any(a, *args, **kwargs):
    # Only valid when multiplicative unit/no offset
    if a._is_multiplicative:
        return np.any(a._magnitude, *args, **kwargs)

    raise ValueError("Boolean value of Quantity with offset unit is ambiguous.")


@implements("all", "function")
def _all(a, *args, **kwargs):
    # Only valid when multiplicative unit/no offset
    if a._is_multiplicative:
        return np.all(a._magnitude, *args, **kwargs)
    else:
        raise ValueError("Boolean value of Quantity with offset unit is ambiguous.")


def implement_prod_func(name):
    if np is None:
        return

    func = getattr(np, name, None)
    if func is None:
        return

    @implements(name, "function")
    def _prod(a, *args, **kwargs):
        arg_names = ("axis", "dtype", "out", "keepdims", "initial", "where")
        all_kwargs = dict(**dict(zip(arg_names, args)), **kwargs)
        axis = all_kwargs.get("axis", None)
        where = all_kwargs.get("where", None)

        registry = a.units._REGISTRY

        if axis is not None and where is not None:
            _, where_ = np.broadcast_arrays(a._magnitude, where)
            exponents = np.unique(np.sum(where_, axis=axis))
            if len(exponents) == 1 or (len(exponents) == 2 and 0 in exponents):
                units = a.units ** np.max(exponents)
            else:
                units = registry.dimensionless
                a = a.to(units)
        elif axis is not None:
            units = a.units ** a.shape[axis]
        elif where is not None:
            exponent = np.sum(where)
            units = a.units**exponent
        else:
            exponent = (
                np.sum(np.logical_not(np.isnan(a))) if name == "nanprod" else a.size
            )
            units = a.units**exponent

        result = func(a._magnitude, *args, **kwargs)

        return registry.Quantity(result, units)


for name in ("prod", "nanprod"):
    implement_prod_func(name)


# Handle mutliplicative functions separately to deal with non-multiplicative units
def _base_unit_if_needed(a):
    if a._is_multiplicative:
        return a
    else:
        if a.units._REGISTRY.autoconvert_offset_to_baseunit:
            return a.to_base_units()
        else:
            raise OffsetUnitCalculusError(a.units)


# NP2 Can remove trapz wrapping when we only support numpy>=2
@implements("trapz", "function")
@implements("trapezoid", "function")
def _trapz(y, x=None, dx=1.0, **kwargs):
    trapezoid = np.trapezoid if hasattr(np, "trapezoid") else np.trapz
    y = _base_unit_if_needed(y)
    units = y.units
    if x is not None:
        if hasattr(x, "units"):
            x = _base_unit_if_needed(x)
            units *= x.units
            x = x._magnitude
        ret = trapezoid(y._magnitude, x, **kwargs)
    else:
        if hasattr(dx, "units"):
            dx = _base_unit_if_needed(dx)
            units *= dx.units
            dx = dx._magnitude
        ret = trapezoid(y._magnitude, dx=dx, **kwargs)

    return y.units._REGISTRY.Quantity(ret, units)


@implements("correlate", "function")
def _correlate(a, v, mode="valid", **kwargs):
    a = _base_unit_if_needed(a)
    v = _base_unit_if_needed(v)
    units = a.units * v.units
    ret = np.correlate(a._magnitude, v._magnitude, mode=mode, **kwargs)
    return a.units._REGISTRY.Quantity(ret, units)


def implement_mul_func(func):
    # If NumPy is not available, do not attempt implement that which does not exist
    if np is None:
        return

    func = getattr(np, func_str)

    @implements(func_str, "function")
    def implementation(a, b, **kwargs):
        a = _base_unit_if_needed(a)
        units = a.units
        if hasattr(b, "units"):
            b = _base_unit_if_needed(b)
            units *= b.units
            b = b._magnitude

        mag = func(a._magnitude, b, **kwargs)
        return a.units._REGISTRY.Quantity(mag, units)


for func_str in ("cross", "dot"):
    implement_mul_func(func_str)


# Implement simple matching-unit or stripped-unit functions based on signature


def implement_consistent_units_by_argument(func_str, unit_arguments, wrap_output=True):
    # If NumPy is not available, do not attempt implement that which does not exist
    if np is None:
        return

    if "." not in func_str:
        func = getattr(np, func_str, None)
    else:
        parts = func_str.split(".")
        module = np
        for part in parts[:-1]:
            module = getattr(module, part, None)
        func = getattr(module, parts[-1], None)

    # if NumPy does not implement it, do not implement it either
    if func is None:
        return

    @implements(func_str, "function")
    def implementation(*args, **kwargs):
        # Bind given arguments to the NumPy function signature
        bound_args = signature(func).bind(*args, **kwargs)

        # Skip unit arguments that are supplied as None
        valid_unit_arguments = [
            label
            for label in unit_arguments
            if label in bound_args.arguments and bound_args.arguments[label] is not None
        ]

        # Unwrap valid unit arguments, ensure consistency, and obtain output wrapper
        unwrapped_unit_args, output_wrap = unwrap_and_wrap_consistent_units(
            *(bound_args.arguments[label] for label in valid_unit_arguments)
        )

        # Call NumPy function with updated arguments
        for i, unwrapped_unit_arg in enumerate(unwrapped_unit_args):
            bound_args.arguments[valid_unit_arguments[i]] = unwrapped_unit_arg
        ret = func(*bound_args.args, **bound_args.kwargs)

        # Conditionally wrap output
        if wrap_output:
            return output_wrap(ret)

        return ret


for func_str, unit_arguments, wrap_output in (
    ("expand_dims", "a", True),
    ("squeeze", "a", True),
    ("rollaxis", "a", True),
    ("moveaxis", "a", True),
    ("around", "a", True),
    ("diagonal", "a", True),
    ("mean", "a", True),
    ("ptp", "a", True),
    ("ravel", "a", True),
    ("round_", "a", True),
    ("round", "a", True),
    ("sort", "a", True),
    ("median", "a", True),
    ("nanmedian", "a", True),
    ("transpose", "a", True),
    ("roll", "a", True),
    ("copy", "a", True),
    ("average", "a", True),
    ("nanmean", "a", True),
    ("swapaxes", "a", True),
    ("nanmin", "a", True),
    ("nanmax", "a", True),
    ("percentile", "a", True),
    ("nanpercentile", "a", True),
    ("quantile", "a", True),
    ("nanquantile", "a", True),
    ("flip", "m", True),
    ("fix", "x", True),
    ("trim_zeros", ["filt"], True),
    ("broadcast_to", ["array"], True),
    ("amax", ["a", "initial"], True),
    ("amin", ["a", "initial"], True),
    ("max", ["a", "initial"], True),
    ("min", ["a", "initial"], True),
    ("searchsorted", ["a", "v"], False),
    ("nan_to_num", ["x", "nan", "posinf", "neginf"], True),
    ("clip", ["a", "a_min", "a_max"], True),
    ("append", ["arr", "values"], True),
    ("compress", "a", True),
    ("linspace", ["start", "stop"], True),
    ("tile", "A", True),
    ("lib.stride_tricks.sliding_window_view", "x", True),
    ("rot90", "m", True),
    ("insert", ["arr", "values"], True),
    ("delete", ["arr"], True),
    ("resize", "a", True),
    ("reshape", "a", True),
    ("intersect1d", ["ar1", "ar2"], True),
):
    implement_consistent_units_by_argument(func_str, unit_arguments, wrap_output)


# implement isclose and allclose
def implement_close(func_str):
    if np is None:
        return

    func = getattr(np, func_str)

    @implements(func_str, "function")
    def implementation(*args, **kwargs):
        bound_args = signature(func).bind(*args, **kwargs)
        labels = ["a", "b"]
        arrays = {label: bound_args.arguments[label] for label in labels}
        if "atol" in bound_args.arguments:
            atol = bound_args.arguments["atol"]
            a = arrays["a"]
            if not hasattr(atol, "_REGISTRY") and hasattr(a, "_REGISTRY"):
                # always use the units of `a`
                atol_ = a._REGISTRY.Quantity(atol, a.units)
            else:
                atol_ = atol
            arrays["atol"] = atol_

        args, _ = unwrap_and_wrap_consistent_units(*arrays.values())
        for label, value in zip(arrays.keys(), args):
            bound_args.arguments[label] = value

        return func(*bound_args.args, **bound_args.kwargs)


for func_str in ("isclose", "allclose"):
    implement_close(func_str)

# Handle atleast_nd functions


def implement_atleast_nd(func_str):
    # If NumPy is not available, do not attempt implement that which does not exist
    if np is None:
        return

    func = getattr(np, func_str)

    @implements(func_str, "function")
    def implementation(*arrays):
        stripped_arrays, _ = convert_to_consistent_units(*arrays)
        arrays_magnitude = func(*stripped_arrays)
        if len(arrays) > 1:
            return [
                (
                    array_magnitude
                    if not hasattr(original, "_REGISTRY")
                    else original._REGISTRY.Quantity(array_magnitude, original.units)
                )
                for array_magnitude, original in zip(arrays_magnitude, arrays)
            ]
        else:
            output_unit = arrays[0].units
            return output_unit._REGISTRY.Quantity(arrays_magnitude, output_unit)


for func_str in ("atleast_1d", "atleast_2d", "atleast_3d"):
    implement_atleast_nd(func_str)


# Handle cumulative products (which must be dimensionless for consistent units across
# output array)
def implement_single_dimensionless_argument_func(func_str):
    # If NumPy is not available, do not attempt implement that which does not exist
    if np is None:
        return

    func = getattr(np, func_str)

    @implements(func_str, "function")
    def implementation(a, *args, **kwargs):
        (a_stripped,), _ = convert_to_consistent_units(
            a, pre_calc_units=a._REGISTRY.parse_units("dimensionless")
        )
        return a._REGISTRY.Quantity(func(a_stripped, *args, **kwargs))


for func_str in ("cumprod", "nancumprod"):
    implement_single_dimensionless_argument_func(func_str)

# Handle single-argument consistent unit functions
for func_str in (
    "block",
    "hstack",
    "vstack",
    "dstack",
    "column_stack",
    "broadcast_arrays",
):
    implement_func(
        "function", func_str, input_units="all_consistent", output_unit="match_input"
    )

# Handle functions that ignore units on input and output
for func_str in (
    "size",
    "isreal",
    "iscomplex",
    "shape",
    "ones_like",
    "zeros_like",
    "empty_like",
    "argsort",
    "argmin",
    "argmax",
    "ndim",
    "nanargmax",
    "nanargmin",
    "count_nonzero",
    "nonzero",
    "result_type",
):
    implement_func("function", func_str, input_units=None, output_unit=None)

# Handle functions with output unit defined by operation
for func_str in (
    "std",
    "nanstd",
    "sum",
    "nansum",
    "cumsum",
    "nancumsum",
    "linalg.norm",
):
    implement_func("function", func_str, input_units=None, output_unit="sum")
for func_str in ("diff", "ediff1d"):
    implement_func("function", func_str, input_units=None, output_unit="delta")
for func_str in ("gradient",):
    implement_func("function", func_str, input_units=None, output_unit="delta,div")
for func_str in ("linalg.solve",):
    implement_func("function", func_str, input_units=None, output_unit="invdiv")
for func_str in ("var", "nanvar"):
    implement_func("function", func_str, input_units=None, output_unit="variance")


def numpy_wrap(func_type, func, args, kwargs, types):
    """Return the result from a NumPy function/ufunc as wrapped by Pint."""

    if func_type == "function":
        handled = HANDLED_FUNCTIONS
        # Need to handle functions in submodules
        name = ".".join(func.__module__.split(".")[1:] + [func.__name__])
    elif func_type == "ufunc":
        handled = HANDLED_UFUNCS
        # ufuncs do not have func.__module__
        name = func.__name__
    else:
        raise ValueError(f"Invalid func_type {func_type}")

    if name not in handled or any(is_upcast_type(t) for t in types):
        return NotImplemented
    return handled[name](*args, **kwargs)
