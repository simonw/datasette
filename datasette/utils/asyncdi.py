import asyncio
from functools import wraps
import inspect

try:
    import graphlib
except ImportError:
    from . import vendored_graphlib as graphlib


def inject(fn):
    fn._inject = True
    return fn


class AsyncMeta(type):
    def __new__(cls, name, bases, attrs):
        # Decorate any items that are 'async def' methods
        _registry = {}
        new_attrs = {"_registry": _registry}
        inject_all = attrs.get("inject_all")
        for key, value in attrs.items():
            if (
                inspect.iscoroutinefunction(value)
                and not value.__name__ == "resolve"
                and (inject_all or getattr(value, "_inject", None))
            ):
                new_attrs[key] = make_method(value)
                _registry[key] = new_attrs[key]
            else:
                new_attrs[key] = value
        # Gather graph for later dependency resolution
        graph = {
            key: {
                p
                for p in inspect.signature(method).parameters.keys()
                if p != "self" and not p.startswith("_")
            }
            for key, method in _registry.items()
        }
        new_attrs["_graph"] = graph
        return super().__new__(cls, name, bases, new_attrs)


def make_method(method):
    parameters = inspect.signature(method).parameters.keys()

    @wraps(method)
    async def inner(self, _results=None, **kwargs):
        # Any parameters not provided by kwargs are resolved from registry
        to_resolve = [p for p in parameters if p not in kwargs and p != "self"]
        missing = [p for p in to_resolve if p not in self._registry]
        assert (
            not missing
        ), "The following DI parameters could not be found in the registry: {}".format(
            missing
        )

        results = {}
        results.update(kwargs)
        if to_resolve:
            resolved_parameters = await self.resolve(to_resolve, _results)
            results.update(resolved_parameters)
        return_value = await method(self, **results)
        if _results is not None:
            _results[method.__name__] = return_value
        return return_value

    return inner


class AsyncBase(metaclass=AsyncMeta):
    async def resolve(self, names, results=None):
        if results is None:
            results = {}

        # Come up with an execution plan, just for these nodes
        ts = graphlib.TopologicalSorter()
        to_do = set(names)
        done = set()
        while to_do:
            item = to_do.pop()
            dependencies = self._graph[item]
            ts.add(item, *dependencies)
            done.add(item)
            # Add any not-done dependencies to the queue
            to_do.update({k for k in dependencies if k not in done})

        ts.prepare()
        plan = []
        while ts.is_active():
            node_group = ts.get_ready()
            plan.append(node_group)
            ts.done(*node_group)

        results = {}
        for node_group in plan:
            awaitables = [
                self._registry[name](
                    self,
                    _results=results,
                    **{k: v for k, v in results.items() if k in self._graph[name]},
                )
                for name in node_group
            ]
            awaitable_results = await asyncio.gather(*awaitables)
            results.update(
                {p[0].__name__: p[1] for p in zip(awaitables, awaitable_results)}
            )

        return {key: value for key, value in results.items() if key in names}
