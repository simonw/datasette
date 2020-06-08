.. _authentication:

================================
 Authentication and permissions
================================

Datasette does not require authentication by default. Any visitor to a Datasette instance can explore the full data and execute SQL queries.

Datasette's plugin system can be used to add many different styles of authentication, such as user accounts, single sign-on or API keys.

.. _authentication_actor:

Actors
======

Through plugins, Datasette can support both authenticated users (with cookies) and authenticated API agents (via authentication tokens). The word "actor" is used to cover both of these cases.

Every request to Datasette has an associated actor value, available in the code as ``request.actor``. This can be ``None`` for unauthenticated requests, or a JSON compatible Python dictionary for authenticated users or API agents.

The only required field in an actor is ``"id"``, which must be a string. Plugins may decide to add any other fields to the actor dictionary.

Plugins can use the :ref:`plugin_actor_from_request` hook to implement custom logic for authenticating an actor based on the incoming HTTP request.

.. _authentication_root:

Using the "root" actor
----------------------

Datasette currently leaves almost all forms of authentication to plugins - `datasette-auth-github <https://github.com/simonw/datasette-auth-github>`__ for example.

The one exception is the "root" account, which you can sign into while using Datasette on your local machine. This provides access to a small number of debugging features.

To sign in as root, start Datasette using the ``--root`` command-line option, like this::

    $ datasette --root
    http://127.0.0.1:8001/-/auth-token?token=786fc524e0199d70dc9a581d851f466244e114ca92f33aa3b42a139e9388daa7
    INFO:     Started server process [25801]
    INFO:     Waiting for application startup.
    INFO:     Application startup complete.
    INFO:     Uvicorn running on http://127.0.0.1:8001 (Press CTRL+C to quit)

The URL on the first line includes a one-use token which can be used to sign in as the "root" actor in your browser. Click on that link and then visit ``http://127.0.0.1:8001/-/actor`` to confirm that you are authenticated as an actor that looks like this:

.. code-block:: json

    {
        "id": "root"
    }

.. _authentication_permissions:

Checking permission
===================

Datasette plugins can check if an actor has permission to perform an action using the :ref:`datasette.permission_allowed(...)<datasette_permission_allowed>` method. This method is also used by Datasette core code itself, which allows plugins to help make decisions on which actions are allowed by implementing the :ref:`plugin_permission_allowed` plugin hook.

.. _authentication_permissions_metadata:

Configuring permissions in metadata.json
========================================

You can limit who is allowed to view different parts of your Datasette instance using ``"allow"`` keys in your :ref:`metadata` configuration.

You can control the following:

* Access to the entire Datasette instance
* Access to specific databases
* Access to specific tables and views
* Access to specific :ref:`canned_queries`

If a user cannot access a specific database, they will not be able to access tables, views or queries within that database. If a user cannot access the instance they will not be able to access any of the databases, tables, views or queries.

.. _authentication_permissions_instance:

Controlling access to an instance
---------------------------------

Here's how to restrict access to your entire Datasette instance to just the ``"id": "root"`` user:

.. code-block:: json

    {
        "title": "My private Datasette instance",
        "allow": {
            "id": "root"
        }
    }

To allow any of the actors with an ``id`` matching a specific list of values, use this:

.. code-block:: json

    {
        "allow": {
            "id": ["simon", "cleopaws"]
        }
    }

This works for other keys as well. Imagine an actor that looks like this:

.. code-block:: json

    {
        "id": "simon",
        "roles": ["staff", "developer"]
    }

You can provide access to any user that has "developer" as one of their roles like so:

.. code-block:: json

    {
        "allow": {
            "roles": ["developer"]
        }
    }

Note that "roles" is not a concept that is baked into Datasette - it's more of a convention that plugins can choose to implement and act on.

If you want to provide access to any actor with a value for a specific key, use ``"*"``. For example, to spceify that a query can be accessed by any logged-in user use this:

.. code-block:: json

    {
        "allow": {
            "id": "*"
        }
    }

