.. upgrade_guide_v1:

====================================
 Datasette 0.X -> 1.0 Upgrade Guide
====================================


This document specifically reviews what breaking changes Datasette ``1.0`` has when upgrading from a ``0.XX`` version.
For new features that ``1.0`` offers, see the [Changelog](https://docs.datasette.io/en/latest/changelog.html).


Metadata changes
================

Metadata in Datasette.10 was completely revamped.
There are a number of related breaking changes, from the ``metadata.yaml`` file to Python APIs that you'll need to consider when upgrading.

``metadata.yaml`` split into ``datasette.yaml``
-----------------------------------------------

Before Datasette 1.0, the ``metadata.yaml`` file became a kitchen sink if a mix of metadata, configuration, and settings.
Now ``metadata.yaml`` is strictly for metaata (ex title and descriptions of database and tables, licensing info, etc).


Metadata "fallback" has been removed
------------------------------------

Certain keys in metadata like ``license`` used to "fallback" up the chain of ownership.
For example, if you set an ``MIT`` to a database and a table within that database did not have a specified license,
then that table would inherit an ``MIT`` license.

This behavior has been removed in Datasette 1.0. Now license fields must be placed on all items, including individual databases and tables.

The ``get_metadata()`` Plugin hook has been removed
---------------------------------------------------

As of Datasette ``1.0a14`` (2024-XX-XX), the ``get_metadata()`` hook has been deprecated.

.. code-block:: python

    # ❌ DEPRECATED in Datasette 1.0
    @hookimpl
    def get_metadata(datasette, key, database, table):
        pass

Instead, one should use the following methods on a Datasette class:

- :ref:`get_instance_metadata() <datasette_get_instance_metadata>` and  :ref:`set_instance_metadata() <datasette_set_instance_metadata>`
- :ref:`get_database_metadata() <datasette_get_database_metadata>` and  :ref:`set_database_metadata() <datasette_set_database_metadata>`
- :ref:`get_resource_metadata() <datasette_get_resource_metadata>` and  :ref:`set_resource_metadata() <datasette_set_resource_metadata>`
- :ref:`get_column_metadata() <datasette_get_column_metadata>` and  :ref:`set_column_metadata() <datasette_set_column_metadata>`

The ``/metadata.json`` endpoint has been removed
------------------------------------------------

As of Datasette `1.0a14`, the root level ``/metadata.json`` endpoint has been removed.

The ``metadata()`` method on the Datasette class has been removed
-----------------------------------------------------------------

As of Datasette ``1.0a14``, the ``.metadata()`` method on the Datasette Python API has been removed.

Instead, one should use the following methods on a Datasette class:


- :ref:`get_instance_metadata() <datasette_get_instance_metadata>`
- :ref:`get_database_metadata() <datasette_get_database_metadata>`
- :ref:`get_resource_metadata() <datasette_get_resource_metadata>`
- :ref:`get_column_metadata() <datasette_get_column_metadata>`


New endpoint for SQL queries
============================

Previously, if you wanted to run SQL code using the Datasette HTTP API, you could call an endpoint that looked like:

::

    # DEPRECATED: Older endpoint for Datasette 0.XX
    curl http://localhost:8001/_memory?sql=select+123

However, in Datasette 1.0, the endpoint was slightly changed to:

::

    # ✅ Datasette 1.0 and beyond
    curl http://localhost:8001/_memory/-/query?sql=select+123

Specifically, now there's a ``/-/query`` "action" that should be used.

**This isn't a breaking change.** API calls to the older ``/database?sql=...`` endpoint will redirect to the new ``database/-/query?sql=...`` endpoint. However, documentations and example will use the new query endpoint, so it is recommended to use that instead.
