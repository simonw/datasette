.. _authentication:

================================
 Authentication and permissions
================================

Datasette doesn't require authentication by default. Any visitor to a Datasette instance can explore the full data and execute read-only SQL queries.

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

    datasette --root

::

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

Datasette's built-in view permissions (``view-database``, ``view-table`` etc) default to *allow* - unless you :ref:`configure additional permission rules <authentication_permissions_config>` unauthenticated users will be allowed to access content.

Permissions with potentially harmful effects should default to *deny*. Plugin authors should account for this when designing new plugins - for example, the `datasette-upload-csvs <https://github.com/simonw/datasette-upload-csvs>`__ plugin defaults to deny so that installations don't accidentally allow unauthenticated users to create new tables by uploading a CSV file.

.. _authentication_permissions_explained:

How permissions are resolved
----------------------------

The :ref:`datasette.permission_allowed(actor, action, resource=None, default=...)<datasette_permission_allowed>` method is called to check if an actor is allowed to perform a specific action.

This method asks every plugin that implements the :ref:`plugin_hook_permission_allowed` hook if the actor is allowed to perform the action.

Each plugin can return ``True`` to indicate that the actor is allowed to perform the action, ``False`` if they are not allowed and ``None`` if the plugin has no opinion on the matter.

``False`` acts as a veto - if any plugin returns ``False`` then the permission check is denied. Otherwise, if any plugin returns ``True`` then the permission check is allowed.

The ``resource`` argument can be used to specify a specific resource that the action is being performed against. Some permissions, such as ``view-instance``, do not involve a resource. Others such as ``view-database`` have a resource that is a string naming the database. Permissions that take both a database name and the name of a table, view or canned query within that database use a resource that is a tuple of two strings, ``(database_name, resource_name)``.

Plugins that implement the ``permission_allowed()`` hook can decide if they are going to consider the provided resource or not.

.. _authentication_permissions_allow:

Defining permissions with "allow" blocks
----------------------------------------

The standard way to define permissions in Datasette is to use an ``"allow"`` block :ref:`in the datasette.yaml file <authentication_permissions_config>`. This is a JSON document describing which actors are allowed to perform a permission.

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

You can specify that only unauthenticated actors (from anynomous HTTP requests) should be allowed access using the special ``"unauthenticated": true`` key in an allow block (`allow demo <https://latest.datasette.io/-/allow-debug?actor=null&allow=%7B%0D%0A++++%22unauthenticated%22%3A+true%0D%0A%7D>`__, `deny demo <https://latest.datasette.io/-/allow-debug?actor=%7B%0D%0A++++%22id%22%3A+%22hello%22%0D%0A%7D&allow=%7B%0D%0A++++%22unauthenticated%22%3A+true%0D%0A%7D>`__):

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

If a user cannot access a specific database, they will not be able to access tables, views or queries within that database. If a user cannot access the instance they will not be able to access any of the databases, tables, views or queries.

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

Access to this ability is controlled by the :ref:`permissions_execute_sql` permission.

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

The debug tool at ``/-/permissions`` is only available to the :ref:`authenticated root user <authentication_root>` (or any actor granted the ``permissions-debug`` action).

It shows the thirty most recent permission checks that have been carried out by the Datasette instance.

It also provides an interface for running hypothetical permission checks against a hypothetical actor. This is a useful way of confirming that your configured permissions work in the way you expect.

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

.. _permissions_insert_row:

insert-row
----------

Actor is allowed to insert rows into a table.

``resource`` - tuple: (string, string)
    The name of the database, then the name of the table

Default *deny*.

.. _permissions_delete_row:

delete-row
----------

Actor is allowed to delete rows from a table.

``resource`` - tuple: (string, string)
    The name of the database, then the name of the table

Default *deny*.

.. _permissions_update_row:

update-row
----------

Actor is allowed to update rows in a table.

``resource`` - tuple: (string, string)
    The name of the database, then the name of the table

Default *deny*.

.. _permissions_create_table:

create-table
------------

Actor is allowed to create a database table.

``resource`` - string
    The name of the database

Default *deny*.

.. _permissions_alter_table:

alter-table
-----------

Actor is allowed to alter a database table.

``resource`` - tuple: (string, string)
    The name of the database, then the name of the table

Default *deny*.

.. _permissions_drop_table:

drop-table
----------

Actor is allowed to drop a database table.

``resource`` - tuple: (string, string)
    The name of the database, then the name of the table

Default *deny*.

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
