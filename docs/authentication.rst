.. _authentication:

================================
 Authentication and permissions
================================

Datasette doesn't require authentication by default. Any visitor to a Datasette instance can explore the full data and execute read-only SQL queries.

Datasette can be configured to only allow authenticated users, or to control which databases, tables, and queries can be accessed by the public or by specific users. Datasette's plugin system can be used to add many different styles of authentication, such as user accounts, single sign-on or API keys.

.. _authentication_actor:

Actors
======

Through plugins, Datasette can support both authenticated users (with cookies) and authenticated API clients (via authentication tokens). The word "actor" is used to cover both of these cases.

Every request to Datasette has an associated actor value, available in the code as ``request.actor``. This can be ``None`` for unauthenticated requests, or a JSON compatible Python dictionary for authenticated users or API clients.

The actor dictionary can be any shape - the design of that data structure is left up to the plugins. Actors should always include a unique ``"id"`` string, as demonstrated by the "root" actor below.

Plugins can use the :ref:`plugin_hook_actor_from_request` hook to implement custom logic for authenticating an actor based on the incoming HTTP request.

.. _authentication_root:

Using the "root" actor
----------------------

Datasette currently leaves almost all forms of authentication to plugins - `datasette-auth-github <https://github.com/simonw/datasette-auth-github>`__ for example.

The one exception is the "root" account, which you can sign into while using Datasette on your local machine. The root user has **all permissions** - they can perform any action regardless of other permission rules.

The ``--root`` flag is designed for local development and testing. When you start Datasette with ``--root``, the root user automatically receives every permission, including:

* All view permissions (``view-instance``, ``view-database``, ``view-table``, etc.)
* All write permissions (``insert-row``, ``update-row``, ``delete-row``, ``create-table``, ``alter-table``, ``drop-table``)
* Debug permissions (``permissions-debug``, ``debug-menu``)
* Any custom permissions defined by plugins

If you add explicit deny rules in ``datasette.yaml`` those can still block the
root actor from specific databases or tables.

The ``--root`` flag sets an internal ``root_enabled`` switch—without it, a signed-in user with ``{"id": "root"}`` is treated like any other actor.

To sign in as root, start Datasette using the ``--root`` command-line option, like this::

    datasette --root

Datasette will output a single-use-only login URL on startup::

    http://127.0.0.1:8001/-/auth-token?token=786fc524e0199d70dc9a581d851f466244e114ca92f33aa3b42a139e9388daa7
    INFO:     Started server process [25801]
    INFO:     Waiting for application startup.
    INFO:     Application startup complete.
    INFO:     Uvicorn running on http://127.0.0.1:8001 (Press CTRL+C to quit)

Click on that link and then visit ``http://127.0.0.1:8001/-/actor`` to confirm that you are authenticated as an actor that looks like this:

.. code-block:: json

    {
        "id": "root"
    }

.. _authentication_default_deny:

Denying all permissions by default
----------------------------------

By default, Datasette allows unauthenticated access to view databases, tables, and execute SQL queries. This makes it easy to explore data without needing to set up authentication.

However, you may want to run Datasette in a mode where **all** access is denied by default, and you explicitly grant permissions only to authenticated users. This is useful for:

* Running a locked-down Datasette instance that requires authentication for all access
* Building applications where you control access through authentication plugins
* Deploying Datasette in environments where data should never be publicly accessible

To enable this mode, use the ``--default-deny`` command-line option::

    datasette --default-deny data.db --root

With ``--default-deny`` enabled:

* Anonymous users are denied access to view the instance, databases, tables, and queries
* Authenticated users are also denied access unless they're explicitly granted permissions
* The root user (when using ``--root``) still has access to everything
* You can grant permissions using :ref:`configuration file rules <authentication_permissions_config>` or plugins

For example, to allow only a specific user to access your instance::

    datasette --default-deny data.db --config datasette.yaml

Where ``datasette.yaml`` contains:

.. code-block:: yaml

    allow:
      id: alice

This configuration will deny access to everyone except the user with ``id`` of ``alice``.

.. _authentication_permissions:

Permissions
===========

Datasette's permissions system is built around SQL queries. Datasette and its plugins construct SQL queries to resolve the list of resources that an actor cas access.

The key question the permissions system answers is this:

    Is this **actor** allowed to perform this **action**, optionally against this particular **resource**?

**Actors** are :ref:`described above <authentication_actor>`.

An **action** is a string describing the action the actor would like to perform. A full list is :ref:`provided below <actions>` - examples include ``view-table`` and ``execute-sql``.

A **resource** is the item the actor wishes to interact with - for example a specific database or table. Some actions, such as ``permissions-debug``, are not associated with a particular resource.

