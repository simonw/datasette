(events)=
# Events

Datasette includes a mechanism for tracking events that occur while the software is running. This is primarily intended to be used by plugins, which can both trigger events and listen for events.

The core Datasette application triggers events when certain things happen. This page describes those events.

Note that these events will *not* fire for changes made to a SQLite database by a process other than Datasette itself.

Plugins can listen for events using the {ref}`plugin_hook_track_event` plugin hook, which will be called with instances of the following classes - or additional classes {ref}`registered by other plugins <plugin_hook_register_events>`.

## Delivery guarantees for database lifecycle events

The ``add-database`` and ``remove-database`` events have some specific delivery characteristics:

- Delivery is asynchronous. Listeners run shortly after the change, not before the triggering ``add_database()`` or ``remove_database()`` call returns.
- These events fire only for changes made at runtime - while an event loop is running, after Datasette's startup has completed. Databases attached while the instance is starting up do not produce events: plugins that need to see those should iterate over ``datasette.databases`` in their own {ref}`plugin_hook_startup` hook.
- Rapid successive changes involving the same database name may reach listeners interleaved. Listeners should tolerate events arriving out of order.
- ``event.actor`` is ``None`` for programmatic calls made by Datasette itself or by plugins.

```{eval-rst}
.. automodule:: datasette.events
    :members:
    :exclude-members: Event
```
