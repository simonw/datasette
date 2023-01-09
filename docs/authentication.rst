.. _authentication:

================================
 Authentication and permissions
================================

Datasette does not require authentication by default. Any visitor to a Datasette instance can explore the full data and execute read-only SQL queries.

Datasette's plugin system can be used to add many different styles of authentication, such as user accounts, single sign-on or API keys.

.. _authentication_actor:

Actors
======

Through plugins, Datasette can support both authenticated users (with cookies) and authenticated API agents (via authentication tokens). The word "actor" is used to cover both of these cases.

Every request to Datasette has an associated actor value, available in the code as ``request.actor``. This can be ``None`` for unauthenticated requests, or a JSON compatible Python dictionary for authenticated users or API agents.

The actor dictionary can be any shape - the design of that data structure is left up to the plugins. A useful convention is to include an ``"id"`` string, as demonstrated by the "root" actor below.

Plugins can use the :ref:`plugin_hook_actor_from_request` hook to implement custom logic for authenticating an actor based on the incoming HTTP request.

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

Permissions
===========

Datasette has an extensive permissions system built-in, which can be further extended and customized by plugins.

The key question the permissions system answers is this:

    Is this **actor** allowed to perform this **action**, optionally against this particular **resource**?

**Actors** are :ref:`described above <authentication_actor>`.

An **action** is a string describing the action the actor would like to perform. A full list is :ref:`provided below <permissions>` - examples include ``view-table`` and ``execute-sql``.

A **resource** is the item the actor wishes to interact with - for example a specific database or table. Some actions, such as ``permissions-debug``, are not associated with a particular resource.

Datasette's built-in view permissions (``view-database``, ``view-table`` etc) default to *allow* - unless you :ref:`configure additional permission rules <authentication_permissions_metadata>` unauthenticated users will be allowed to access content.

Permissions with potentially harmful effects should default to *deny*. Plugin authors should account for this when designing new plugins - for example, the `datasette-upload-csvs <https://github.com/simonw/datasette-upload-csvs>`__ plugin defaults to deny so that installations don't accidentally allow unauthenticated users to create new tables by uploading a CSV file.

.. _authentication_permissions_allow:

Defining permissions with "allow" blocks
----------------------------------------

The standard way to define permissions in Datasette is to use an ``"allow"`` block. This is a JSON document describing which actors are allowed to perform a permission.

The most basic form of allow block is this (`allow demo <https://latest.datasette.io/-/allow-debug?actor=%7B%22id%22%3A+%22root%22%7D&allow=%7B%0D%0A++++++++%22id%22%3A+%22root%22%0D%0A++++%7D>`__, `deny demo <https://latest.datasette.io/-/allow-debug?actor=%7B%22id%22%3A+%22trevor%22%7D&allow=%7B%0D%0A++++++++%22id%22%3A+%22root%22%0D%0A++++%7D>`__):

.. code-block:: json

    {
        "allow": {
            "id": "root"
        }
    }

This will match any actors with an ``"id"`` property of ``"root"`` - for example, an actor that looks like this:

.. code-block:: json

    {
        "id": "root",
        "name": "Root User"
    }

An allow block can specify "deny all" using ``false`` (`demo <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22id%22%3A+%22root%22%0D%0A%7D&allow=false>`__):

.. code-block:: json

    {
        "allow": false
    }

An ``"allow"`` of ``true`` allows all access (`demo <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22id%22%3A+%22root%22%0D%0A%7D&allow=true>`__):

.. code-block:: json

    {
        "allow": true
    }

Allow keys can provide a list of values. These will match any actor that has any of those values (`allow demo <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22id%22%3A+%22cleopaws%22%0D%0A%7D&allow=%7B%0D%0A++++%22id%22%3A+%5B%0D%0A++++++++%22simon%22%2C%0D%0A++++++++%22cleopaws%22%0D%0A++++%5D%0D%0A%7D>`__, `deny demo <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22id%22%3A+%22pancakes%22%0D%0A%7D&allow=%7B%0D%0A++++%22id%22%3A+%5B%0D%0A++++++++%22simon%22%2C%0D%0A++++++++%22cleopaws%22%0D%0A++++%5D%0D%0A%7D>`__):

