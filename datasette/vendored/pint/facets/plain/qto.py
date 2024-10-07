from __future__ import annotations

import bisect
import math
import numbers
import warnings
from typing import TYPE_CHECKING

from ...compat import (
    mip_INF,
    mip_INTEGER,
    mip_Model,
    mip_model,
    mip_OptimizationStatus,
    mip_xsum,
)
from ...errors import UndefinedBehavior
from ...util import infer_base_unit

if TYPE_CHECKING:
    from ..._typing import UnitLike
    from ...util import UnitsContainer
    from .quantity import PlainQuantity


def _get_reduced_units(
    quantity: PlainQuantity, units: UnitsContainer
) -> UnitsContainer:
    # loop through individual units and compare to each other unit
    # can we do better than a nested loop here?
    for unit1, exp in units.items():
        # make sure it wasn't already reduced to zero exponent on prior pass
        if unit1 not in units:
            continue
        for unit2 in units:
            # get exponent after reduction
            exp = units[unit1]
            if unit1 != unit2:
                power = quantity._REGISTRY._get_dimensionality_ratio(unit1, unit2)
                if power:
                    units = units.add(unit2, exp / power).remove([unit1])
                    break
    return units


def ito_reduced_units(quantity: PlainQuantity) -> None:
    """Return PlainQuantity scaled in place to reduced units, i.e. one unit per
    dimension. This will not reduce compound units (e.g., 'J/kg' will not
    be reduced to m**2/s**2), nor can it make use of contexts at this time.
    """

    # shortcuts in case we're dimensionless or only a single unit
    if quantity.dimensionless:
        return quantity.ito({})
    if len(quantity._units) == 1:
        return None

    units = quantity._units.copy()
    new_units = _get_reduced_units(quantity, units)

    return quantity.ito(new_units)


def to_reduced_units(
    quantity: PlainQuantity,
) -> PlainQuantity:
    """Return PlainQuantity scaled in place to reduced units, i.e. one unit per
    dimension. This will not reduce compound units (intentionally), nor
    can it make use of contexts at this time.
    """

    # shortcuts in case we're dimensionless or only a single unit
    if quantity.dimensionless:
        return quantity.to({})
    if len(quantity._units) == 1:
        return quantity

    units = quantity._units.copy()
    new_units = _get_reduced_units(quantity, units)

    return quantity.to(new_units)


def to_compact(
    quantity: PlainQuantity, unit: UnitsContainer | None = None
) -> PlainQuantity:
    """ "Return PlainQuantity rescaled to compact, human-readable units.

    To get output in terms of a different unit, use the unit parameter.


    Examples
    --------

    >>> import pint
    >>> ureg = pint.UnitRegistry()
    >>> (200e-9*ureg.s).to_compact()
    <Quantity(200.0, 'nanosecond')>
    >>> (1e-2*ureg('kg m/s^2')).to_compact('N')
    <Quantity(10.0, 'millinewton')>
    """

    if not isinstance(quantity.magnitude, numbers.Number) and not hasattr(
        quantity.magnitude, "nominal_value"
    ):
        warnings.warn(
            "to_compact applied to non numerical types has an undefined behavior.",
            UndefinedBehavior,
            stacklevel=2,
        )
        return quantity

    if (
        quantity.unitless
        or quantity.magnitude == 0
        or math.isnan(quantity.magnitude)
        or math.isinf(quantity.magnitude)
    ):
        return quantity

    SI_prefixes: dict[int, str] = {}
    for prefix in quantity._REGISTRY._prefixes.values():
        try:
            scale = prefix.converter.scale
            # Kludgy way to check if this is an SI prefix
            log10_scale = int(math.log10(scale))
            if log10_scale == math.log10(scale):
                SI_prefixes[log10_scale] = prefix.name
        except Exception:
            SI_prefixes[0] = ""

    SI_prefixes_list = sorted(SI_prefixes.items())
    SI_powers = [item[0] for item in SI_prefixes_list]
    SI_bases = [item[1] for item in SI_prefixes_list]

    if unit is None:
        unit = infer_base_unit(quantity, registry=quantity._REGISTRY)
    else:
        unit = infer_base_unit(quantity.__class__(1, unit), registry=quantity._REGISTRY)

    q_base = quantity.to(unit)

    magnitude = q_base.magnitude
    # Support uncertainties
    if hasattr(magnitude, "nominal_value"):
        magnitude = magnitude.nominal_value

    units = list(q_base._units.items())
    units_numerator = [a for a in units if a[1] > 0]

    if len(units_numerator) > 0:
        unit_str, unit_power = units_numerator[0]
    else:
        unit_str, unit_power = units[0]

    if unit_power > 0:
        power = math.floor(math.log10(abs(magnitude)) / float(unit_power) / 3) * 3
    else:
        power = math.ceil(math.log10(abs(magnitude)) / float(unit_power) / 3) * 3

    index = bisect.bisect_left(SI_powers, power)

    if index >= len(SI_bases):
        index = -1

    prefix_str = SI_bases[index]

    new_unit_str = prefix_str + unit_str
    new_unit_container = q_base._units.rename(unit_str, new_unit_str)

    return quantity.to(new_unit_container)


