(events)=
# Events

Datasette includes a mechanism for tracking events that occur while the software is running. This is primarily intended to be used by plugins, which can both trigger events and listen for events.

The core Datasette application triggers events for actions such as signing in or changing data. Each event is a dataclass with a `name`, a `created` timestamp, an `actor` and fields describing the action. This page describes the built-in event classes.

Note that these events will *not* fire for changes made to a SQLite database by a process other than Datasette itself.

Plugins can listen for events using the {ref}`plugin_hook_track_event` plugin hook, which will be called with instances of the following classes - or additional classes {ref}`registered by other plugins <plugin_hook_register_events>`.

## Event actors

The `actor=` argument identifies the authenticated user or API client responsible for the event. It uses the same JSON-compatible dictionary as {ref}`request.actor <authentication_actor>`, and should normally include a unique `"id"` string. Pass `actor=None` for an anonymous request or an action that was not initiated by an authenticated actor.

`actor` is a required field on the event object, not an argument to `datasette.track_event()`. A plugin that tracks an event associated with the current request can use:

```python
from datasette.events import InsertRowsEvent


await datasette.track_event(
    InsertRowsEvent(
        actor=request.actor,
        database="data",
        table="items",
        num_rows=1,
        ignore=False,
        replace=False,
    )
)
```

The actor dictionary is passed to `track_event` hooks as part of the event. Plugins that record events should treat it as authentication metadata and avoid adding secrets or credentials to it.

## Debugging events

The [datasette-debug-events](https://github.com/datasette/datasette-debug-events) plugin prints every event to standard error. Install it in the same environment as Datasette:

```bash
datasette install datasette-debug-events
```

Once installed, run Datasette from a terminal and perform the action you want to inspect. The corresponding events, including their actors and properties, will be displayed in that terminal.

```{eval-rst}
.. automodule:: datasette.events
    :members:
    :exclude-members: Event
```