.. code-block:: json

    {
        "allow": {
            "id": ["simon", "cleopaws"]
        }
    }

This will match any actor with an ``"id"`` of either ``"simon"`` or ``"cleopaws"``.

Actors can have properties that feature a list of values. These will be matched against the list of values in an allow block. Consider the following actor:

.. code-block:: json

    {
        "id": "simon",
        "roles": ["staff", "developer"]
    }

This allow block will provide access to any actor that has ``"developer"`` as one of their roles (`allow demo <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22id%22%3A+%22simon%22%2C%0D%0A++++%22roles%22%3A+%5B%0D%0A++++++++%22staff%22%2C%0D%0A++++++++%22developer%22%0D%0A++++%5D%0D%0A%7D&allow=%7B%0D%0A++++%22roles%22%3A+%5B%0D%0A++++++++%22developer%22%0D%0A++++%5D%0D%0A%7D>`__, `deny demo <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22id%22%3A+%22cleopaws%22%2C%0D%0A++++%22roles%22%3A+%5B%22dog%22%5D%0D%0A%7D&allow=%7B%0D%0A++++%22roles%22%3A+%5B%0D%0A++++++++%22developer%22%0D%0A++++%5D%0D%0A%7D>`__):

.. code-block:: json

    {
        "allow": {
            "roles": ["developer"]
        }
    }

Note that "roles" is not a concept that is baked into Datasette - it's a convention that plugins can choose to implement and act on.

If you want to provide access to any actor with a value for a specific key, use ``"*"``. For example, to match any logged-in user specify the following (`allow demo <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22id%22%3A+%22simon%22%0D%0A%7D&allow=%7B%0D%0A++++%22id%22%3A+%22*%22%0D%0A%7D>`__, `deny demo <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22bot%22%3A+%22readme-bot%22%0D%0A%7D&allow=%7B%0D%0A++++%22id%22%3A+%22*%22%0D%0A%7D>`__):

.. code-block:: json

    {
        "allow": {
            "id": "*"
        }
    }

You can specify that only unauthenticated actors (from anynomous HTTP requests) should be allowed access using the special ``"unauthenticated": true`` key in an allow block (`allow demo <https://latest.datasette.io/-/allow-debug?actor=null&allow=%7B%0D%0A++++%22unauthenticated%22%3A+true%0D%0A%7D>`__, `deny demo <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22id%22%3A+%22hello%22%0D%0A%7D&allow=%7B%0D%0A++++%22unauthenticated%22%3A+true%0D%0A%7D>`__):

.. code-block:: json

    {
        "allow": {
            "unauthenticated": true
        }
    }

Allow keys act as an "or" mechanism. An actor will be able to execute the query if any of their JSON properties match any of the values in the corresponding lists in the ``allow`` block. The following block will allow users with either a ``role`` of ``"ops"`` OR users who have an ``id`` of ``"simon"`` or ``"cleopaws"``:

.. code-block:: json

    {
        "allow": {
            "id": ["simon", "cleopaws"],
            "role": "ops"
        }
    }

`Demo for cleopaws <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22id%22%3A+%22cleopaws%22%0D%0A%7D&allow=%7B%0D%0A++++%22id%22%3A+%5B%0D%0A++++++++%22simon%22%2C%0D%0A++++++++%22cleopaws%22%0D%0A++++%5D%2C%0D%0A++++%22role%22%3A+%22ops%22%0D%0A%7D>`__, `demo for ops role <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22id%22%3A+%22trevor%22%2C%0D%0A++++%22role%22%3A+%5B%0D%0A++++++++%22ops%22%2C%0D%0A++++++++%22staff%22%0D%0A++++%5D%0D%0A%7D&allow=%7B%0D%0A++++%22id%22%3A+%5B%0D%0A++++++++%22simon%22%2C%0D%0A++++++++%22cleopaws%22%0D%0A++++%5D%2C%0D%0A++++%22role%22%3A+%22ops%22%0D%0A%7D>`__, `demo for an actor matching neither rule <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22id%22%3A+%22percy%22%2C%0D%0A++++%22role%22%3A+%5B%0D%0A++++++++%22staff%22%0D%0A++++%5D%0D%0A%7D&allow=%7B%0D%0A++++%22id%22%3A+%5B%0D%0A++++++++%22simon%22%2C%0D%0A++++++++%22cleopaws%22%0D%0A++++%5D%2C%0D%0A++++%22role%22%3A+%22ops%22%0D%0A%7D>`__.