def to_preferred(
    quantity: PlainQuantity, preferred_units: list[UnitLike] | None = None
) -> PlainQuantity:
    """Return Quantity converted to a unit composed of the preferred units.

    Examples
    --------

    >>> import pint
    >>> ureg = pint.UnitRegistry()
    >>> (1*ureg.acre).to_preferred([ureg.meters])
    <Quantity(4046.87261, 'meter ** 2')>
    >>> (1*(ureg.force_pound*ureg.m)).to_preferred([ureg.W])
    <Quantity(4.44822162, 'watt * second')>
    """

    units = _get_preferred(quantity, preferred_units)
    return quantity.to(units)


def ito_preferred(
    quantity: PlainQuantity, preferred_units: list[UnitLike] | None = None
) -> PlainQuantity:
    """Return Quantity converted to a unit composed of the preferred units.

    Examples
    --------

    >>> import pint
    >>> ureg = pint.UnitRegistry()
    >>> (1*ureg.acre).to_preferred([ureg.meters])
    <Quantity(4046.87261, 'meter ** 2')>
    >>> (1*(ureg.force_pound*ureg.m)).to_preferred([ureg.W])
    <Quantity(4.44822162, 'watt * second')>
    """

    units = _get_preferred(quantity, preferred_units)
    return quantity.ito(units)