Datasette's built-in view actions (``view-database``, ``view-table`` etc) are allowed by Datasette's default configuration: unless you :ref:`configure additional permission rules <authentication_permissions_config>` unauthenticated users will be allowed to access content.

Other actions, including those introduced by plugins, will default to *deny*.

.. _authentication_permissions_explained:

How permissions are resolved
----------------------------

Datasette performs permission checks using the internal :ref:`datasette_allowed`, method which accepts keyword arguments for ``action``, ``resource`` and an optional ``actor``. 

``resource`` should be an instance of the appropriate ``Resource`` subclass from :mod:`datasette.resources`—for example ``InstanceResource()``, ``DatabaseResource(database="...``)`` or ``TableResource(database="...", table="...")``. This defaults to ``InstanceResource()`` if not specified.

When a check runs Datasette gathers allow/deny rules from multiple sources and
compiles them into a SQL query. The resulting query describes all of the
resources an actor may access for that action, together with the reasons those
resources were allowed or denied. The combined sources are:

* ``allow`` blocks configured in :ref:`datasette.yaml <authentication_permissions_config>`.
* :ref:`Actor restrictions <authentication_cli_create_token_restrict>` encoded into the actor dictionary or API token.
* The "root" user shortcut when ``--root`` (or :attr:`Datasette.root_enabled <datasette.app.Datasette.root_enabled>`) is active, replying ``True`` to all permission chucks unless configuration rules deny them at a more specific level.
* Any additional SQL provided by plugins implementing :ref:`plugin_hook_permission_resources_sql`.

Datasette evaluates the SQL to determine if the requested ``resource`` is
included. Explicit deny rules returned by configuration or plugins will block
access even if other rules allowed it.

.. _authentication_permissions_allow:

Defining permissions with "allow" blocks
----------------------------------------

One way to define permissions in Datasette is to use an ``"allow"`` block :ref:`in the datasette.yaml file <authentication_permissions_config>`. This is a JSON document describing which actors are allowed to perform an action against a specific resource.

Each ``allow`` block is compiled into SQL and combined with any
:ref:`plugin-provided rules <plugin_hook_permission_resources_sql>` to produce
the cascading allow/deny decisions that power :ref:`datasette_allowed`.

The most basic form of allow block is this (`allow demo <https://latest.datasette.io/-/allow-debug?actor=%7B%22id%22%3A+%22root%22%7D&allow=%7B%0D%0A++++++++%22id%22%3A+%22root%22%0D%0A++++%7D>`__, `deny demo <https://latest.datasette.io/-/allow-debug?actor=%7B%22id%22%3A+%22trevor%22%7D&allow=%7B%0D%0A++++++++%22id%22%3A+%22root%22%0D%0A++++%7D>`__):

.. [[[cog
    from metadata_doc import config_example
    import textwrap
    config_example(cog, textwrap.dedent(
      """
        allow:
          id: root
        """).strip(),
        "YAML", "JSON"
      )
.. ]]]

.. tab:: YAML

    .. code-block:: yaml

        allow:
          id: root

.. tab:: JSON

    .. code-block:: json

        {
          "allow": {
            "id": "root"
          }
        }
.. [[[end]]]

This will match any actors with an ``"id"`` property of ``"root"`` - for example, an actor that looks like this:

.. code-block:: json

    {
        "id": "root",
        "name": "Root User"
    }

An allow block can specify "deny all" using ``false`` (`demo <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22id%22%3A+%22root%22%0D%0A%7D&allow=false>`__):

.. [[[cog
    from metadata_doc import config_example
    import textwrap
    config_example(cog, textwrap.dedent(
      """
        allow: false
        """).strip(),
        "YAML", "JSON"
      )
.. ]]]

.. tab:: YAML

    .. code-block:: yaml

        allow: false

.. tab:: JSON

    .. code-block:: json

        {
          "allow": false
        }
.. [[[end]]]

An ``"allow"`` of ``true`` allows all access (`demo <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22id%22%3A+%22root%22%0D%0A%7D&allow=true>`__):

.. [[[cog
    from metadata_doc import config_example
    import textwrap
    config_example(cog, textwrap.dedent(
      """
        allow: true
        """).strip(),
        "YAML", "JSON"
      )
.. ]]]

.. tab:: YAML

    .. code-block:: yaml

        allow: true

.. tab:: JSON

    .. code-block:: json

        {
          "allow": true
        }
.. [[[end]]]

Allow keys can provide a list of values. These will match any actor that has any of those values (`allow demo <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22id%22%3A+%22cleopaws%22%0D%0A%7D&allow=%7B%0D%0A++++%22id%22%3A+%5B%0D%0A++++++++%22simon%22%2C%0D%0A++++++++%22cleopaws%22%0D%0A++++%5D%0D%0A%7D>`__, `deny demo <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22id%22%3A+%22pancakes%22%0D%0A%7D&allow=%7B%0D%0A++++%22id%22%3A+%5B%0D%0A++++++++%22simon%22%2C%0D%0A++++++++%22cleopaws%22%0D%0A++++%5D%0D%0A%7D>`__):

.. [[[cog
    from metadata_doc import config_example
    import textwrap
    config_example(cog, textwrap.dedent(
      """
        allow:
          id:
          - simon
          - cleopaws
        """).strip(),
        "YAML", "JSON"
      )
.. ]]]

.. tab:: YAML

    .. code-block:: yaml

        allow:
          id:
          - simon
          - cleopaws

.. tab:: JSON

    .. code-block:: json

        {
          "allow": {
            "id": [
              "simon",
              "cleopaws"
            ]
          }
        }
.. [[[end]]]

This will match any actor with an ``"id"`` of either ``"simon"`` or ``"cleopaws"``.

Actors can have properties that feature a list of values. These will be matched against the list of values in an allow block. Consider the following actor:

.. code-block:: json

      {
          "id": "simon",
          "roles": ["staff", "developer"]
      }

This allow block will provide access to any actor that has ``"developer"`` as one of their roles (`allow demo <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22id%22%3A+%22simon%22%2C%0D%0A++++%22roles%22%3A+%5B%0D%0A++++++++%22staff%22%2C%0D%0A++++++++%22developer%22%0D%0A++++%5D%0D%0A%7D&allow=%7B%0D%0A++++%22roles%22%3A+%5B%0D%0A++++++++%22developer%22%0D%0A++++%5D%0D%0A%7D>`__, `deny demo <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22id%22%3A+%22cleopaws%22%2C%0D%0A++++%22roles%22%3A+%5B%22dog%22%5D%0D%0A%7D&allow=%7B%0D%0A++++%22roles%22%3A+%5B%0D%0A++++++++%22developer%22%0D%0A++++%5D%0D%0A%7D>`__):

.. [[[cog
    from metadata_doc import config_example
    import textwrap
    config_example(cog, textwrap.dedent(
      """
        allow:
          roles:
          - developer
        """).strip(),
        "YAML", "JSON"
      )
.. ]]]

.. tab:: YAML

    .. code-block:: yaml

        allow:
          roles:
          - developer

.. tab:: JSON

    .. code-block:: json

        {
          "allow": {
            "roles": [
              "developer"
            ]
          }
        }
.. [[[end]]]

Note that "roles" is not a concept that is baked into Datasette - it's a convention that plugins can choose to implement and act on.

If you want to provide access to any actor with a value for a specific key, use ``"*"``. For example, to match any logged-in user specify the following (`allow demo <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22id%22%3A+%22simon%22%0D%0A%7D&allow=%7B%0D%0A++++%22id%22%3A+%22*%22%0D%0A%7D>`__, `deny demo <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22bot%22%3A+%22readme-bot%22%0D%0A%7D&allow=%7B%0D%0A++++%22id%22%3A+%22*%22%0D%0A%7D>`__):

.. [[[cog
    from metadata_doc import config_example
    import textwrap
    config_example(cog, textwrap.dedent(
      """
        allow:
          id: "*"
        """).strip(),
        "YAML", "JSON"
      )
.. ]]]

.. tab:: YAML

    .. code-block:: yaml

        allow:
          id: "*"

.. tab:: JSON

    .. code-block:: json

        {
          "allow": {
            "id": "*"
          }
        }
.. [[[end]]]

You can specify that only unauthenticated actors (from anonymous HTTP requests) should be allowed access using the special ``"unauthenticated": true`` key in an allow block (`allow demo <https://latest.datasette.io/-/allow-debug?actor=null&allow=%7B%0D%0A++++%22unauthenticated%22%3A+true%0D%0A%7D>`__, `deny demo <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22id%22%3A+%22hello%22%0D%0A%7D&allow=%7B%0D%0A++++%22unauthenticated%22%3A+true%0D%0A%7D>`__):

.. [[[cog
    from metadata_doc import config_example
    import textwrap
    config_example(cog, textwrap.dedent(
      """
        allow:
          unauthenticated: true
        """).strip(),
        "YAML", "JSON"
      )
.. ]]]

.. tab:: YAML

    .. code-block:: yaml

        allow:
          unauthenticated: true

.. tab:: JSON

    .. code-block:: json

        {
          "allow": {
            "unauthenticated": true
          }
        }
.. [[[end]]]

Allow keys act as an "or" mechanism. An actor will be able to execute the query if any of their JSON properties match any of the values in the corresponding lists in the ``allow`` block. The following block will allow users with either a ``role`` of ``"ops"`` OR users who have an ``id`` of ``"simon"`` or ``"cleopaws"``:

.. [[[cog
    from metadata_doc import config_example
    import textwrap
    config_example(cog, textwrap.dedent(
      """
        allow:
          id:
          - simon
          - cleopaws
          role: ops
        """).strip(),
        "YAML", "JSON"
      )
.. ]]]

.. tab:: YAML

    .. code-block:: yaml

        allow:
          id:
          - simon
          - cleopaws
          role: ops

.. tab:: JSON

    .. code-block:: json

        {
          "allow": {
            "id": [
              "simon",
              "cleopaws"
            ],
            "role": "ops"
          }
        }
.. [[[end]]]

`Demo for cleopaws <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22id%22%3A+%22cleopaws%22%0D%0A%7D&allow=%7B%0D%0A++++%22id%22%3A+%5B%0D%0A++++++++%22simon%22%2C%0D%0A++++++++%22cleopaws%22%0D%0A++++%5D%2C%0D%0A++++%22role%22%3A+%22ops%22%0D%0A%7D>`__, `demo for ops role <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22id%22%3A+%22trevor%22%2C%0D%0A++++%22role%22%3A+%5B%0D%0A++++++++%22ops%22%2C%0D%0A++++++++%22staff%22%0D%0A++++%5D%0D%0A%7D&allow=%7B%0D%0A++++%22id%22%3A+%5B%0D%0A++++++++%22simon%22%2C%0D%0A++++++++%22cleopaws%22%0D%0A++++%5D%2C%0D%0A++++%22role%22%3A+%22ops%22%0D%0A%7D>`__, `demo for an actor matching neither rule <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22id%22%3A+%22percy%22%2C%0D%0A++++%22role%22%3A+%5B%0D%0A++++++++%22staff%22%0D%0A++++%5D%0D%0A%7D&allow=%7B%0D%0A++++%22id%22%3A+%5B%0D%0A++++++++%22simon%22%2C%0D%0A++++++++%22cleopaws%22%0D%0A++++%5D%2C%0D%0A++++%22role%22%3A+%22ops%22%0D%0A%7D>`__.

.. _AllowDebugView:

The /-/allow-debug tool
-----------------------

The ``/-/allow-debug`` tool lets you try out different  ``"action"`` blocks against different ``"actor"`` JSON objects. You can try that out here: https://latest.datasette.io/-/allow-debug

.. _authentication_permissions_config:

Access permissions in ``datasette.yaml``
========================================

There are two ways to configure permissions using ``datasette.yaml`` (or ``datasette.json``).

For simple visibility permissions you can use ``"allow"`` blocks in the root, database, table and query sections.

For other permissions you can use a ``"permissions"`` block, described :ref:`in the next section <authentication_permissions_other>`.

You can limit who is allowed to view different parts of your Datasette instance using ``"allow"`` keys in your :ref:`configuration`.

You can control the following:

* Access to the entire Datasette instance
* Access to specific databases
* Access to specific tables and views
* Access to specific :ref:`canned_queries`

If a user has permission to view a table they will be able to view that table, independent of if they have permission to view the database or instance that the table exists within.

.. _authentication_permissions_instance:

Access to an instance
---------------------

Here's how to restrict access to your entire Datasette instance to just the ``"id": "root"`` user:

.. [[[cog
    from metadata_doc import config_example
    config_example(cog, """
        title: My private Datasette instance
        allow:
          id: root
      """)
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml


            title: My private Datasette instance
            allow:
              id: root
  

.. tab:: datasette.json

    .. code-block:: json

        {
          "title": "My private Datasette instance",
          "allow": {
            "id": "root"
          }
        }
.. [[[end]]]

To deny access to all users, you can use ``"allow": false``:

.. [[[cog
    config_example(cog, """
        title: My entirely inaccessible instance
        allow: false
    """)
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml


            title: My entirely inaccessible instance
            allow: false


.. tab:: datasette.json

    .. code-block:: json

        {
          "title": "My entirely inaccessible instance",
          "allow": false
        }
.. [[[end]]]

One reason to do this is if you are using a Datasette plugin - such as `datasette-permissions-sql <https://github.com/simonw/datasette-permissions-sql>`__ - to control permissions instead.

.. _authentication_permissions_database:

Access to specific databases
----------------------------

To limit access to a specific ``private.db`` database to just authenticated users, use the ``"allow"`` block like this:

.. [[[cog
    config_example(cog, """
        databases:
          private:
            allow:
              id: "*"
    """)
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml


            databases:
              private:
                allow:
                  id: "*"


.. tab:: datasette.json

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
.. [[[end]]]

.. _authentication_permissions_table:

Access to specific tables and views
-----------------------------------

To limit access to the ``users`` table in your ``bakery.db`` database:

.. [[[cog
    config_example(cog, """
        databases:
          bakery:
            tables:
              users:
                allow:
                  id: '*'
    """)
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml


            databases:
              bakery:
                tables:
                  users:
                    allow:
                      id: '*'


.. tab:: datasette.json

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
.. [[[end]]]

This works for SQL views as well - you can list their names in the ``"tables"`` block above in the same way as regular tables.

.. warning::
    Restricting access to tables and views in this way will NOT prevent users from querying them using arbitrary SQL queries, `like this <https://latest.datasette.io/fixtures?sql=select+*+from+facetable>`__ for example.

    If you are restricting access to specific tables you should also use the ``"allow_sql"`` block to prevent users from bypassing the limit with their own SQL queries - see :ref:`authentication_permissions_execute_sql`.

.. _authentication_permissions_query:

Access to specific canned queries
---------------------------------

:ref:`canned_queries` allow you to configure named SQL queries in your ``datasette.yaml`` that can be executed by users. These queries can be set up to both read and write to the database, so controlling who can execute them can be important.

To limit access to the ``add_name`` canned query in your ``dogs.db`` database to just the :ref:`root user<authentication_root>`:

.. [[[cog
    config_example(cog, """
        databases:
          dogs:
            queries:
              add_name:
                sql: INSERT INTO names (name) VALUES (:name)
                write: true
                allow:
                  id:
                  - root
    """)
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml


            databases:
              dogs:
                queries:
                  add_name:
                    sql: INSERT INTO names (name) VALUES (:name)
                    write: true
                    allow:
                      id:
                      - root


.. tab:: datasette.json

    .. code-block:: json

        {
          "databases": {
            "dogs": {
              "queries": {
                "add_name": {
                  "sql": "INSERT INTO names (name) VALUES (:name)",
                  "write": true,
                  "allow": {
                    "id": [
                      "root"
                    ]
                  }
                }
              }
            }
          }
        }
.. [[[end]]]

.. _authentication_permissions_execute_sql:

Controlling the ability to execute arbitrary SQL
------------------------------------------------

Datasette defaults to allowing any site visitor to execute their own custom SQL queries, for example using the form on `the database page <https://latest.datasette.io/fixtures>`__ or by appending a ``?_where=`` parameter to the table page `like this <https://latest.datasette.io/fixtures/facetable?_where=_city_id=1>`__.

Access to this ability is controlled by the :ref:`actions_execute_sql` permission.

The easiest way to disable arbitrary SQL queries is using the :ref:`default_allow_sql setting <setting_default_allow_sql>` when you first start Datasette running.

You can alternatively use an ``"allow_sql"`` block to control who is allowed to execute arbitrary SQL queries.

To prevent any user from executing arbitrary SQL queries, use this:

.. [[[cog
    config_example(cog, """
        allow_sql: false
    """)
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml


            allow_sql: false


.. tab:: datasette.json

    .. code-block:: json

        {
          "allow_sql": false
        }
.. [[[end]]]

To enable just the :ref:`root user<authentication_root>` to execute SQL for all databases in your instance, use the following:

.. [[[cog
    config_example(cog, """
        allow_sql:
          id: root
    """)
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml


            allow_sql:
              id: root


.. tab:: datasette.json

    .. code-block:: json

        {
          "allow_sql": {
            "id": "root"
          }
        }
.. [[[end]]]

To limit this ability for just one specific database, use this:

.. [[[cog
    config_example(cog, """
        databases:
          mydatabase:
            allow_sql:
              id: root
    """)
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml


            databases:
              mydatabase:
                allow_sql:
                  id: root


.. tab:: datasette.json

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
.. [[[end]]]

.. _authentication_permissions_other:

Other permissions in ``datasette.yaml``
=======================================

For all other permissions, you can use one or more ``"permissions"`` blocks in your ``datasette.yaml`` configuration file.

To grant access to the :ref:`permissions debug tool <PermissionsDebugView>` to all signed in users, you can grant ``permissions-debug`` to any actor with an ``id`` matching the wildcard ``*`` by adding this a the root of your configuration:

.. [[[cog
    config_example(cog, """
        permissions:
          debug-menu:
            id: '*'
    """)
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml


            permissions:
              debug-menu:
                id: '*'


.. tab:: datasette.json

    .. code-block:: json

        {
          "permissions": {
            "debug-menu": {
              "id": "*"
            }
          }
        }
.. [[[end]]]

To grant ``create-table`` to the user with ``id`` of ``editor`` for the ``docs`` database:

.. [[[cog
    config_example(cog, """
        databases:
          docs:
            permissions:
              create-table:
                id: editor
    """)
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml


            databases:
              docs:
                permissions:
                  create-table:
                    id: editor


.. tab:: datasette.json

    .. code-block:: json

        {
          "databases": {
            "docs": {
              "permissions": {
                "create-table": {
                  "id": "editor"
                }
              }
            }
          }
        }
.. [[[end]]]

And for ``insert-row`` against the ``reports`` table in that ``docs`` database:

.. [[[cog
    config_example(cog, """
        databases:
          docs:
            tables:
              reports:
                permissions:
                  insert-row:
                    id: editor
    """)
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml


            databases:
              docs:
                tables:
                  reports:
                    permissions:
                      insert-row:
                        id: editor


.. tab:: datasette.json

    .. code-block:: json

        {
          "databases": {
            "docs": {
              "tables": {
                "reports": {
                  "permissions": {
                    "insert-row": {
                      "id": "editor"
                    }
                  }
                }
              }
            }
          }
        }
.. [[[end]]]

The :ref:`permissions debug tool <PermissionsDebugView>` can be useful for helping test permissions that you have configured in this way.

.. _CreateTokenView:

API Tokens
==========

Datasette includes a default mechanism for generating API tokens that can be used to authenticate requests.

Authenticated users can create new API tokens using a form on the ``/-/create-token`` page.

Tokens created in this way can be further restricted to only allow access to specific actions, or to limit those actions to specific databases, tables or queries.

Created tokens can then be passed in the ``Authorization: Bearer $token`` header of HTTP requests to Datasette.

A token created by a user will include that user's ``"id"`` in the token payload, so any permissions granted to that user based on their ID can be made available to the token as well.

When one of these a token accompanies a request, the actor for that request will have the following shape:

.. code-block:: json

    {
        "id": "user_id",
        "token": "dstok",
        "token_expires": 1667717426
    }

The ``"id"`` field duplicates the ID of the actor who first created the token.

The ``"token"`` field identifies that this actor was authenticated using a Datasette signed token (``dstok``).

The ``"token_expires"`` field, if present, indicates that the token will expire after that integer timestamp.

The ``/-/create-token`` page cannot be accessed by actors that are authenticated with a ``"token": "some-value"`` property. This is to prevent API tokens from being used to create more tokens.

Datasette plugins that implement their own form of API token authentication should follow this convention.

You can disable the signed token feature entirely using the :ref:`allow_signed_tokens <setting_allow_signed_tokens>` setting.

.. _authentication_cli_create_token:

datasette create-token
----------------------

You can also create tokens on the command line using the ``datasette create-token`` command.

This command takes one required argument - the ID of the actor to be associated with the created token.

You can specify a ``-e/--expires-after`` option in seconds. If omitted, the token will never expire.

The command will sign the token using the ``DATASETTE_SECRET`` environment variable, if available. You can also pass the secret using the ``--secret`` option.

This means you can run the command locally to create tokens for use with a deployed Datasette instance, provided you know that instance's secret.

To create a token for the ``root`` actor that will expire in one hour::

    datasette create-token root --expires-after 3600

To create a token that never expires using a specific secret::

    datasette create-token root --secret my-secret-goes-here

.. _authentication_cli_create_token_restrict:

Restricting the actions that a token can perform
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Tokens created using ``datasette create-token ACTOR_ID`` will inherit all of the permissions of the actor that they are associated with.

You can pass additional options to create tokens that are restricted to a subset of that actor's permissions.

To restrict the token to just specific permissions against all available databases, use the ``--all`` option::

    datasette create-token root --all insert-row --all update-row

This option can be passed as many times as you like. In the above example the token will only be allowed to insert and update rows.

You can also restrict permissions such that they can only be used within specific databases::

    datasette create-token root --database mydatabase insert-row

The resulting token will only be able to insert rows, and only to tables in the ``mydatabase`` database.

Finally, you can restrict permissions to individual resources - tables, SQL views and :ref:`named queries <canned_queries>` - within a specific database::

    datasette create-token root --resource mydatabase mytable insert-row

These options have short versions: ``-a`` for ``--all``, ``-d`` for ``--database`` and ``-r`` for ``--resource``.

You can add ``--debug`` to see a JSON representation of the token that has been created. Here's a full example::

    datasette create-token root \
        --secret mysecret \
        --all view-instance \
        --all view-table \
        --database docs view-query \
        --resource docs documents insert-row \
        --resource docs documents update-row \
        --debug

This example outputs the following::

    dstok_.eJxFizEKgDAMRe_y5w4qYrFXERGxDkVsMI0uxbubdjFL8l_ez1jhwEQCA6Fjjxp90qtkuHawzdjYrh8MFobLxZ_wBH0_gtnAF-hpS5VfmF8D_lnd97lHqUJgLd6sls4H1qwlhA.nH_7RecYHj5qSzvjhMU95iy0Xlc

    Decoded:

    {
      "a": "root",
      "token": "dstok",
      "t": 1670907246,
      "_r": {
        "a": [
          "vi",
          "vt"
        ],
        "d": {
          "docs": [
            "vq"
          ]
        },
        "r": {
          "docs": {
            "documents": [
              "ir",
              "ur"
            ]
          }
        }
      }
    }

Restrictions act as an allowlist layered on top of the actor's existing
permissions. They can only remove access the actor would otherwise have—they
cannot grant new access. If the underlying actor is denied by ``allow`` rules in
``datasette.yaml`` or by a plugin, a token that lists that resource in its
``"_r"`` section will still be denied.


.. _permissions_plugins:

Checking permissions in plugins
===============================

Datasette plugins can check if an actor has permission to perform an action using :ref:`datasette_allowed`—for example::

    from datasette.resources import TableResource

    can_edit = await datasette.allowed(
        action="update-row",
        resource=TableResource(database="fixtures", table="facetable"),
        actor=request.actor,
    )

Use :ref:`datasette_ensure_permission` when you need to enforce a permission and
raise a ``Forbidden`` error automatically.

Plugins that define new operations should return :class:`~datasette.permissions.Action`
objects from :ref:`plugin_register_actions` and can supply additional allow/deny
rules by returning :class:`~datasette.permissions.PermissionSQL` objects from the
:ref:`plugin_hook_permission_resources_sql` hook. Those rules are merged with
configuration ``allow`` blocks and actor restrictions to determine the final
result for each check.

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

Permissions debug tools
=======================

The debug tool at ``/-/permissions`` is available to any actor with the ``permissions-debug`` permission. By default this is just the :ref:`authenticated root user <authentication_root>` but you can open it up to all users by starting Datasette like this::

    datasette -s permissions.permissions-debug true data.db

The page shows the permission checks that have been carried out by the Datasette instance.

It also provides an interface for running hypothetical permission checks against a hypothetical actor. This is a useful way of confirming that your configured permissions work in the way you expect.

This is designed to help administrators and plugin authors understand exactly how permission checks are being carried out, in order to effectively configure Datasette's permission system.

.. _AllowedResourcesView:

Allowed resources view
----------------------

The ``/-/allowed`` endpoint displays resources that the current actor can access for a specified ``action``.

This endpoint provides an interactive HTML form interface. Add ``.json`` to the URL path (e.g. ``/-/allowed.json``) to get the raw JSON response instead.

Pass ``?action=view-table`` (or another action) to select the action. Optional ``parent=`` and ``child=`` query parameters can narrow the results to a specific database/table pair.

This endpoint is publicly accessible to help users understand their own permissions. The potentially sensitive ``reason`` field is only shown to users with the ``permissions-debug`` permission - it shows the plugins and explanatory reasons that were responsible for each decision.

.. _PermissionRulesView:

Permission rules view
---------------------

The ``/-/rules`` endpoint displays all permission rules (both allow and deny) for each candidate resource for the requested action.

This endpoint provides an interactive HTML form interface. Add ``.json`` to the URL path (e.g. ``/-/rules.json?action=view-table``) to get the raw JSON response instead.

Pass ``?action=`` as a query parameter to specify which action to check.

This endpoint requires the ``permissions-debug`` permission.

.. _PermissionCheckView:

Permission check view
---------------------

The ``/-/check`` endpoint evaluates a single action/resource pair and returns information indicating whether the access was allowed along with diagnostic information.

This endpoint provides an interactive HTML form interface. Add ``.json`` to the URL path (e.g. ``/-/check.json?action=view-instance``) to get the raw JSON response instead.

Pass ``?action=`` to specify the action to check, and optional ``?parent=`` and ``?child=`` parameters to specify the resource.

.. _authentication_ds_actor:

The ds_actor cookie
===================

Datasette includes a default authentication plugin which looks for a signed ``ds_actor`` cookie containing a JSON actor dictionary. This is how the :ref:`root actor <authentication_root>` mechanism works.

Authentication plugins can set signed ``ds_actor`` cookies themselves like so:

.. code-block:: python

    response = Response.redirect("/")
    datasette.set_actor_cookie(response, {"id": "cleopaws"})

The shape of data encoded in the cookie is as follows:

.. code-block:: json

    {
      "a": {
        "id": "cleopaws"
      }
    }

To implement logout in a plugin, use the ``delete_actor_cookie()`` method:

.. code-block:: python

    response = Response.redirect("/")
    datasette.delete_actor_cookie(response)

.. _authentication_ds_actor_expiry:

Including an expiry time
------------------------

``ds_actor`` cookies can optionally include a signed expiry timestamp, after which the cookies will no longer be valid. Authentication plugins may chose to use this mechanism to limit the lifetime of the cookie. For example, if a plugin implements single-sign-on against another source it may decide to set short-lived cookies so that if the user is removed from the SSO system their existing Datasette cookies will stop working shortly afterwards.

To include an expiry pass ``expire_after=`` to ``datasette.set_actor_cookie()`` with a number of seconds. For example, to expire in 24 hours:

.. code-block:: python

    response = Response.redirect("/")
    datasette.set_actor_cookie(
        response, {"id": "cleopaws"}, expire_after=60 * 60 * 24
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

.. _actions:

Built-in actions
================

This section lists all of the permission checks that are carried out by Datasette core, along with the ``resource`` if it was passed.

.. _actions_view_instance:

view-instance
-------------

Top level permission - Actor is allowed to view any pages within this instance, starting at https://latest.datasette.io/

.. _actions_view_database:

view-database
-------------

Actor is allowed to view a database page, e.g. https://latest.datasette.io/fixtures

``resource`` - ``datasette.permissions.DatabaseResource(database)``
    ``database`` is the name of the database (string)

.. _actions_view_database_download:

view-database-download
----------------------

Actor is allowed to download a database, e.g. https://latest.datasette.io/fixtures.db

``resource`` - ``datasette.resources.DatabaseResource(database)``
    ``database`` is the name of the database (string)

.. _actions_view_table:

view-table
----------

Actor is allowed to view a table (or view) page, e.g. https://latest.datasette.io/fixtures/complex_foreign_keys

``resource`` - ``datasette.resources.TableResource(database, table)``
    ``database`` is the name of the database (string)

    ``table`` is the name of the table (string)

.. _actions_view_query:

view-query
----------

Actor is allowed to view (and execute) a :ref:`canned query <canned_queries>` page, e.g. https://latest.datasette.io/fixtures/pragma_cache_size - this includes executing :ref:`canned_queries_writable`.

``resource`` - ``datasette.resources.QueryResource(database, query)``
    ``database`` is the name of the database (string)
    
    ``query`` is the name of the canned query (string)

.. _actions_insert_row:

insert-row
----------

Actor is allowed to insert rows into a table.

``resource`` - ``datasette.resources.TableResource(database, table)``
    ``database`` is the name of the database (string)

    ``table`` is the name of the table (string)

.. _actions_delete_row:

delete-row
----------

Actor is allowed to delete rows from a table.

``resource`` - ``datasette.resources.TableResource(database, table)``
    ``database`` is the name of the database (string)

    ``table`` is the name of the table (string)

.. _actions_update_row:

update-row
----------

Actor is allowed to update rows in a table.

``resource`` - ``datasette.resources.TableResource(database, table)``
    ``database`` is the name of the database (string)

    ``table`` is the name of the table (string)

.. _actions_create_table:

create-table
------------

Actor is allowed to create a database table.

``resource`` - ``datasette.resources.DatabaseResource(database)``
    ``database`` is the name of the database (string)

.. _actions_alter_table:

alter-table
-----------

Actor is allowed to alter a database table.

``resource`` - ``datasette.resources.TableResource(database, table)``
    ``database`` is the name of the database (string)

    ``table`` is the name of the table (string)

.. _actions_drop_table:

drop-table
----------

Actor is allowed to drop a database table.

``resource`` - ``datasette.resources.TableResource(database, table)``
    ``database`` is the name of the database (string)

    ``table`` is the name of the table (string)

.. _actions_execute_sql:

execute-sql
-----------

Actor is allowed to run arbitrary SQL queries against a specific database, e.g. https://latest.datasette.io/fixtures/-/query?sql=select+100

``resource`` - ``datasette.resources.DatabaseResource(database)``
    ``database`` is the name of the database (string)

See also :ref:`the default_allow_sql setting <setting_default_allow_sql>`.

.. _actions_permissions_debug:

permissions-debug
-----------------

Actor is allowed to view the ``/-/permissions`` debug tools.

.. _actions_debug_menu:

debug-menu
----------

Controls if the various debug pages are displayed in the navigation menu.