.. _AllowDebugView:

The /-/allow-debug tool
-----------------------

The ``/-/allow-debug`` tool lets you try out different  ``"action"`` blocks against different ``"actor"`` JSON objects. You can try that out here: https://latest.datasette.io/-/allow-debug

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

To deny access to all users, you can use ``"allow": false``:

.. code-block:: json

    {
        "title": "My entirely inaccessible instance",
        "allow": false
    }

One reason to do this is if you are using a Datasette plugin - such as `datasette-permissions-sql <https://github.com/simonw/datasette-permissions-sql>`__ - to control permissions instead.

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

This works for SQL views as well - you can list their names in the ``"tables"`` block above in the same way as regular tables.

.. warning::
    Restricting access to tables and views in this way will NOT prevent users from querying them using arbitrary SQL queries, `like this <https://latest.datasette.io/fixtures?sql=select+*+from+facetable>`__ for example.

    If you are restricting access to specific tables you should also use the ``"allow_sql"`` block to prevent users from bypassing the limit with their own SQL queries - see :ref:`authentication_permissions_execute_sql`.

.. _authentication_permissions_query:

Controlling access to specific canned queries
---------------------------------------------

:ref:`canned_queries` allow you to configure named SQL queries in your ``metadata.json`` that can be executed by users. These queries can be set up to both read and write to the database, so controlling who can execute them can be important.

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

.. _authentication_permissions_execute_sql:

Controlling the ability to execute arbitrary SQL
------------------------------------------------

Datasette defaults to allowing any site visitor to execute their own custom SQL queries, for example using the form on `the database page <https://latest.datasette.io/fixtures>`__ or by appending a ``?_where=`` parameter to the table page `like this <https://latest.datasette.io/fixtures/facetable?_where=_city_id=1>`__.

Access to this ability is controlled by the :ref:`permissions_execute_sql` permission.

The easiest way to disable arbitrary SQL queries is using the :ref:`default_allow_sql setting <setting_default_allow_sql>` when you first start Datasette running.

You can alternatively use an ``"allow_sql"`` block to control who is allowed to execute arbitrary SQL queries.

To prevent any user from executing arbitrary SQL queries, use this:

.. code-block:: json

    {
        "allow_sql": false
    }

To enable just the :ref:`root user<authentication_root>` to execute SQL for all databases in your instance, use the following:

.. code-block:: json

    {
        "allow_sql": {
            "id": "root"
        }
    }

To limit this ability for just one specific database, use this:

.. code-block:: json

    {
        "databases": {
            "mydatabase": {
                "allow_sql": {
                    "id": "root"
                }
            }
        }
    }

.. _permissions_plugins:

Checking permissions in plugins
===============================

Datasette plugins can check if an actor has permission to perform an action using the :ref:`datasette.permission_allowed(...)<datasette_permission_allowed>` method.

Datasette core performs a number of permission checks, :ref:`documented below <permissions>`. Plugins can implement the :ref:`plugin_hook_permission_allowed` plugin hook to participate in decisions about whether an actor should be able to perform a specified action.

.. _authentication_actor_matches_allow:

actor_matches_allow()
=====================

Plugins that wish to implement this same ``"allow"`` block permissions scheme can take advantage of the ``datasette.utils.actor_matches_allow(actor, allow)`` function:

.. code-block:: python

    from datasette.utils import actor_matches_allow

    actor_matches_allow({"id": "root"}, {"id": "*"})
    # returns True

The currently authenticated actor is made available to plugins as ``request.actor``.

.. _PermissionsDebugView:

The permissions debug tool
==========================

The debug tool at ``/-/permissions`` is only available to the :ref:`authenticated root user <authentication_root>` (or any actor granted the ``permissions-debug`` action according to a plugin).

