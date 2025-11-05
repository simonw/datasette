(events)=
# Events

Datasette includes a mechanism for tracking events that occur while the software is running. This is primarily intended to be used by plugins, which can both trigger events and listen for events.

The core Datasette application triggers events when certain things happen. This page describes those events.

Plugins can listen for events using the {ref}`plugin_hook_track_event` plugin hook, which will be called with instances of the following classes - or additional classes {ref}`registered by other plugins <plugin_hook_register_events>`.

```{eval-rst}
.. automodule:: datasette.events
    :members:
    :exclude-members: Event
```