These keys act as an "or" mechanism. A actor will be able to execute the query if any of their JSON properties match any of the values in the corresponding lists in the ``allow`` block.

.. _authentication_permissions_database:

Controlling access to specific databases
----------------------------------------

To limit access to a specific ``private.db`` database to just authenticated users, use the ``"allow"`` block like this:

.. code-block:: json

    {
        "databases": {
            "private": {
                "allow": {
                    "id": "*"
                }
            }
        }
    }

.. _authentication_permissions_table:

Controlling access to specific tables and views
-----------------------------------------------

To limit access to the ``users`` table in your ``bakery.db`` database:

.. code-block:: json

    {
        "databases": {
            "bakery": {
                "tables": {
                    "users": {
                        "allow": {
                            "id": "*"
                        }
                    }
                }
            }
        }
    }

This works for SQL views as well - you can treat them as if they are tables.

.. warning::
    Restricting access to tables and views in this way will NOT prevent users from querying them using arbitrary SQL queries.

    If you are restricting access to specific tables you should also use the ``"allow_sql"`` block to prevent users from accessing 

.. _authentication_permissions_table:

Controlling access to specific canned queries
---------------------------------------------

To limit access to the ``add_name`` canned query in your ``dogs.db`` database to just the :ref:`root user<authentication_root>`:

.. code-block:: json

    {
        "databases": {
            "dogs": {
                "queries": {
                    "add_name": {
                        "sql": "INSERT INTO names (name) VALUES (:name)",
                        "write": true,
                        "allow": {
                            "id": ["root"]
                        }
                    }
                }
            }
        }
    }

.. _authentication_actor_matches_allow:

actor_matches_allow()
=====================

Plugins that wish to implement the same permissions scheme as canned queries can take advantage of the ``datasette.utils.actor_matches_allow(actor, allow)`` function:

.. code-block:: python

    from datasette.utils import actor_matches_allow

    actor_matches_allow({"id": "root"}, {"id": "*"})
    # returns True

The currently authenticated actor is made available to plugins as ``request.actor``.

.. _PermissionsDebugView:

Permissions Debug
=================

The debug tool at ``/-/permissions`` is only available to the :ref:`authenticated root user <authentication_root>` (or any actor granted the ``permissions-debug`` action according to a plugin).

It shows the thirty most recent permission checks that have been carried out by the Datasette instance.

This is designed to help administrators and plugin authors understand exactly how permission checks are being carried out, in order to effectively configure Datasette's permission system.


.. _permissions:

Permissions
===========

This section lists all of the permission checks that are carried out by Datasette core, along with the ``resource`` if it was passed.

.. _permissions_view_instance:

view-instance
-------------

Top level permission - Actor is allowed to view any pages within this instance, starting at https://latest.datasette.io/


.. _permissions_view_database:

view-database
-------------

Actor is allowed to view a database page, e.g. https://latest.datasette.io/fixtures

``resource`` - string
    The name of the database

.. _permissions_view_database_download:

view-database-download
-----------------------

Actor is allowed to download a database, e.g. https://latest.datasette.io/fixtures.db

``resource`` - string
    The name of the database

.. _permissions_view_table:

view-table
----------

Actor is allowed to view a table (or view) page, e.g. https://latest.datasette.io/fixtures/complex_foreign_keys

``resource`` - tuple: (string, string)
    The name of the database, then the name of the table

.. _permissions_view_query:

view-query
----------

Actor is allowed to view a :ref:`canned query <canned_queries>` page, e.g. https://latest.datasette.io/fixtures/pragma_cache_size

``resource`` - string
    The name of the canned query

.. _permissions_execute_sql:

execute-sql
-----------

Actor is allowed to run arbitrary SQL queries against a specific database, e.g. https://latest.datasette.io/fixtures?sql=select+100

``resource`` - string
    The name of the database

.. _permissions_permissions_debug:

permissions-debug
-----------------

Actor is allowed to view the ``/-/permissions`` debug page.