It shows the thirty most recent permission checks that have been carried out by the Datasette instance.

This is designed to help administrators and plugin authors understand exactly how permission checks are being carried out, in order to effectively configure Datasette's permission system.

.. _authentication_ds_actor:

The ds_actor cookie
===================

Datasette includes a default authentication plugin which looks for a signed ``ds_actor`` cookie containing a JSON actor dictionary. This is how the :ref:`root actor <authentication_root>` mechanism works.

Authentication plugins can set signed ``ds_actor`` cookies themselves like so:

.. code-block:: python

    response = Response.redirect("/")
    response.set_cookie(
        "ds_actor",
        datasette.sign({"a": {"id": "cleopaws"}}, "actor"),
    )

Note that you need to pass ``"actor"`` as the namespace to :ref:`datasette_sign`.

The shape of data encoded in the cookie is as follows::

    {
        "a": {... actor ...}
    }

.. _authentication_ds_actor_expiry:

Including an expiry time
------------------------

``ds_actor`` cookies can optionally include a signed expiry timestamp, after which the cookies will no longer be valid. Authentication plugins may chose to use this mechanism to limit the lifetime of the cookie. For example, if a plugin implements single-sign-on against another source it may decide to set short-lived cookies so that if the user is removed from the SSO system their existing Datasette cookies will stop working shortly afterwards.

To include an expiry, add a ``"e"`` key to the cookie value containing a base62-encoded integer representing the timestamp when the cookie should expire. For example, here's how to set a cookie that expires after 24 hours:

.. code-block:: python

    import time
    from datasette.utils import baseconv

    expires_at = int(time.time()) + (24 * 60 * 60)

    response = Response.redirect("/")
    response.set_cookie(
        "ds_actor",
        datasette.sign(
            {
                "a": {"id": "cleopaws"},
                "e": baseconv.base62.encode(expires_at),
            },
            "actor",
        ),
    )

The resulting cookie will encode data that looks something like this:

.. code-block:: json

    {
        "a": {
            "id": "cleopaws"
        },
        "e": "1jjSji"
    }


.. _LogoutView:

The /-/logout page
------------------

The page at ``/-/logout`` provides the ability to log out of a ``ds_actor`` cookie authentication session.

.. _permissions:

Built-in permissions
====================

This section lists all of the permission checks that are carried out by Datasette core, along with the ``resource`` if it was passed.

.. _permissions_view_instance:

view-instance
-------------

Top level permission - Actor is allowed to view any pages within this instance, starting at https://latest.datasette.io/

Default *allow*.

.. _permissions_view_database:

view-database
-------------

Actor is allowed to view a database page, e.g. https://latest.datasette.io/fixtures

``resource`` - string
    The name of the database

Default *allow*.

.. _permissions_view_database_download:

view-database-download
-----------------------

Actor is allowed to download a database, e.g. https://latest.datasette.io/fixtures.db

``resource`` - string
    The name of the database

Default *allow*.

.. _permissions_view_table:

view-table
----------

Actor is allowed to view a table (or view) page, e.g. https://latest.datasette.io/fixtures/complex_foreign_keys

``resource`` - tuple: (string, string)
    The name of the database, then the name of the table

Default *allow*.

.. _permissions_view_query:

view-query
----------

Actor is allowed to view (and execute) a :ref:`canned query <canned_queries>` page, e.g. https://latest.datasette.io/fixtures/pragma_cache_size - this includes executing :ref:`canned_queries_writable`.

``resource`` - tuple: (string, string)
    The name of the database, then the name of the canned query

Default *allow*.

.. _permissions_execute_sql:

execute-sql
-----------

Actor is allowed to run arbitrary SQL queries against a specific database, e.g. https://latest.datasette.io/fixtures?sql=select+100

``resource`` - string
    The name of the database

Default *allow*. See also :ref:`the default_allow_sql setting <setting_default_allow_sql>`.

.. _permissions_permissions_debug:

permissions-debug
-----------------

Actor is allowed to view the ``/-/permissions`` debug page.

Default *deny*.

.. _permissions_debug_menu:

debug-menu
----------

Controls if the various debug pages are displayed in the navigation menu.

Default *deny*.