def _get_preferred(
    quantity: PlainQuantity, preferred_units: list[UnitLike] | None = None
) -> PlainQuantity:
    if preferred_units is None:
        preferred_units = quantity._REGISTRY.default_preferred_units

    if not quantity.dimensionality:
        return quantity._units.copy()

    # The optimizer isn't perfect, and will sometimes miss obvious solutions.
    # This sub-algorithm is less powerful, but always finds the very simple solutions.
    def find_simple():
        best_ratio = None
        best_unit = None
        self_dims = sorted(quantity.dimensionality)
        self_exps = [quantity.dimensionality[d] for d in self_dims]
        s_exps_head, *s_exps_tail = self_exps
        n = len(s_exps_tail)
        for preferred_unit in preferred_units:
            dims = sorted(preferred_unit.dimensionality)
            if dims == self_dims:
                p_exps_head, *p_exps_tail = (
                    preferred_unit.dimensionality[d] for d in dims
                )
                if all(
                    s_exps_tail[i] * p_exps_head == p_exps_tail[i] ** s_exps_head
                    for i in range(n)
                ):
                    ratio = p_exps_head / s_exps_head
                    ratio = max(ratio, 1 / ratio)
                    if best_ratio is None or ratio < best_ratio:
                        best_ratio = ratio
                        best_unit = preferred_unit ** (s_exps_head / p_exps_head)
        return best_unit

    simple = find_simple()
    if simple is not None:
        return simple

    # For each dimension (e.g. T(ime), L(ength), M(ass)), assign a default base unit from
    # the collection of base units

    unit_selections = {
        base_unit.dimensionality: base_unit
        for base_unit in map(quantity._REGISTRY.Unit, quantity._REGISTRY._base_units)
    }

    # Override the default unit of each dimension with the 1D-units used in this Quantity
    unit_selections.update(
        {
            unit.dimensionality: unit
            for unit in map(quantity._REGISTRY.Unit, quantity._units.keys())
        }
    )

    # Determine the preferred unit for each dimensionality from the preferred_units
    # (A prefered unit doesn't have to be only one dimensional, e.g. Watts)
    preferred_dims = {
        preferred_unit.dimensionality: preferred_unit
        for preferred_unit in map(quantity._REGISTRY.Unit, preferred_units)
    }

    # Combine the defaults and preferred, favoring the preferred
    unit_selections.update(preferred_dims)

    # This algorithm has poor asymptotic time complexity, so first reduce the considered
    # dimensions and units to only those that are useful to the problem

    # The dimensions (without powers) of this Quantity
    dimension_set = set(quantity.dimensionality)

    # Getting zero exponents in dimensions not in dimension_set can be facilitated
    # by units that interact with that dimension and one or more dimension_set members.
    # For example MT^1 * LT^-1 lets you get MLT^0 when T is not in dimension_set.
    # For each candidate unit that interacts with a dimension_set member, add the
    # candidate unit's other dimensions to dimension_set, and repeat until no more
    # dimensions are selected.

    discovery_done = False
    while not discovery_done:
        discovery_done = True
        for d in unit_selections:
            unit_dimensions = set(d)
            intersection = unit_dimensions.intersection(dimension_set)
            if 0 < len(intersection) < len(unit_dimensions):
                # there are dimensions in this unit that are in dimension set
                # and others that are not in dimension set
                dimension_set = dimension_set.union(unit_dimensions)
                discovery_done = False
                break

    # filter out dimensions and their unit selections that don't interact with any
    # dimension_set members
    unit_selections = {
        dimensionality: unit
        for dimensionality, unit in unit_selections.items()
        if set(dimensionality).intersection(dimension_set)
    }

    # update preferred_units with the selected units that were originally preferred
    preferred_units = list(
        {u for d, u in unit_selections.items() if d in preferred_dims}
    )
    preferred_units.sort(key=str)  # for determinism

    # and unpreferred_units are the selected units that weren't originally preferred
    unpreferred_units = list(
        {u for d, u in unit_selections.items() if d not in preferred_dims}
    )
    unpreferred_units.sort(key=str)  # for determinism

    # for indexability
    dimensions = list(dimension_set)
    dimensions.sort()  # for determinism

    # the powers for each elemet of dimensions (the list) for this Quantity
    dimensionality = [quantity.dimensionality[dimension] for dimension in dimensions]

    # Now that the input data is minimized, setup the optimization problem

    # use mip to select units from preferred units

    model = mip_Model()
    model.verbose = 0

    # Make one variable for each candidate unit

    vars = [
        model.add_var(str(unit), lb=-mip_INF, ub=mip_INF, var_type=mip_INTEGER)
        for unit in (preferred_units + unpreferred_units)
    ]

    # where [u1 ... uN] are powers of N candidate units (vars)
    # and [d1(uI) ... dK(uI)] are the K dimensional exponents of candidate unit I
    # and [t1 ... tK] are the dimensional exponents of the quantity (quantity)
    # create the following constraints
    #
    #                ⎡ d1(u1) ⋯ dK(u1) ⎤
    # [ u1 ⋯ uN ] * ⎢    ⋮    ⋱         ⎢ = [ t1 ⋯ tK ]
    #                ⎣ d1(uN)    dK(uN) ⎦
    #
    # in English, the units we choose, and their exponents, when combined, must have the
    # target dimensionality

    matrix = [
        [preferred_unit.dimensionality[dimension] for dimension in dimensions]
        for preferred_unit in (preferred_units + unpreferred_units)
    ]

    # Do the matrix multiplication with mip_model.xsum for performance and create constraints
    for i in range(len(dimensions)):
        dot = mip_model.xsum([var * vector[i] for var, vector in zip(vars, matrix)])
        # add constraint to the model
        model += dot == dimensionality[i]

    # where [c1 ... cN] are costs, 1 when a preferred variable, and a large value when not
    # minimize sum(abs(u1) * c1 ... abs(uN) * cN)

    # linearize the optimization variable via a proxy
    objective = model.add_var("objective", lb=0, ub=mip_INF, var_type=mip_INTEGER)

    # Constrain the objective to be equal to the sums of the absolute values of the preferred
    # unit powers. Do this by making a separate constraint for each permutation of signedness.
    # Also apply the cost coefficient, which causes the output to prefer the preferred units

    # prefer units that interact with fewer dimensions
    cost = [len(p.dimensionality) for p in preferred_units]

    # set the cost for non preferred units to a higher number
    bias = (
        max(map(abs, dimensionality)) * max((1, *cost)) * 10
    )  # arbitrary, just needs to be larger
    cost.extend([bias] * len(unpreferred_units))

    for i in range(1 << len(vars)):
        sum = mip_xsum(
            [
                (-1 if i & 1 << (len(vars) - j - 1) else 1) * cost[j] * var
                for j, var in enumerate(vars)
            ]
        )
        model += objective >= sum

    model.objective = objective

    # run the mips minimizer and extract the result if successful
    if model.optimize() == mip_OptimizationStatus.OPTIMAL:
        optimal_units = []
        min_objective = float("inf")
        for i in range(model.num_solutions):
            if model.objective_values[i] < min_objective:
                min_objective = model.objective_values[i]
                optimal_units.clear()
            elif model.objective_values[i] > min_objective:
                continue

            temp_unit = quantity._REGISTRY.Unit("")
            for var in vars:
                if var.xi(i):
                    temp_unit *= quantity._REGISTRY.Unit(var.name) ** var.xi(i)
            optimal_units.append(temp_unit)

        sorting_keys = {tuple(sorted(unit._units)): unit for unit in optimal_units}
        min_key = sorted(sorting_keys)[0]
        result_unit = sorting_keys[min_key]

        return result_unit

    # for whatever reason, a solution wasn't found
    # return the original quantity
    return quantity._units.copy()
