.. _changelog:

=========
Changelog
=========

.. _v0_49a1:

0.49a1 (2020-09-13)
-------------------

.. warning:: This is an **alpha** release. See :ref:`contributing_alpha_beta`.

- ``register_output_renderer()`` render functions can now return a ``Response``. (`#953 <https://github.com/simonw/datasette/issues/953>`__)
- New ``--upgrade`` option for ``datasette install``. (`#945 <https://github.com/simonw/datasette/issues/945>`__)
- ``datasette publish heroku`` now deploys using Python 3.8.5
- Upgraded `CodeMirror <https://codemirror.net/>`__ to 5.57.0. (`#948 <https://github.com/simonw/datasette/issues/948>`__)
- Upgraded code style to Black 20.8b1. (`#958 <https://github.com/simonw/datasette/issues/958>`__)
- New ``datasette --pdb`` option. (`#962 <https://github.com/simonw/datasette/issues/962>`__)
- ``datasette --get`` exit code now reflects the internal HTTP status code. (`#947 <https://github.com/simonw/datasette/issues/947>`__)
- Fixed bug where selected facets were not correctly persisted in hidden form fields on the table page. (`#963 <https://github.com/simonw/datasette/issues/963>`__)
- New mechanism for defining page templates with custom path parameters. (`#944 <https://github.com/simonw/datasette/issues/944>`__)

.. _v0_48:

0.48 (2020-08-16)
-----------------

- Datasette documentation now lives at `docs.datasette.io <https://docs.datasette.io/>`__.
- ``db.is_mutable`` property is now documented and tested, see :ref:`internals_database_introspection`.
- The ``extra_template_vars``, ``extra_css_urls``, ``extra_js_urls`` and ``extra_body_script`` plugin hooks now all accept the same arguments. See :ref:`plugin_hook_extra_template_vars` for details. (`#939 <https://github.com/simonw/datasette/issues/939>`__)
- Those hooks now accept a new ``columns`` argument detailing the table columns that will be rendered on that page. (`#938 <https://github.com/simonw/datasette/issues/938>`__)
- Fixed bug where plugins calling ``db.execute_write_fn()`` could hang Datasette if the connection failed. (`#935 <https://github.com/simonw/datasette/issues/935>`__)
- Fixed bug with the ``?_nl=on`` output option and binary data. (`#914 <https://github.com/simonw/datasette/issues/914>`__)

.. _v0_47_3:

0.47.3 (2020-08-15)
-------------------

- The ``datasette --get`` command-line mechanism now ensures any plugins using the ``startup()`` hook are correctly executed. (`#934 <https://github.com/simonw/datasette/issues/934>`__)

.. _v0_47_2:

0.47.2 (2020-08-12)
-------------------

- Fixed an issue with the Docker image `published to Docker Hub <https://hub.docker.com/r/datasetteproject/datasette>`__. (`#931 <https://github.com/simonw/datasette/issues/931>`__)

.. _v0_47_1:

0.47.1 (2020-08-11)
-------------------

- Fixed a bug where the ``sdist`` distribution of Datasette was not correctly including the template files. (`#930 <https://github.com/simonw/datasette/issues/930>`__)

.. _v0_47:

0.47 (2020-08-11)
-----------------

- Datasette now has `a GitHub discussions forum <https://github.com/simonw/datasette/discussions>`__ for conversations about the project that go beyond just bug reports and issues.
- Datasette can now be installed on macOS using Homebrew! Run ``brew install simonw/datasette/datasette``. See :ref:`installation_homebrew`. (`#335 <https://github.com/simonw/datasette/issues/335>`__)
- Two new commands: ``datasette install name-of-plugin`` and ``datasette uninstall name-of-plugin``. These are equivalent to ``pip install`` and ``pip uninstall`` but automatically run in the same virtual environment as Datasette, so users don't have to figure out where that virtual environment is - useful for installations created using Homebrew or ``pipx``. See :ref:`plugins_installing`. (`#925 <https://github.com/simonw/datasette/issues/925>`__)
- A new command-line option, ``datasette --get``, accepts a path to a URL within the Datasette instance. It will run that request through Datasette (without starting a web server) and print out the repsonse. See :ref:`getting_started_datasette_get` for an example. (`#926 <https://github.com/simonw/datasette/issues/926>`__)

.. _v0_46:

0.46 (2020-08-09)
-----------------

.. warning::
    This release contains a security fix related to authenticated writable canned queries. If you are using this feature you should upgrade as soon as possible.

- **Security fix:** CSRF tokens were incorrectly included in read-only canned query forms, which could allow them to be leaked to a sophisticated attacker. See `issue 918 <https://github.com/simonw/datasette/issues/918>`__ for details.
- Datasette now supports GraphQL via the new `datasette-graphql <https://github.com/simonw/datasette-graphql>`__ plugin - see `GraphQL in Datasette with the new datasette-graphql plugin <https://simonwillison.net/2020/Aug/7/datasette-graphql/>`__.
- Principle git branch has been renamed from ``master`` to ``main``. (`#849 <https://github.com/simonw/datasette/issues/849>`__)
- New debugging tool: ``/-/allow-debug tool`` (`demo here <https://latest.datasette.io/-/allow-debug>`__) helps test allow blocks against actors, as described in :ref:`authentication_permissions_allow`. (`#908 <https://github.com/simonw/datasette/issues/908>`__)
- New logo for the documentation, and a new project tagline: "An open source multi-tool for exploring and publishing data".
- Whitespace in column values is now respected on display, using ``white-space: pre-wrap``. (`#896 <https://github.com/simonw/datasette/issues/896>`__)
- New ``await request.post_body()`` method for accessing the raw POST body, see :ref:`internals_request`. (`#897 <https://github.com/simonw/datasette/issues/897>`__)
- Database file downloads now include a ``content-length`` HTTP header, enabling download progress bars. (`#905 <https://github.com/simonw/datasette/issues/905>`__)
- File downloads now also correctly set the suggested file name using a ``content-disposition`` HTTP header. (`#909 <https://github.com/simonw/datasette/issues/909>`__)
- ``tests`` are now excluded from the Datasette package properly - thanks, abeyerpath. (`#456 <https://github.com/simonw/datasette/issues/456>`__)
- The Datasette package published to PyPI now includes ``sdist`` as well as ``bdist_wheel``.
- Better titles for canned query pages. (`#887 <https://github.com/simonw/datasette/issues/887>`__)
- Now only loads Python files from a directory passed using the ``--plugins-dir`` option - thanks, Amjith Ramanujam. (`#890 <https://github.com/simonw/datasette/pull/890>`__)
- New documentation section on :ref:`publish_vercel`.

.. _v0_45:

0.45 (2020-07-01)
-----------------

Magic parameters for canned queries, a log out feature, improved plugin documentation and four new plugin hooks.

Magic parameters for canned queries
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Canned queries now support :ref:`canned_queries_magic_parameters`, which can be used to insert or select automatically generated values. For example::

    insert into logs
      (user_id, timestamp)
    values
      (:_actor_id, :_now_datetime_utc)

This inserts the currently authenticated actor ID and the current datetime. (`#842 <https://github.com/simonw/datasette/issues/842>`__)

Log out
~~~~~~~

The :ref:`ds_actor cookie <authentication_ds_actor>` can be used by plugins (or by Datasette's :ref:`--root mechanism<authentication_root>`) to authenticate users. The new ``/-/logout`` page provides a way to clear that cookie.

A "Log out" button now shows in the global navigation provided the user is authenticated using the ``ds_actor`` cookie. (`#840 <https://github.com/simonw/datasette/issues/840>`__)

Better plugin documentation
~~~~~~~~~~~~~~~~~~~~~~~~~~~

The plugin documentation has been re-arranged into four sections, including a brand new section on testing plugins. (`#687 <https://github.com/simonw/datasette/issues/687>`__)

- :ref:`plugins` introduces Datasette's plugin system and describes how to install and configure plugins.
- :ref:`writing_plugins` describes how to author plugins, from simple one-off plugins to packaged plugins that can be published to PyPI. It also describes how to start a plugin using the new `datasette-plugin <https://github.com/simonw/datasette-plugin>`__ cookiecutter template.
- :ref:`plugin_hooks` is a full list of detailed documentation for every Datasette plugin hook.
- :ref:`testing_plugins` describes how to write tests for Datasette plugins, using `pytest <https://docs.pytest.org/>`__ and `HTTPX <https://www.python-httpx.org/>`__.

New plugin hooks
~~~~~~~~~~~~~~~~

- :ref:`plugin_hook_register_magic_parameters` can be used to define new types of magic canned query parameters.
- :ref:`plugin_hook_startup` can run custom code when Datasette first starts up. `datasette-init <https://github.com/simonw/datasette-init>`__ is a new plugin that uses this hook to create database tables and views on startup if they have not yet been created. (`#834 <https://github.com/simonw/datasette/issues/834>`__)
- :ref:`plugin_hook_canned_queries` lets plugins provide additional canned queries beyond those defined in Datasette's metadata. See `datasette-saved-queries <https://github.com/simonw/datasette-saved-queries>`__ for an example of this hook in action. (`#852 <https://github.com/simonw/datasette/issues/852>`__)
- :ref:`plugin_hook_forbidden` is a hook for customizing how Datasette responds to 403 forbidden errors. (`#812 <https://github.com/simonw/datasette/issues/812>`__)

Smaller changes
~~~~~~~~~~~~~~~

- Cascading view permissons - so if a user has ``view-table`` they can view the table page even if they do not have ``view-database`` or ``view-instance``. (`#832 <https://github.com/simonw/datasette/issues/832>`__)
- CSRF protection no longer applies to ``Authentication: Bearer token`` requests or requests without cookies. (`#835 <https://github.com/simonw/datasette/issues/835>`__)
- ``datasette.add_message()`` now works inside plugins. (`#864 <https://github.com/simonw/datasette/issues/864>`__)
- Workaround for "Too many open files" error in test runs. (`#846 <https://github.com/simonw/datasette/issues/846>`__)
- Respect existing ``scope["actor"]`` if already set by ASGI middleware. (`#854 <https://github.com/simonw/datasette/issues/854>`__)
- New process for shipping :ref:`contributing_alpha_beta`. (`#807 <https://github.com/simonw/datasette/issues/807>`__)
- ``{{ csrftoken() }}`` now works when plugins render a template using ``datasette.render_template(..., request=request)``. (`#863 <https://github.com/simonw/datasette/issues/863>`__)
- Datasette now creates a single :ref:`internals_request` and uses it throughout the lifetime of the current HTTP request. (`#870 <https://github.com/simonw/datasette/issues/870>`__)

.. _v0_44:

0.44 (2020-06-11)
-----------------

Authentication and permissions, writable canned queries, flash messages, new plugin hooks and more.

Authentication
~~~~~~~~~~~~~~

Prior to this release the Datasette ecosystem has treated authentication as exclusively the realm of plugins, most notably through `datasette-auth-github <https://github.com/simonw/datasette-auth-github>`__.

0.44 introduces :ref:`authentication` as core Datasette concepts (`#699 <https://github.com/simonw/datasette/issues/699>`__). This makes it easier for different plugins can share responsibility for authenticating requests - you might have one plugin that handles user accounts and another one that allows automated access via API keys, for example.

You'll need to install plugins if you want full user accounts, but default Datasette can now authenticate a single root user with the new ``--root`` command-line option, which outputs a one-time use URL to :ref:`authenticate as a root actor <authentication_root>` (`#784 <https://github.com/simonw/datasette/issues/784>`__)::

    $ datasette fixtures.db --root
    http://127.0.0.1:8001/-/auth-token?token=5b632f8cd44b868df625f5a6e2185d88eea5b22237fd3cc8773f107cc4fd6477
    INFO:     Started server process [14973]
    INFO:     Waiting for application startup.
    INFO:     Application startup complete.
    INFO:     Uvicorn running on http://127.0.0.1:8001 (Press CTRL+C to quit)

Plugins can implement new ways of authenticating users using the new :ref:`plugin_hook_actor_from_request` hook.

Permissions
~~~~~~~~~~~

Datasette also now has a built-in concept of :ref:`authentication_permissions`. The permissions system answers the following question:

    Is this **actor** allowed to perform this **action**, optionally against this particular **resource**?

You can use the new ``"allow"`` block syntax in ``metadata.json`` (or ``metadata.yaml``) to set required permissions at the instance, database, table or canned query level. For example, to restrict access to the ``fixtures.db`` database to the ``"root"`` user:

.. code-block:: json

    {
        "databases": {
            "fixtures": {
                "allow": {
                    "id" "root"
                }
            }
        }
    }

See :ref:`authentication_permissions_allow` for more details.

Plugins can implement their own custom permission checks using the new :ref:`plugin_hook_permission_allowed` hook.

A new debug page at ``/-/permissions`` shows recent permission checks, to help administrators and plugin authors understand exactly what checks are being performed. This tool defaults to only being available to the root user, but can be exposed to other users by plugins that respond to the ``permissions-debug`` permission. (`#788 <https://github.com/simonw/datasette/issues/788>`__)

Writable canned queries
~~~~~~~~~~~~~~~~~~~~~~~

Datasette's :ref:`canned_queries` feature lets you define SQL queries in ``metadata.json`` which can then be executed by users visiting a specific URL. https://latest.datasette.io/fixtures/neighborhood_search for example.

Canned queries were previously restricted to ``SELECT``, but Datasette 0.44 introduces the ability for canned queries to execute ``INSERT`` or ``UPDATE`` queries as well, using the new ``"write": true`` property (`#800 <https://github.com/simonw/datasette/issues/800>`__):

.. code-block:: json

    {
        "databases": {
            "dogs": {
                "queries": {
                    "add_name": {
                        "sql": "INSERT INTO names (name) VALUES (:name)",
                        "write": true
                    }
                }
            }
        }
    }

See :ref:`canned_queries_writable` for more details.

Flash messages
~~~~~~~~~~~~~~

Writable canned queries needed a mechanism to let the user know that the query has been successfully executed. The new flash messaging system (`#790 <https://github.com/simonw/datasette/issues/790>`__) allows messages to persist in signed cookies which are then displayed to the user on the next page that they visit. Plugins can use this mechanism to display their own messages, see :ref:`datasette_add_message` for details.

You can try out the new messages using the ``/-/messages`` debug tool, for example at https://latest.datasette.io/-/messages

Signed values and secrets
~~~~~~~~~~~~~~~~~~~~~~~~~

Both flash messages and user authentication needed a way to sign values and set signed cookies. Two new methods are now available for plugins to take advantage of this mechanism: :ref:`datasette_sign` and :ref:`datasette_unsign`.

Datasette will generate a secret automatically when it starts up, but to avoid resetting the secret (and hence invalidating any cookies) every time the server restarts you should set your own secret. You can pass a secret to Datasette using the new ``--secret`` option or with a ``DATASETTE_SECRET`` environment variable. See :ref:`config_secret` for more details.

You can also set a secret when you deploy Datasette using ``datasette publish`` or ``datasette package`` - see :ref:`config_publish_secrets`.

Plugins can now sign value and verify their signatures using the :ref:`datasette.sign() <datasette_sign>` and :ref:`datasette.unsign() <datasette_unsign>` methods.

CSRF protection
~~~~~~~~~~~~~~~

Since writable canned queries are built using POST forms, Datasette now ships with :ref:`internals_csrf` (`#798 <https://github.com/simonw/datasette/issues/798>`__). This applies automatically to any POST request, which means plugins need to include a ``csrftoken`` in any POST forms that they render. They can do that like so:

.. code-block:: html

    <input type="hidden" name="csrftoken" value="{{ csrftoken() }}">

Cookie methods
~~~~~~~~~~~~~~

Plugins can now use the new :ref:`response.set_cookie() <internals_response_set_cookie>` method to set cookies.

A new ``request.cookies`` method on the :ref:internals_request` can be used to read incoming cookies.

register_routes() plugin hooks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Plugins can now register new views and routes via the :ref:`plugin_register_routes` plugin hook (`#819 <https://github.com/simonw/datasette/issues/819>`__). View functions can be defined that accept any of the current ``datasette`` object, the current ``request``, or the ASGI ``scope``, ``send`` and ``receive`` objects.

Smaller changes
~~~~~~~~~~~~~~~

- New internals documentation for :ref:`internals_request` and :ref:`internals_response`. (`#706 <https://github.com/simonw/datasette/issues/706>`__)
- ``request.url`` now respects the ``force_https_urls`` config setting. closes (`#781 <https://github.com/simonw/datasette/issues/781>`__)
- ``request.args.getlist()`` returns ``[]`` if missing. Removed ``request.raw_args`` entirely. (`#774 <https://github.com/simonw/datasette/issues/774>`__)
- New :ref:`datasette.get_database() <datasette_get_database>` method.
- Added ``_`` prefix to many private, undocumented methods of the Datasette class. (`#576 <https://github.com/simonw/datasette/issues/576>`__)
- Removed the ``db.get_outbound_foreign_keys()`` method which duplicated the behaviour of ``db.foreign_keys_for_table()``.
- New :ref:`await datasette.permission_allowed() <datasette_permission_allowed>` method.
- ``/-/actor`` debugging endpoint for viewing the currently authenticated actor.
- New ``request.cookies`` property.
- ``/-/plugins`` endpoint now shows a list of hooks implemented by each plugin, e.g. https://latest.datasette.io/-/plugins?all=1
- ``request.post_vars()`` method no longer discards empty values.
- New "params" canned query key for explicitly setting named parameters, see :ref:`canned_queries_named_parameters`. (`#797 <https://github.com/simonw/datasette/issues/797>`__)
- ``request.args`` is now a :ref:`MultiParams <internals_multiparams>` object.
- Fixed a bug with the ``datasette plugins`` command. (`#802 <https://github.com/simonw/datasette/issues/802>`__)
- Nicer pattern for using ``make_app_client()`` in tests. (`#395 <https://github.com/simonw/datasette/issues/395>`__)
- New ``request.actor`` property.
- Fixed broken CSS on nested 404 pages. (`#777 <https://github.com/simonw/datasette/issues/777>`__)
- New ``request.url_vars`` property. (`#822 <https://github.com/simonw/datasette/issues/822>`__)
- Fixed a bug with the ``python tests/fixtures.py`` command for outputting Datasette's testing fixtures database and plugins. (`#804 <https://github.com/simonw/datasette/issues/804>`__)
- ``datasette publish heroku`` now deploys using Python 3.8.3.
- Added a warning that the :ref:`plugin_register_facet_classes` hook is unstable and may change in the future. (`#830 <https://github.com/simonw/datasette/issues/830>`__)
- The ``{"$env": "ENVIRONMENT_VARIBALE"}`` mechanism (see :ref:`plugins_configuration_secret`) now works with variables inside nested lists. (`#837 <https://github.com/simonw/datasette/issues/837>`__)

The road to Datasette 1.0
~~~~~~~~~~~~~~~~~~~~~~~~~

I've assembled a `milestone for Datasette 1.0 <https://github.com/simonw/datasette/milestone/7>`__. The focus of the 1.0 release will be the following:

- Signify confidence in the quality/stability of Datasette
- Give plugin authors confidence that their plugins will work for the whole 1.x release cycle
- Provide the same confidence to developers building against Datasette JSON APIs

If you have thoughts about what you would like to see for Datasette 1.0 you can join `the conversation on issue #519 <https://github.com/simonw/datasette/issues/519>`__.

.. _v0_43:

0.43 (2020-05-28)
-----------------

The main focus of this release is a major upgrade to the :ref:`plugin_register_output_renderer` plugin hook, which allows plugins to provide new output formats for Datasette such as `datasette-atom <https://github.com/simonw/datasette-atom>`__ and `datasette-ics <https://github.com/simonw/datasette-ics>`__.

* Redesign of :ref:`plugin_register_output_renderer` to provide more context to the render callback and support an optional ``"can_render"`` callback that controls if a suggested link to the output format is provided. (`#581 <https://github.com/simonw/datasette/issues/581>`__, `#770 <https://github.com/simonw/datasette/issues/770>`__)
* Visually distinguish float and integer columns - useful for figuring out why order-by-column might be returning unexpected results. (`#729 <https://github.com/simonw/datasette/issues/729>`__)
* The :ref:`internals_request`, which is passed to several plugin hooks, is now documented. (`#706 <https://github.com/simonw/datasette/issues/706>`__)
* New ``metadata.json`` option for setting a custom default page size for specific tables and views, see :ref:`metadata_page_size`. (`#751 <https://github.com/simonw/datasette/issues/751>`__)
* Canned queries can now be configured with a default URL fragment hash, useful when working with plugins such as `datasette-vega <https://github.com/simonw/datasette-vega>`__, see :ref:`canned_queries_default_fragment`. (`#706 <https://github.com/simonw/datasette/issues/706>`__)
* Fixed a bug in ``datasette publish`` when running on operating systems where the ``/tmp`` directory lives in a different volume, using a backport of the Python 3.8 ``shutil.copytree()`` function. (`#744 <https://github.com/simonw/datasette/issues/744>`__)
* Every plugin hook is now covered by the unit tests, and a new unit test checks that each plugin hook has at least one corresponding test. (`#771 <https://github.com/simonw/datasette/issues/771>`__, `#773 <https://github.com/simonw/datasette/issues/773>`__)

.. _v0_42:

0.42 (2020-05-08)
-----------------

A small release which provides improved internal methods for use in plugins, along with documentation. See `#685 <https://github.com/simonw/datasette/issues/685>`__.

* Added documentation for ``db.execute()``, see :ref:`database_execute`.
* Renamed ``db.execute_against_connection_in_thread()`` to ``db.execute_fn()`` and made it a documented method, see :ref:`database_execute_fn`.
* New ``results.first()`` and ``results.single_value()`` methods, plus documentation for the ``Results`` class - see :ref:`database_results`.

.. _v0_41:

0.41 (2020-05-06)
-----------------

You can now create :ref:`custom pages <custom_pages>` within your Datasette instance using a custom template file. For example, adding a template file called ``templates/pages/about.html`` will result in a new page being served at ``/about`` on your instance. See the :ref:`custom pages documentation <custom_pages>` for full details, including how to return custom HTTP headers, redirects and status codes. (`#648 <https://github.com/simonw/datasette/issues/648>`__)

:ref:`config_dir` (`#731 <https://github.com/simonw/datasette/issues/731>`__) allows you to define a custom Datasette instance as a directory. So instead of running the following::

    $ datasette one.db two.db \
      --metadata.json \
      --template-dir=templates/ \
      --plugins-dir=plugins \
      --static css:css

You can instead arrange your files in a single directory called ``my-project`` and run this::

    $ datasette my-project/

Also in this release:

* New ``NOT LIKE`` table filter: ``?colname__notlike=expression``. (`#750 <https://github.com/simonw/datasette/issues/750>`__)
* Datasette now has a *pattern portfolio* at ``/-/patterns`` - e.g. https://latest.datasette.io/-/patterns. This is a page that shows every Datasette user interface component in one place, to aid core development and people building custom CSS themes. (`#151 <https://github.com/simonw/datasette/issues/151>`__)
* SQLite `PRAGMA functions <https://www.sqlite.org/pragma.html#pragfunc>`__ such as ``pragma_table_info(tablename)`` are now allowed in Datasette SQL queries. (`#761 <https://github.com/simonw/datasette/issues/761>`__)
* Datasette pages now consistently return a ``content-type`` of ``text/html; charset=utf-8"``. (`#752 <https://github.com/simonw/datasette/issues/752>`__)
* Datasette now handles an ASGI ``raw_path`` value of ``None``, which should allow compatibilty with the `Mangum <https://github.com/erm/mangum>`__ adapter for running ASGI apps on AWS Lambda. Thanks, Colin Dellow. (`#719 <https://github.com/simonw/datasette/pull/719>`__)
* Installation documentation now covers how to :ref:`installation_pipx`. (`#756 <https://github.com/simonw/datasette/issues/756>`__)
* Improved the documentation for :ref:`full_text_search`. (`#748 <https://github.com/simonw/datasette/issues/748>`__)

.. _v0_40:

0.40 (2020-04-21)
-----------------

* Datasette :ref:`metadata` can now be provided as a YAML file as an optional alternative to JSON. See :ref:`metadata_yaml`. (`#713 <https://github.com/simonw/datasette/issues/713>`__)
* Removed support for ``datasette publish now``, which used the the now-retired Zeit Now v1 hosting platform. A new plugin, `datasette-publish-now <https://github.com/simonw/datasette-publish-now>`__, can be installed to publish data to Zeit (`now Vercel <https://vercel.com/blog/zeit-is-now-vercel>`__) Now v2. (`#710 <https://github.com/simonw/datasette/issues/710>`__)
* Fixed a bug where the ``extra_template_vars(request, view_name)`` plugin hook was not receiving the correct ``view_name``. (`#716 <https://github.com/simonw/datasette/issues/716>`__)
* Variables added to the template context by the ``extra_template_vars()`` plugin hook are now shown in the ``?_context=1`` debugging mode (see :ref:`config_template_debug`). (`#693 <https://github.com/simonw/datasette/issues/693>`__)
* Fixed a bug where the "templates considered" HTML comment was no longer being displayed. (`#689 <https://github.com/simonw/datasette/issues/689>`__)
* Fixed a ``datasette publish`` bug where ``--plugin-secret`` would over-ride plugin configuration in the provided ``metadata.json`` file. (`#724 <https://github.com/simonw/datasette/issues/724>`__)
* Added a new CSS class for customizing the canned query page. (`#727 <https://github.com/simonw/datasette/issues/727>`__)

.. _v0_39:

0.39 (2020-03-24)
-----------------

* New :ref:`config_base_url` configuration setting for serving up the correct links while running Datasette under a different URL prefix. (`#394 <https://github.com/simonw/datasette/issues/394>`__)
* New metadata settings ``"sort"`` and ``"sort_desc"`` for setting the default sort order for a table. See :ref:`metadata_default_sort`. (`#702 <https://github.com/simonw/datasette/issues/702>`__)
* Sort direction arrow now displays by default on the primary key. This means you only have to click once (not twice) to sort in reverse order. (`#677 <https://github.com/simonw/datasette/issues/677>`__)
* New ``await Request(scope, receive).post_vars()`` method for accessing POST form variables. (`#700 <https://github.com/simonw/datasette/issues/700>`__)
* :ref:`plugin_hooks` documentation now links to example uses of each plugin. (`#709 <https://github.com/simonw/datasette/issues/709>`__)

.. _v0_38:

0.38 (2020-03-08)
-----------------

* The `Docker build <https://hub.docker.com/r/datasetteproject/datasette>`__ of Datasette now uses SQLite 3.31.1, upgraded from 3.26. (`#695 <https://github.com/simonw/datasette/issues/695>`__)
* ``datasette publish cloudrun`` now accepts an optional ``--memory=2Gi`` flag for setting the Cloud Run allocated memory to a value other than the default (256Mi). (`#694 <https://github.com/simonw/datasette/issues/694>`__)
* Fixed bug where templates that shipped with plugins were sometimes not being correctly loaded. (`#697 <https://github.com/simonw/datasette/issues/697>`__)

.. _v0_37_1:

0.37.1 (2020-03-02)
-------------------

* Don't attempt to count table rows to display on the index page for databases > 100MB. (`#688 <https://github.com/simonw/datasette/issues/688>`__)
* Print exceptions if they occur in the write thread rather than silently swallowing them.
* Handle the possibility of ``scope["path"]`` being a string rather than bytes
* Better documentation for the :ref:`plugin_hook_extra_template_vars` plugin hook.

.. _v0_37:

0.37 (2020-02-25)
-----------------

* Plugins now have a supported mechanism for writing to a database, using the new ``.execute_write()`` and ``.execute_write_fn()`` methods. :ref:`Documentation <database_execute_write>`. (`#682 <https://github.com/simonw/datasette/issues/682>`__)
* Immutable databases that have had their rows counted using the ``inspect`` command now use the calculated count more effectively - thanks, Kevin Keogh. (`#666 <https://github.com/simonw/datasette/pull/666>`__)
* ``--reload`` no longer restarts the server if a database file is modified, unless that database was opened immutable mode with ``-i``. (`#494 <https://github.com/simonw/datasette/issues/494>`__)
* New ``?_searchmode=raw`` option turns off escaping for FTS queries in ``?_search=`` allowing full use of SQLite's `FTS5 query syntax <https://www.sqlite.org/fts5.html#full_text_query_syntax>`__. (`#676 <https://github.com/simonw/datasette/issues/676>`__)

.. _v0_36:

0.36 (2020-02-21)
-----------------

* The ``datasette`` object passed to plugins now has API documentation: :ref:`internals_datasette`. (`#576 <https://github.com/simonw/datasette/issues/576>`__)
* New methods on ``datasette``: ``.add_database()`` and ``.remove_database()`` - :ref:`documentation <datasette_add_database>`. (`#671 <https://github.com/simonw/datasette/issues/671>`__)
* ``prepare_connection()`` plugin hook now takes optional ``datasette`` and ``database`` arguments - :ref:`plugin_hook_prepare_connection`. (`#678 <https://github.com/simonw/datasette/issues/678>`__)
* Added three new plugins and one new conversion tool to the :ref:`ecosystem`.

.. _v0_35:

0.35 (2020-02-04)
-----------------

* Added five new plugins and one new conversion tool to the :ref:`ecosystem`.
* The ``Datasette`` class has a new ``render_template()`` method which can be used by plugins to render templates using Datasette's pre-configured `Jinja <https://jinja.palletsprojects.com/>`__ templating library.
* You can now execute SQL queries that start with a ``-- comment`` - thanks, Jay Graves (`#653 <https://github.com/simonw/datasette/pull/653>`__)

.. _v0_34:

0.34 (2020-01-29)
-----------------

* ``_search=`` queries are now correctly escaped using a new ``escape_fts()`` custom SQL function. This means you can now run searches for strings like ``park.`` without seeing errors. (`#651 <https://github.com/simonw/datasette/issues/651>`__)
* `Google Cloud Run <https://cloud.google.com/run/>`__ is no longer in beta, so ``datasette publish cloudrun`` has been updated to work even if the user has not installed the ``gcloud`` beta components package. Thanks, Katie McLaughlin (`#660 <https://github.com/simonw/datasette/pull/660>`__)
* ``datasette package`` now accepts a ``--port`` option for specifying which port the resulting Docker container should listen on. (`#661 <https://github.com/simonw/datasette/issues/661>`__)

.. _v0_33:

0.33 (2019-12-22)
-----------------

* ``rowid`` is now included in dropdown menus for filtering tables (`#636 <https://github.com/simonw/datasette/issues/636>`__)
* Columns are now only suggested for faceting if they have at least one value with more than one record (`#638 <https://github.com/simonw/datasette/issues/638>`__)
* Queries with no results now display "0 results" (`#637 <https://github.com/simonw/datasette/issues/637>`__)
* Improved documentation for the ``--static`` option (`#641 <https://github.com/simonw/datasette/issues/641>`__)
* asyncio task information is now included on the ``/-/threads`` debug page
* Bumped Uvicorn dependency 0.11
* You can now use ``--port 0`` to listen on an available port
* New :ref:`config_template_debug` setting for debugging templates, e.g. https://latest.datasette.io/fixtures/roadside_attractions?_context=1 (`#654 <https://github.com/simonw/datasette/issues/654>`__)

.. _v0_32:

0.32 (2019-11-14)
-----------------

Datasette now renders templates using `Jinja async mode <https://jinja.palletsprojects.com/en/2.10.x/api/#async-support>`__. This makes it easy for plugins to provide custom template functions that perform asynchronous actions, for example the new `datasette-template-sql <https://github.com/simonw/datasette-template-sql>`__ plugin which allows custom templates to directly execute SQL queries and render their results. (`#628 <https://github.com/simonw/datasette/issues/628>`__)

.. _v0_31_2:

0.31.2 (2019-11-13)
-------------------

- Fixed a bug where ``datasette publish heroku`` applications failed to start (`#633 <https://github.com/simonw/datasette/issues/633>`__)
- Fix for ``datasette publish`` with just ``--source_url`` - thanks, Stanley Zheng (`#572 <https://github.com/simonw/datasette/issues/572>`__)
- Deployments to Heroku now use Python 3.8.0 (`#632 <https://github.com/simonw/datasette/issues/632>`__)

.. _v0_31_1:

0.31.1 (2019-11-12)
-------------------

- Deployments created using ``datasette publish``  now use ``python:3.8`` base Docker image (`#629 <https://github.com/simonw/datasette/pull/629>`__)

.. _v0_31:

0.31 (2019-11-11)
-----------------

This version adds compatibility with Python 3.8 and breaks compatibility with Python 3.5.

If you are still running Python 3.5 you should stick with ``0.30.2``, which you can install like this::

    pip install datasette==0.30.2

- Format SQL button now works with read-only SQL queries - thanks, Tobias Kunze (`#602 <https://github.com/simonw/datasette/pull/602>`__)
- New ``?column__notin=x,y,z`` filter for table views (`#614 <https://github.com/simonw/datasette/issues/614>`__)
- Table view now uses ``select col1, col2, col3`` instead of ``select *``
- Database filenames can now contain spaces - thanks, Tobias Kunze (`#590 <https://github.com/simonw/datasette/pull/590>`__)
- Removed obsolete ``?_group_count=col`` feature (`#504 <https://github.com/simonw/datasette/issues/504>`__)
- Improved user interface and documentation for ``datasette publish cloudrun`` (`#608 <https://github.com/simonw/datasette/issues/608>`__)
- Tables with indexes now show the ``CREATE INDEX`` statements on the table page (`#618 <https://github.com/simonw/datasette/issues/618>`__)
- Current version of `uvicorn <https://www.uvicorn.org/>`__ is now shown on ``/-/versions``
- Python 3.8 is now supported! (`#622 <https://github.com/simonw/datasette/issues/622>`__)
- Python 3.5 is no longer supported.

.. _v0_30_2:

0.30.2 (2019-11-02)
-------------------

- ``/-/plugins`` page now uses distribution name e.g. ``datasette-cluster-map`` instead of the name of the underlying Python package (``datasette_cluster_map``) (`#606 <https://github.com/simonw/datasette/issues/606>`__)
- Array faceting is now only suggested for columns that contain arrays of strings (`#562 <https://github.com/simonw/datasette/issues/562>`__)
- Better documentation for the ``--host`` argument (`#574 <https://github.com/simonw/datasette/issues/574>`__)
- Don't show ``None`` with a broken link for the label on a nullable foreign key (`#406 <https://github.com/simonw/datasette/issues/406>`__)

.. _v0_30_1:

0.30.1 (2019-10-30)
-------------------

- Fixed bug where ``?_where=`` parameter was not persisted in hidden form fields (`#604 <https://github.com/simonw/datasette/issues/604>`__)
- Fixed bug with .JSON representation of row pages - thanks, Chris Shaw (`#603 <https://github.com/simonw/datasette/issues/603>`__)

.. _v0_30:


0.30 (2019-10-18)
-----------------

- Added ``/-/threads`` debugging page
- Allow ``EXPLAIN WITH...`` (`#583 <https://github.com/simonw/datasette/issues/583>`__)
- Button to format SQL - thanks, Tobias Kunze (`#136 <https://github.com/simonw/datasette/issues/136>`__)
- Sort databases on homepage by argument order - thanks, Tobias Kunze (`#585 <https://github.com/simonw/datasette/issues/585>`__)
- Display metadata footer on custom SQL queries - thanks, Tobias Kunze (`#589 <https://github.com/simonw/datasette/pull/589>`__)
- Use ``--platform=managed`` for ``publish cloudrun`` (`#587 <https://github.com/simonw/datasette/issues/587>`__)
- Fixed bug returning non-ASCII characters in CSV (`#584 <https://github.com/simonw/datasette/issues/584>`__)
- Fix for ``/foo`` v.s. ``/foo-bar`` bug (`#601 <https://github.com/simonw/datasette/issues/601>`__)

.. _v0_29_3:

0.29.3 (2019-09-02)
-------------------

- Fixed implementation of CodeMirror on database page (`#560 <https://github.com/simonw/datasette/issues/560>`__)
- Documentation typo fixes - thanks, Min ho Kim (`#561 <https://github.com/simonw/datasette/pull/561>`__)
- Mechanism for detecting if a table has FTS enabled now works if the table name used alternative escaping mechanisms (`#570 <https://github.com/simonw/datasette/issues/570>`__) - for compatibility with `a recent change to sqlite-utils <https://github.com/simonw/sqlite-utils/pull/57>`__.

.. _v0_29_2:

0.29.2 (2019-07-13)
-------------------

- Bumped `Uvicorn <https://www.uvicorn.org/>`__ to 0.8.4, fixing a bug where the querystring was not included in the server logs. (`#559 <https://github.com/simonw/datasette/issues/559>`__)
- Fixed bug where the navigation breadcrumbs were not displayed correctly on the page for a custom query. (`#558 <https://github.com/simonw/datasette/issues/558>`__)
- Fixed bug where custom query names containing unicode characters caused errors.

.. _v0_29_1:

0.29.1 (2019-07-11)
-------------------

- Fixed bug with static mounts using relative paths which could lead to traversal exploits (`#555 <https://github.com/simonw/datasette/issues/555>`__) - thanks Abdussamet Kocak!
- Datasette can now be run as a module: ``python -m datasette`` (`#556 <https://github.com/simonw/datasette/issues/556>`__) - thanks, Abdussamet Kocak!

.. _v0_29:

0.29 (2019-07-07)
-----------------

ASGI, new plugin hooks, facet by date and much, much more...

ASGI
~~~~

`ASGI <https://asgi.readthedocs.io/>`__ is the Asynchronous Server Gateway Interface standard. I've been wanting to convert Datasette into an ASGI application for over a year - `Port Datasette to ASGI #272 <https://github.com/simonw/datasette/issues/272>`__ tracks thirteen months of intermittent development - but with Datasette 0.29 the change is finally released. This also means Datasette now runs on top of `Uvicorn <https://www.uvicorn.org/>`__ and no longer depends on `Sanic <https://github.com/huge-success/sanic>`__.

I wrote about the significance of this change in `Porting Datasette to ASGI, and Turtles all the way down <https://simonwillison.net/2019/Jun/23/datasette-asgi/>`__.

The most exciting consequence of this change is that Datasette plugins can now take advantage of the ASGI standard.

New plugin hook: asgi_wrapper
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The :ref:`plugin_asgi_wrapper` plugin hook allows plugins to entirely wrap the Datasette ASGI application in their own ASGI middleware. (`#520 <https://github.com/simonw/datasette/issues/520>`__)

Two new plugins take advantage of this hook:

* `datasette-auth-github <https://github.com/simonw/datasette-auth-github>`__ adds a authentication layer: users will have to sign in using their GitHub account before they can view data or interact with Datasette. You can also use it to restrict access to specific GitHub users, or to members of specified GitHub `organizations <https://help.github.com/en/articles/about-organizations>`__ or `teams <https://help.github.com/en/articles/organizing-members-into-teams>`__.

* `datasette-cors <https://github.com/simonw/datasette-cors>`__ allows you to configure `CORS headers <https://developer.mozilla.org/en-US/docs/Web/HTTP/CORS>`__ for your Datasette instance. You can use this to enable JavaScript running on a whitelisted set of domains to make ``fetch()`` calls to the JSON API provided by your Datasette instance.

New plugin hook: extra_template_vars
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The :ref:`plugin_hook_extra_template_vars` plugin hook allows plugins to inject their own additional variables into the Datasette template context. This can be used in conjunction with custom templates to customize the Datasette interface. `datasette-auth-github <https://github.com/simonw/datasette-auth-github>`__ uses this hook to add custom HTML to the new top navigation bar (which is designed to be modified by plugins, see `#540 <https://github.com/simonw/datasette/issues/540>`__).

Secret plugin configuration options
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Plugins like `datasette-auth-github <https://github.com/simonw/datasette-auth-github>`__ need a safe way to set secret configuration options. Since the default mechanism for configuring plugins exposes those settings in ``/-/metadata`` a new mechanism was needed. :ref:`plugins_configuration_secret` describes how plugins can now specify that their settings should be read from a file or an environment variable::

    {
        "plugins": {
            "datasette-auth-github": {
                "client_secret": {
                    "$env": "GITHUB_CLIENT_SECRET"
                }
            }
        }
    }

These plugin secrets can be set directly using ``datasette publish``. See :ref:`publish_custom_metadata_and_plugins` for details. (`#538 <https://github.com/simonw/datasette/issues/538>`__ and `#543 <https://github.com/simonw/datasette/issues/543>`__)

Facet by date
~~~~~~~~~~~~~

If a column contains datetime values, Datasette can now facet that column by date. (`#481 <https://github.com/simonw/datasette/issues/481>`__)

.. _v0_29_medium_changes:

Easier custom templates for table rows
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

If you want to customize the display of individual table rows, you can do so using a ``_table.html`` template include that looks something like this::

    {% for row in display_rows %}
        <div>
            <h2>{{ row["title"] }}</h2>
            <p>{{ row["description"] }}<lp>
            <p>Category: {{ row.display("category_id") }}</p>
        </div>
    {% endfor %}

This is a **backwards incompatible change**. If you previously had a custom template called ``_rows_and_columns.html`` you need to rename it to ``_table.html``.

See :ref:`customization_custom_templates` for full details.

?_through= for joins through many-to-many tables
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The new ``?_through={json}`` argument to the Table view allows records to be filtered based on a many-to-many relationship. See :ref:`json_api_table_arguments` for full documentation - here's `an example <https://latest.datasette.io/fixtures/roadside_attractions?_through={%22table%22:%22roadside_attraction_characteristics%22,%22column%22:%22characteristic_id%22,%22value%22:%221%22}>`__. (`#355 <https://github.com/simonw/datasette/issues/355>`__)

This feature was added to help support `facet by many-to-many <https://github.com/simonw/datasette/issues/551>`__, which isn't quite ready yet but will be coming in the next Datasette release.

Small changes
~~~~~~~~~~~~~

* Databases published using ``datasette publish`` now open in :ref:`performance_immutable_mode`. (`#469 <https://github.com/simonw/datasette/issues/469>`__)
* ``?col__date=`` now works for columns containing spaces
* Automatic label detection (for deciding which column to show when linking to a foreign key) has been improved. (`#485 <https://github.com/simonw/datasette/issues/485>`__)
* Fixed bug where pagination broke when combined with an expanded foreign key. (`#489 <https://github.com/simonw/datasette/issues/489>`__)
* Contributors can now run ``pip install -e .[docs]`` to get all of the dependencies needed to build the documentation, including ``cd docs && make livehtml`` support.
* Datasette's dependencies are now all specified using the ``~=`` match operator. (`#532 <https://github.com/simonw/datasette/issues/532>`__)
* ``white-space: pre-wrap`` now used for table creation SQL. (`#505 <https://github.com/simonw/datasette/issues/505>`__)


`Full list of commits <https://github.com/simonw/datasette/compare/0.28...0.29>`__ between 0.28 and 0.29.

.. _v0_28:

0.28 (2019-05-19)
-----------------

A `salmagundi <https://adamj.eu/tech/2019/01/18/a-salmagundi-of-django-alpha-announcements/>`__ of new features! 

.. _v0_28_databases_that_change:

Supporting databases that change
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

From the beginning of the project, Datasette has been designed with read-only databases in mind. If a database is guaranteed not to change it opens up all kinds of interesting opportunities - from taking advantage of SQLite immutable mode and HTTP caching to bundling static copies of the database directly in a Docker container. `The interesting ideas in Datasette <https://simonwillison.net/2018/Oct/4/datasette-ideas/>`__ explores this idea in detail.

As my goals for the project have developed, I realized that read-only databases are no longer the right default. SQLite actually supports concurrent access very well provided only one thread attempts to write to a database at a time, and I keep encountering sensible use-cases for running Datasette on top of a database that is processing inserts and updates.

So, as-of version 0.28 Datasette no longer assumes that a database file will not change. It is now safe to point Datasette at a SQLite database which is being updated by another process.

Making this change was a lot of work - see tracking tickets `#418 <https://github.com/simonw/datasette/issues/418>`__, `#419 <https://github.com/simonw/datasette/issues/419>`__ and `#420 <https://github.com/simonw/datasette/issues/420>`__. It required new thinking around how Datasette should calculate table counts (an expensive operation against a large, changing database) and also meant reconsidering the "content hash" URLs Datasette has used in the past to optimize the performance of HTTP caches.

Datasette can still run against immutable files and gains numerous performance benefits from doing so, but this is no longer the default behaviour. Take a look at the new :ref:`performance` documentation section for details on how to make the most of Datasette against data that you know will be staying read-only and immutable.

.. _v0_28_faceting:

Faceting improvements, and faceting plugins
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Datasette :ref:`facets` provide an intuitive way to quickly summarize and interact with data. Previously the only supported faceting technique was column faceting, but 0.28 introduces two powerful new capabilities: facet-by-JSON-array and the ability to define further facet types using plugins.

Facet by array (`#359 <https://github.com/simonw/datasette/issues/359>`__) is only available if your SQLite installation provides the ``json1`` extension. Datasette will automatically detect columns that contain JSON arrays of values and offer a faceting interface against those columns - useful for modelling things like tags without needing to break them out into a new table. See :ref:`facet_by_json_array` for more.

The new :ref:`plugin_register_facet_classes` plugin hook (`#445 <https://github.com/simonw/datasette/pull/445>`__) can be used to register additional custom facet classes. Each facet class should provide two methods: ``suggest()`` which suggests facet selections that might be appropriate for a provided SQL query, and ``facet_results()`` which executes a facet operation and returns results. Datasette's own faceting implementations have been refactored to use the same API as these plugins.

.. _v0_28_publish_cloudrun:

datasette publish cloudrun
~~~~~~~~~~~~~~~~~~~~~~~~~~

`Google Cloud Run <https://cloud.google.com/run/>`__ is a brand new serverless hosting platform from Google, which allows you to build a Docker container which will run only when HTTP traffic is received and will shut down (and hence cost you nothing) the rest of the time. It's similar to Zeit's Now v1 Docker hosting platform which sadly is `no longer accepting signups <https://hyperion.alpha.spectrum.chat/zeit/now/cannot-create-now-v1-deployments~d206a0d4-5835-4af5-bb5c-a17f0171fb25?m=MTU0Njk2NzgwODM3OA==>`__ from new users.

The new ``datasette publish cloudrun`` command was contributed by Romain Primet (`#434 <https://github.com/simonw/datasette/pull/434>`__) and publishes selected databases to a new Datasette instance running on Google Cloud Run.

See :ref:`publish_cloud_run` for full documentation.

.. _v0_28_register_output_renderer:

register_output_renderer plugins
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Russ Garrett implemented a new Datasette plugin hook called :ref:`register_output_renderer <plugin_register_output_renderer>` (`#441 <https://github.com/simonw/datasette/pull/441>`__) which allows plugins to create additional output renderers in addition to Datasette's default ``.json`` and ``.csv``.

Russ's in-development `datasette-geo <https://github.com/russss/datasette-geo>`__ plugin includes `an example <https://github.com/russss/datasette-geo/blob/d4cecc020848bbde91e9e17bf352f7c70bc3dccf/datasette_plugin_geo/geojson.py>`__ of this hook being used to output ``.geojson`` automatically converted from SpatiaLite.

.. _v0_28_medium_changes:

Medium changes
~~~~~~~~~~~~~~

- Datasette now conforms to the `Black coding style <https://github.com/python/black>`__ (`#449 <https://github.com/simonw/datasette/pull/449>`__) - and has a unit test to enforce this in the future
- New :ref:`json_api_table_arguments`:
   - ``?columnname__in=value1,value2,value3`` filter for executing SQL IN queries against a table, see :ref:`table_arguments` (`#433 <https://github.com/simonw/datasette/issues/433>`__)
   - ``?columnname__date=yyyy-mm-dd`` filter which returns rows where the spoecified datetime column falls on the specified date (`583b22a <https://github.com/simonw/datasette/commit/583b22aa28e26c318de0189312350ab2688c90b1>`__)
   - ``?tags__arraycontains=tag`` filter which acts against a JSON array contained in a column (`78e45ea <https://github.com/simonw/datasette/commit/78e45ead4d771007c57b307edf8fc920101f8733>`__)
   - ``?_where=sql-fragment`` filter for the table view  (`#429 <https://github.com/simonw/datasette/issues/429>`__)
   - ``?_fts_table=mytable`` and ``?_fts_pk=mycolumn`` querystring options can be used to specify which FTS table to use for a search query - see :ref:`full_text_search_table_or_view` (`#428 <https://github.com/simonw/datasette/issues/428>`__)
- You can now pass the same table filter multiple times - for example, ``?content__not=world&content__not=hello`` will return all rows where the content column is neither ``hello`` or ``world`` (`#288 <https://github.com/simonw/datasette/issues/288>`__)
- You can now specify ``about`` and ``about_url`` metadata (in addition to ``source`` and ``license``) linking to further information about a project - see :ref:`metadata_source_license_about`
- New ``?_trace=1`` parameter now adds debug information showing every SQL query that was executed while constructing the page (`#435 <https://github.com/simonw/datasette/issues/435>`__)
- ``datasette inspect`` now just calculates table counts, and does not introspect other database metadata (`#462 <https://github.com/simonw/datasette/issues/462>`__)
- Removed ``/-/inspect`` page entirely - this will be replaced by something similar in the future, see `#465 <https://github.com/simonw/datasette/issues/465>`__
- Datasette can now run against an in-memory SQLite database. You can do this by starting it without passing any files or by using the new ``--memory`` option to ``datasette serve``. This can be useful for experimenting with SQLite queries that do not access any data, such as ``SELECT 1+1`` or ``SELECT sqlite_version()``.

.. _v0_28_small_changes:

Small changes
~~~~~~~~~~~~~

- We now show the size of the database file next to the download link (`#172 <https://github.com/simonw/datasette/issues/172>`__)
- New ``/-/databases`` introspection page shows currently connected databases (`#470 <https://github.com/simonw/datasette/issues/470>`__)
- Binary data is no longer displayed on the table and row pages (`#442 <https://github.com/simonw/datasette/pull/442>`__ - thanks, Russ Garrett)
- New show/hide SQL links on custom query pages (`#415 <https://github.com/simonw/datasette/issues/415>`__)
- The :ref:`extra_body_script <plugin_hook_extra_body_script>` plugin hook now accepts an optional ``view_name`` argument (`#443 <https://github.com/simonw/datasette/pull/443>`__ - thanks, Russ Garrett)
- Bumped Jinja2 dependency to 2.10.1 (`#426 <https://github.com/simonw/datasette/pull/426>`__)
- All table filters are now documented, and documentation is enforced via unit tests (`2c19a27 <https://github.com/simonw/datasette/commit/2c19a27d15a913e5f3dd443f04067169a6f24634>`__)
- New project guideline: master should stay shippable at all times! (`31f36e1 <https://github.com/simonw/datasette/commit/31f36e1b97ccc3f4387c80698d018a69798b6228>`__)
- Fixed a bug where ``sqlite_timelimit()`` occasionally failed to clean up after itself (`bac4e01 <https://github.com/simonw/datasette/commit/bac4e01f40ae7bd19d1eab1fb9349452c18de8f5>`__)
- We no longer load additional plugins when executing pytest (`#438 <https://github.com/simonw/datasette/issues/438>`__)
- Homepage now links to database views if there are less than five tables in a database (`#373 <https://github.com/simonw/datasette/issues/373>`__)
- The ``--cors`` option is now respected by error pages (`#453 <https://github.com/simonw/datasette/issues/453>`__)
- ``datasette publish heroku`` now uses the ``--include-vcs-ignore`` option, which means it works under Travis CI (`#407 <https://github.com/simonw/datasette/pull/407>`__)
- ``datasette publish heroku`` now publishes using Python 3.6.8 (`666c374 <https://github.com/simonw/datasette/commit/666c37415a898949fae0437099d62a35b1e9c430>`__)
- Renamed ``datasette publish now`` to ``datasette publish nowv1`` (`#472 <https://github.com/simonw/datasette/issues/472>`__)
- ``datasette publish nowv1`` now accepts multiple ``--alias`` parameters (`09ef305 <https://github.com/simonw/datasette/commit/09ef305c687399384fe38487c075e8669682deb4>`__)
- Removed the ``datasette skeleton`` command (`#476 <https://github.com/simonw/datasette/issues/476>`__)
- The :ref:`documentation on how to build the documentation <contributing_documentation>` now recommends ``sphinx-autobuild``

.. _v0_27_1:

0.27.1 (2019-05-09)
-------------------

- Tiny bugfix release: don't install ``tests/`` in the wrong place. Thanks, Veit Heller.

.. _v0_27:

0.27 (2019-01-31)
-----------------

- New command: ``datasette plugins`` (:ref:`documentation <plugins_installed>`) shows you the currently installed list of plugins.
- Datasette can now output `newline-delimited JSON <http://ndjson.org/>`__ using the new ``?_shape=array&_nl=on`` querystring option.
- Added documentation on :ref:`ecosystem`.
- Now using Python 3.7.2 as the base for the official `Datasette Docker image <https://hub.docker.com/r/datasetteproject/datasette/>`__.

.. _v0_26_1:

0.26.1 (2019-01-10)
-------------------

- ``/-/versions`` now includes SQLite ``compile_options`` (`#396 <https://github.com/simonw/datasette/issues/396>`__)
- `datasetteproject/datasette <https://hub.docker.com/r/datasetteproject/datasette>`__ Docker image now uses SQLite 3.26.0 (`#397 <https://github.com/simonw/datasette/issues/397>`__)
- Cleaned up some deprecation warnings under Python 3.7

.. _v0_26:

0.26 (2019-01-02)
-----------------

- ``datasette serve --reload`` now restarts Datasette if a database file changes on disk.
- ``datasette publish now`` now takes an optional ``--alias mysite.now.sh`` argument. This will attempt to set an alias after the deploy completes.
- Fixed a bug where the advanced CSV export form failed to include the currently selected filters (`#393 <https://github.com/simonw/datasette/issues/393>`__)

.. _v0_25_2:

0.25.2 (2018-12-16)
-------------------

- ``datasette publish heroku`` now uses the ``python-3.6.7`` runtime
- Added documentation on :ref:`how to build the documentation <contributing_documentation>`
- Added documentation covering :ref:`our release process <contributing_release>`
- Upgraded to pytest 4.0.2

.. _v0_25_1:

0.25.1 (2018-11-04)
-------------------

Documentation improvements plus a fix for publishing to Zeit Now.

- ``datasette publish now`` now uses Zeit's v1 platform, to work around the new 100MB image limit. Thanks, @slygent - closes `#366 <https://github.com/simonw/datasette/issues/366>`__.

.. _v0_25:

0.25 (2018-09-19)
-----------------

New plugin hooks, improved database view support and an easier way to use more recent versions of SQLite.

- New ``publish_subcommand`` plugin hook. A plugin can now add additional ``datasette publish`` publishers in addition to the default ``now`` and ``heroku``, both of which have been refactored into default plugins. :ref:`publish_subcommand documentation <plugin_hook_publish_subcommand>`. Closes `#349 <https://github.com/simonw/datasette/issues/349>`__
- New ``render_cell`` plugin hook. Plugins can now customize how values are displayed in the HTML tables produced by Datasette's browseable interface. `datasette-json-html <https://github.com/simonw/datasette-json-html>`__ and `datasette-render-images <https://github.com/simonw/datasette-render-images>`__ are two new plugins that use this hook. :ref:`render_cell documentation <plugin_hook_render_cell>`. Closes `#352 <https://github.com/simonw/datasette/issues/352>`__
- New ``extra_body_script`` plugin hook, enabling plugins to provide additional JavaScript that should be added to the page footer. :ref:`extra_body_script documentation <plugin_hook_extra_body_script>`.
- ``extra_css_urls`` and ``extra_js_urls`` hooks now take additional optional parameters, allowing them to be more selective about which pages they apply to. :ref:`Documentation <plugin_hook_extra_css_urls>`.
- You can now use the :ref:`sortable_columns metadata setting <metadata_sortable_columns>` to explicitly enable sort-by-column in the interface for database views, as well as for specific tables.
- The new ``fts_table`` and ``fts_pk`` metadata settings can now be used to :ref:`explicitly configure full-text search for a table or a view <full_text_search_table_or_view>`, even if that table is not directly coupled to the SQLite FTS feature in the database schema itself.
- Datasette will now use `pysqlite3 <https://github.com/coleifer/pysqlite3>`__ in place of the standard library ``sqlite3`` module if it has been installed in the current environment. This makes it much easier to run Datasette against a more recent version of SQLite, including the just-released `SQLite 3.25.0 <https://www.sqlite.org/releaselog/3_25_0.html>`__ which adds window function support. More details on how to use this in `#360 <https://github.com/simonw/datasette/issues/360>`__
- New mechanism that allows :ref:`plugin configuration options <plugins_configuration>` to be set using ``metadata.json``.


.. _v0_24:

0.24 (2018-07-23)
-----------------

A number of small new features:

- ``datasette publish heroku`` now supports ``--extra-options``, fixes `#334 <https://github.com/simonw/datasette/issues/334>`_
- Custom error message if SpatiaLite is needed for specified database, closes `#331 <https://github.com/simonw/datasette/issues/331>`_
- New config option: ``truncate_cells_html`` for :ref:`truncating long cell values <config_truncate_cells_html>` in HTML view - closes `#330 <https://github.com/simonw/datasette/issues/330>`_
- Documentation for :ref:`datasette publish and datasette package <publishing>`, closes `#337 <https://github.com/simonw/datasette/issues/337>`_
- Fixed compatibility with Python 3.7
- ``datasette publish heroku`` now supports app names via the ``-n`` option, which can also be used to overwrite an existing application [Russ Garrett]
- Title and description metadata can now be set for :ref:`canned SQL queries <canned_queries>`, closes `#342 <https://github.com/simonw/datasette/issues/342>`_
- New ``force_https_on`` config option, fixes ``https://`` API URLs when deploying to Zeit Now - closes `#333 <https://github.com/simonw/datasette/issues/333>`_
- ``?_json_infinity=1`` querystring argument for handling Infinity/-Infinity values in JSON, closes `#332 <https://github.com/simonw/datasette/issues/332>`_
- URLs displayed in the results of custom SQL queries are now URLified, closes `#298 <https://github.com/simonw/datasette/issues/298>`_

.. _v0_23_2:

0.23.2 (2018-07-07)
-------------------

Minor bugfix and documentation release.

- CSV export now respects ``--cors``, fixes `#326 <https://github.com/simonw/datasette/issues/326>`_
- :ref:`Installation instructions <installation>`, including docker image - closes `#328 <https://github.com/simonw/datasette/issues/328>`_
- Fix for row pages for tables with / in, closes `#325 <https://github.com/simonw/datasette/issues/325>`_

.. _v0_23_1:

0.23.1 (2018-06-21)
-------------------

Minor bugfix release.

- Correctly display empty strings in HTML table, closes `#314 <https://github.com/simonw/datasette/issues/314>`_
- Allow "." in database filenames, closes `#302 <https://github.com/simonw/datasette/issues/302>`_
- 404s ending in slash redirect to remove that slash, closes `#309 <https://github.com/simonw/datasette/issues/309>`_
- Fixed incorrect display of compound primary keys with foreign key
  references. Closes `#319 <https://github.com/simonw/datasette/issues/319>`_
- Docs + example of canned SQL query using || concatenation. Closes `#321 <https://github.com/simonw/datasette/issues/321>`_
- Correctly display facets with value of 0 - closes `#318 <https://github.com/simonw/datasette/issues/318>`_
- Default 'expand labels' to checked in CSV advanced export

.. _v0_23:

0.23 (2018-06-18)
-----------------

This release features CSV export, improved options for foreign key expansions,
new configuration settings and improved support for SpatiaLite.

See `datasette/compare/0.22.1...0.23
<https://github.com/simonw/datasette/compare/0.22.1...0.23>`_ for a full list of
commits added since the last release.

CSV export
~~~~~~~~~~

Any Datasette table, view or custom SQL query can now be exported as CSV.

.. image:: advanced_export.png

Check out the :ref:`CSV export documentation <csv_export>` for more details, or
try the feature out on
https://fivethirtyeight.datasettes.com/fivethirtyeight/bechdel%2Fmovies

If your table has more than :ref:`config_max_returned_rows` (default 1,000)
Datasette provides the option to *stream all rows*. This option takes advantage
of async Python and Datasette's efficient :ref:`pagination <pagination>` to
iterate through the entire matching result set and stream it back as a
downloadable CSV file.

Foreign key expansions
~~~~~~~~~~~~~~~~~~~~~~

When Datasette detects a foreign key reference it attempts to resolve a label
for that reference (automatically or using the :ref:`label_columns` metadata
option) so it can display a link to the associated row.

This expansion is now also available for JSON and CSV representations of the
table, using the new ``_labels=on`` querystring option. See
:ref:`expand_foreign_keys` for more details.

New configuration settings
~~~~~~~~~~~~~~~~~~~~~~~~~~

Datasette's :ref:`config` now also supports boolean settings. A number of new
configuration options have been added:

* ``num_sql_threads`` - the number of threads used to execute SQLite queries. Defaults to 3.
* ``allow_facet`` - enable or disable custom :ref:`facets` using the `_facet=` parameter. Defaults to on.
* ``suggest_facets`` - should Datasette suggest facets? Defaults to on.
* ``allow_download`` - should users be allowed to download the entire SQLite database? Defaults to on.
* ``allow_sql`` - should users be allowed to execute custom SQL queries? Defaults to on.
* ``default_cache_ttl`` - Default HTTP caching max-age header in seconds. Defaults to 365 days - caching can be disabled entirely by settings this to 0.
* ``cache_size_kb`` - Set the amount of memory SQLite uses for its `per-connection cache <https://www.sqlite.org/pragma.html#pragma_cache_size>`_, in KB.
* ``allow_csv_stream`` - allow users to stream entire result sets as a single CSV file. Defaults to on.
* ``max_csv_mb`` - maximum size of a returned CSV file in MB. Defaults to 100MB, set to 0 to disable this limit.

Control HTTP caching with ?_ttl=
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You can now customize the HTTP max-age header that is sent on a per-URL basis, using the new ``?_ttl=`` querystring parameter.

You can set this to any value in seconds, or you can set it to 0 to disable HTTP caching entirely.

Consider for example this query which returns a randomly selected member of the Avengers::

    select * from [avengers/avengers] order by random() limit 1

If you hit the following page repeatedly you will get the same result, due to HTTP caching:

`/fivethirtyeight?sql=select+*+from+%5Bavengers%2Favengers%5D+order+by+random%28%29+limit+1 <https://fivethirtyeight.datasettes.com/fivethirtyeight?sql=select+*+from+%5Bavengers%2Favengers%5D+order+by+random%28%29+limit+1>`_

By adding `?_ttl=0` to the zero you can ensure the page will not be cached and get back a different super hero every time:

`/fivethirtyeight?sql=select+*+from+%5Bavengers%2Favengers%5D+order+by+random%28%29+limit+1&_ttl=0 <https://fivethirtyeight.datasettes.com/fivethirtyeight?sql=select+*+from+%5Bavengers%2Favengers%5D+order+by+random%28%29+limit+1&_ttl=0>`_

Improved support for SpatiaLite
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The `SpatiaLite module <https://www.gaia-gis.it/fossil/libspatialite/index>`_
for SQLite adds robust geospatial features to the database.

Getting SpatiaLite working can be tricky, especially if you want to use the most
recent alpha version (with support for K-nearest neighbor).

Datasette now includes :ref:`extensive documentation on SpatiaLite
<spatialite>`, and thanks to `Ravi Kotecha <https://github.com/r4vi>`_ our GitHub
repo includes a `Dockerfile
<https://github.com/simonw/datasette/blob/master/Dockerfile>`_ that can build
the latest SpatiaLite and configure it for use with Datasette.

The ``datasette publish`` and ``datasette package`` commands now accept a new
``--spatialite`` argument which causes them to install and configure SpatiaLite
as part of the container they deploy.

latest.datasette.io
~~~~~~~~~~~~~~~~~~~

Every commit to Datasette master is now automatically deployed by Travis CI to
https://latest.datasette.io/ - ensuring there is always a live demo of the
latest version of the software.

The demo uses `the fixtures
<https://github.com/simonw/datasette/blob/master/tests/fixtures.py>`_ from our
unit tests, ensuring it demonstrates the same range of functionality that is
covered by the tests.

You can see how the deployment mechanism works in our `.travis.yml
<https://github.com/simonw/datasette/blob/master/.travis.yml>`_ file.

Miscellaneous
~~~~~~~~~~~~~

* Got JSON data in one of your columns? Use the new ``?_json=COLNAME`` argument
  to tell Datasette to return that JSON value directly rather than encoding it
  as a string.
* If you just want an array of the first value of each row, use the new
  ``?_shape=arrayfirst`` option - `example
  <https://latest.datasette.io/fixtures.json?sql=select+neighborhood+from+facetable+order+by+pk+limit+101&_shape=arrayfirst>`_.

0.22.1 (2018-05-23)
-------------------

Bugfix release, plus we now use `versioneer <https://github.com/warner/python-versioneer>`_ for our version numbers.

- Faceting no longer breaks pagination, fixes `#282 <https://github.com/simonw/datasette/issues/282>`_
- Add ``__version_info__`` derived from `__version__` [Robert Gieseke]

  This might be tuple of more than two values (major and minor
  version) if commits have been made after a release.
- Add version number support with Versioneer. [Robert Gieseke]

  Versioneer Licence:
  Public Domain (CC0-1.0)

  Closes `#273 <https://github.com/simonw/datasette/issues/273>`_
- Refactor inspect logic [Russ Garrett]

0.22 (2018-05-20)
-----------------

The big new feature in this release is :ref:`facets`. Datasette can now apply faceted browse to any column in any table. It will also suggest possible facets. See the `Datasette Facets <https://simonwillison.net/2018/May/20/datasette-facets/>`_ announcement post for more details.

In addition to the work on facets:

- Added `docs for introspection endpoints <https://docs.datasette.io/en/stable/introspection.html>`_

- New ``--config`` option, added ``--help-config``, closes `#274 <https://github.com/simonw/datasette/issues/274>`_

  Removed the ``--page_size=`` argument to ``datasette serve`` in favour of::

      datasette serve --config default_page_size:50 mydb.db

  Added new help section::

      $ datasette --help-config
      Config options:
        default_page_size            Default page size for the table view
                                     (default=100)
        max_returned_rows            Maximum rows that can be returned from a table
                                     or custom query (default=1000)
        sql_time_limit_ms            Time limit for a SQL query in milliseconds
                                     (default=1000)
        default_facet_size           Number of values to return for requested facets
                                     (default=30)
        facet_time_limit_ms          Time limit for calculating a requested facet
                                     (default=200)
        facet_suggest_time_limit_ms  Time limit for calculating a suggested facet
                                     (default=50)
- Only apply responsive table styles to ``.rows-and-column``

  Otherwise they interfere with tables in the description, e.g. on
  https://fivethirtyeight.datasettes.com/fivethirtyeight/nba-elo%2Fnbaallelo

- Refactored views into new ``views/`` modules, refs `#256 <https://github.com/simonw/datasette/issues/256>`_
- `Documentation for SQLite full-text search <https://docs.datasette.io/en/stable/full_text_search.html>`_ support, closes `#253 <https://github.com/simonw/datasette/issues/253>`_
- ``/-/versions`` now includes SQLite ``fts_versions``, closes `#252 <https://github.com/simonw/datasette/issues/252>`_

0.21 (2018-05-05)
-----------------

New JSON ``_shape=`` options, the ability to set table ``_size=`` and a mechanism for searching within specific columns.

- Default tests to using a longer timelimit

  Every now and then a test will fail in Travis CI on Python 3.5 because it hit
  the default 20ms SQL time limit.

  Test fixtures now default to a 200ms time limit, and we only use the 20ms time
  limit for the specific test that tests query interruption. This should make
  our tests on Python 3.5 in Travis much more stable.
- Support ``_search_COLUMN=text`` searches, closes `#237 <https://github.com/simonw/datasette/issues/237>`_
- Show version on ``/-/plugins`` page, closes `#248 <https://github.com/simonw/datasette/issues/248>`_
- ``?_size=max`` option, closes `#249 <https://github.com/simonw/datasette/issues/249>`_
- Added ``/-/versions`` and ``/-/versions.json``, closes `#244 <https://github.com/simonw/datasette/issues/244>`_

  Sample output::

      {
        "python": {
          "version": "3.6.3",
          "full": "3.6.3 (default, Oct  4 2017, 06:09:38) \n[GCC 4.2.1 Compatible Apple LLVM 9.0.0 (clang-900.0.37)]"
        },
        "datasette": {
          "version": "0.20"
        },
        "sqlite": {
          "version": "3.23.1",
          "extensions": {
            "json1": null,
            "spatialite": "4.3.0a"
          }
        }
      }
- Renamed ``?_sql_time_limit_ms=`` to ``?_timelimit``, closes `#242 <https://github.com/simonw/datasette/issues/242>`_
- New ``?_shape=array`` option + tweaks to ``_shape``, closes `#245 <https://github.com/simonw/datasette/issues/245>`_

  * Default is now ``?_shape=arrays`` (renamed from ``lists``)
  * New ``?_shape=array`` returns an array of objects as the root object
  * Changed ``?_shape=object`` to return the object as the root
  * Updated docs

- FTS tables now detected by ``inspect()``, closes `#240 <https://github.com/simonw/datasette/issues/240>`_
- New ``?_size=XXX`` querystring parameter for table view, closes `#229 <https://github.com/simonw/datasette/issues/229>`_

  Also added documentation for all of the ``_special`` arguments.

  Plus deleted some duplicate logic implementing ``_group_count``.
- If ``max_returned_rows==page_size``, increment ``max_returned_rows`` - fixes `#230 <https://github.com/simonw/datasette/issues/230>`_
- New ``hidden: True`` option for table metadata, closes `#239 <https://github.com/simonw/datasette/issues/239>`_
- Hide ``idx_*`` tables if spatialite detected, closes `#228 <https://github.com/simonw/datasette/issues/228>`_
- Added ``class=rows-and-columns`` to custom query results table
- Added CSS class ``rows-and-columns`` to main table
- ``label_column`` option in ``metadata.json`` - closes `#234 <https://github.com/simonw/datasette/issues/234>`_

0.20 (2018-04-20)
-----------------

Mostly new work on the :ref:`plugins` mechanism: plugins can now bundle static assets and custom templates, and ``datasette publish`` has a new ``--install=name-of-plugin`` option.

- Add col-X classes to HTML table on custom query page
- Fixed out-dated template in documentation
- Plugins can now bundle custom templates, `#224 <https://github.com/simonw/datasette/issues/224>`_
- Added /-/metadata /-/plugins /-/inspect, `#225 <https://github.com/simonw/datasette/issues/225>`_
- Documentation for --install option, refs `#223 <https://github.com/simonw/datasette/issues/223>`_
- Datasette publish/package --install option, `#223 <https://github.com/simonw/datasette/issues/223>`_
- Fix for plugins in Python 3.5, `#222 <https://github.com/simonw/datasette/issues/222>`_
- New plugin hooks: extra_css_urls() and extra_js_urls(), `#214 <https://github.com/simonw/datasette/issues/214>`_
- /-/static-plugins/PLUGIN_NAME/ now serves static/ from plugins
- <th> now gets class="col-X" - plus added col-X documentation
- Use to_css_class for table cell column classes

  This ensures that columns with spaces in the name will still
  generate usable CSS class names. Refs `#209 <https://github.com/simonw/datasette/issues/209>`_
- Add column name classes to <td>s, make PK bold [Russ Garrett]
- Don't duplicate simple primary keys in the link column [Russ Garrett]

  When there's a simple (single-column) primary key, it looks weird to
  duplicate it in the link column.

  This change removes the second PK column and treats the link column as
  if it were the PK column from a header/sorting perspective.
- Correct escaping for HTML display of row links [Russ Garrett]
- Longer time limit for test_paginate_compound_keys

  It was failing intermittently in Travis - see `#209 <https://github.com/simonw/datasette/issues/209>`_
- Use application/octet-stream for downloadable databases
- Updated PyPI classifiers
- Updated PyPI link to pypi.org

0.19 (2018-04-16)
-----------------

This is the first preview of the new Datasette plugins mechanism. Only two
plugin hooks are available so far - for custom SQL functions and custom template
filters. There's plenty more to come - read `the documentation
<https://docs.datasette.io/en/stable/plugins.html>`_ and get involved in
`the tracking ticket <https://github.com/simonw/datasette/issues/14>`_ if you
have feedback on the direction so far.

- Fix for ``_sort_desc=sortable_with_nulls`` test, refs `#216 <https://github.com/simonw/datasette/issues/216>`_

- Fixed `#216 <https://github.com/simonw/datasette/issues/216>`_ - paginate correctly when sorting by nullable column

- Initial documentation for plugins, closes `#213 <https://github.com/simonw/datasette/issues/213>`_

  https://docs.datasette.io/en/stable/plugins.html

- New ``--plugins-dir=plugins/`` option (`#212 <https://github.com/simonw/datasette/issues/212>`_)

  New option causing Datasette to load and evaluate all of the Python files in
  the specified directory and register any plugins that are defined in those
  files.

  This new option is available for the following commands::

      datasette serve mydb.db --plugins-dir=plugins/
      datasette publish now/heroku mydb.db --plugins-dir=plugins/
      datasette package mydb.db --plugins-dir=plugins/

- Start of the plugin system, based on pluggy (`#210 <https://github.com/simonw/datasette/issues/14>`_)

  Uses https://pluggy.readthedocs.io/ originally created for the py.test project

  We're starting with two plugin hooks:

  ``prepare_connection(conn)``

  This is called when a new SQLite connection is created. It can be used to register custom SQL functions.

  ``prepare_jinja2_environment(env)``

  This is called with the Jinja2 environment. It can be used to register custom template tags and filters.

  An example plugin which uses these two hooks can be found at https://github.com/simonw/datasette-plugin-demos or installed using ``pip install datasette-plugin-demos``

  Refs `#14 <https://github.com/simonw/datasette/issues/14>`_

- Return HTTP 405 on InvalidUsage rather than 500. [Russ Garrett]

  This also stops it filling up the logs. This happens for HEAD requests
  at the moment - which perhaps should be handled better, but that's a
  different issue.


0.18 (2018-04-14)
-----------------

This release introduces `support for units <https://docs.datasette.io/en/stable/metadata.html#specifying-units-for-a-column>`_,
contributed by Russ Garrett (`#203 <https://github.com/simonw/datasette/issues/203>`_).
You can now optionally specify the units for specific columns using ``metadata.json``.
Once specified, units will be displayed in the HTML view of your table. They also become
available for use in filters - if a column is configured with a unit of distance, you can
request all rows where that column is less than 50 meters or more than 20 feet for example.

- Link foreign keys which don't have labels. [Russ Garrett]

  This renders unlabeled FKs as simple links.

  Also includes bonus fixes for two minor issues:

  * In foreign key link hrefs the primary key was escaped using HTML
    escaping rather than URL escaping. This broke some non-integer PKs.
  * Print tracebacks to console when handling 500 errors.

- Fix SQLite error when loading rows with no incoming FKs. [Russ
  Garrett]

  This fixes an error caused by an invalid query when loading incoming FKs.

  The error was ignored due to async but it still got printed to the
  console.

- Allow custom units to be registered with Pint. [Russ Garrett]
- Support units in filters. [Russ Garrett]
- Tidy up units support. [Russ Garrett]

  * Add units to exported JSON
  * Units key in metadata skeleton
  * Docs

- Initial units support. [Russ Garrett]

  Add support for specifying units for a column in ``metadata.json`` and
  rendering them on display using
  `pint <https://pint.readthedocs.io/en/latest/>`_


0.17 (2018-04-13)
-----------------
- Release 0.17 to fix issues with PyPI


0.16 (2018-04-13)
-----------------
- Better mechanism for handling errors; 404s for missing table/database

  New error mechanism closes `#193 <https://github.com/simonw/datasette/issues/193>`_

  404s for missing tables/databases closes `#184 <https://github.com/simonw/datasette/issues/184>`_

- long_description in markdown for the new PyPI
- Hide SpatiaLite system tables. [Russ Garrett]
- Allow ``explain select`` / ``explain query plan select`` `#201 <https://github.com/simonw/datasette/issues/201>`_
- Datasette inspect now finds primary_keys `#195 <https://github.com/simonw/datasette/issues/195>`_
- Ability to sort using form fields (for mobile portrait mode) `#199 <https://github.com/simonw/datasette/issues/199>`_

  We now display sort options as a select box plus a descending checkbox, which
  means you can apply sort orders even in portrait mode on a mobile phone where
  the column headers are hidden.

0.15 (2018-04-09)
-----------------

The biggest new feature in this release is the ability to sort by column. On the
table page the column headers can now be clicked to apply sort (or descending
sort), or you can specify ``?_sort=column`` or ``?_sort_desc=column`` directly
in the URL.

- ``table_rows`` => ``table_rows_count``, ``filtered_table_rows`` =>
  ``filtered_table_rows_count``

  Renamed properties. Closes `#194 <https://github.com/simonw/datasette/issues/194>`_

- New ``sortable_columns`` option in ``metadata.json`` to control sort options.

  You can now explicitly set which columns in a table can be used for sorting
  using the ``_sort`` and ``_sort_desc`` arguments using ``metadata.json``::

      {
          "databases": {
              "database1": {
                  "tables": {
                      "example_table": {
                          "sortable_columns": [
                              "height",
                              "weight"
                          ]
                      }
                  }
              }
          }
      }

  Refs `#189 <https://github.com/simonw/datasette/issues/189>`_

- Column headers now link to sort/desc sort - refs `#189 <https://github.com/simonw/datasette/issues/189>`_

- ``_sort`` and ``_sort_desc`` parameters for table views

  Allows for paginated sorted results based on a specified column.

  Refs `#189 <https://github.com/simonw/datasette/issues/189>`_

- Total row count now correct even if ``_next`` applied

- Use .custom_sql() for _group_count implementation (refs `#150 <https://github.com/simonw/datasette/issues/150>`_)

- Make HTML title more readable in query template (`#180 <https://github.com/simonw/datasette/issues/180>`_) [Ryan Pitts]

- New ``?_shape=objects/object/lists`` param for JSON API (`#192 <https://github.com/simonw/datasette/issues/192>`_)

  New ``_shape=`` parameter replacing old ``.jsono`` extension

  Now instead of this::

      /database/table.jsono

  We use the ``_shape`` parameter like this::

      /database/table.json?_shape=objects

  Also introduced a new ``_shape`` called ``object`` which looks like this::

      /database/table.json?_shape=object

  Returning an object for the rows key::

      ...
      "rows": {
          "pk1": {
              ...
          },
          "pk2": {
              ...
          }
      }

  Refs `#122 <https://github.com/simonw/datasette/issues/122>`_

- Utility for writing test database fixtures to a .db file

  ``python tests/fixtures.py /tmp/hello.db``

  This is useful for making a SQLite database of the test fixtures for
  interactive exploration.

- Compound primary key ``_next=`` now plays well with extra filters

  Closes `#190 <https://github.com/simonw/datasette/issues/190>`_

- Fixed bug with keyset pagination over compound primary keys

  Refs `#190 <https://github.com/simonw/datasette/issues/190>`_

- Database/Table views inherit ``source/license/source_url/license_url``
  metadata

  If you set the ``source_url/license_url/source/license`` fields in your root
  metadata those values will now be inherited all the way down to the database
  and table templates.

  The ``title/description`` are NOT inherited.

  Also added unit tests for the HTML generated by the metadata.

  Refs `#185 <https://github.com/simonw/datasette/issues/185>`_

- Add metadata, if it exists, to heroku temp dir (`#178 <https://github.com/simonw/datasette/issues/178>`_) [Tony Hirst]
- Initial documentation for pagination
- Broke up test_app into test_api and test_html
- Fixed bug with .json path regular expression

  I had a table called ``geojson`` and it caused an exception because the regex
  was matching ``.json`` and not ``\.json``

- Deploy to Heroku with Python 3.6.3

0.14 (2017-12-09)
-----------------

The theme of this release is customization: Datasette now allows every aspect
of its presentation `to be customized <https://docs.datasette.io/en/stable/custom_templates.html>`_
either using additional CSS or by providing entirely new templates.

Datasette's `metadata.json format <https://docs.datasette.io/en/stable/metadata.html>`_
has also been expanded, to allow per-database and per-table metadata. A new
``datasette skeleton`` command can be used to generate a skeleton JSON file
ready to be filled in with per-database and per-table details.

The ``metadata.json`` file can also be used to define
`canned queries <https://docs.datasette.io/en/stable/sql_queries.html#canned-queries>`_,
as a more powerful alternative to SQL views.

- ``extra_css_urls``/``extra_js_urls`` in metadata

  A mechanism in the ``metadata.json`` format for adding custom CSS and JS urls.

  Create a ``metadata.json`` file that looks like this::

      {
          "extra_css_urls": [
              "https://simonwillison.net/static/css/all.bf8cd891642c.css"
          ],
          "extra_js_urls": [
              "https://code.jquery.com/jquery-3.2.1.slim.min.js"
          ]
      }

  Then start datasette like this::

      datasette mydb.db --metadata=metadata.json

  The CSS and JavaScript files will be linked in the ``<head>`` of every page.

  You can also specify a SRI (subresource integrity hash) for these assets::

      {
          "extra_css_urls": [
              {
                  "url": "https://simonwillison.net/static/css/all.bf8cd891642c.css",
                  "sri": "sha384-9qIZekWUyjCyDIf2YK1FRoKiPJq4PHt6tp/ulnuuyRBvazd0hG7pWbE99zvwSznI"
              }
          ],
          "extra_js_urls": [
              {
                  "url": "https://code.jquery.com/jquery-3.2.1.slim.min.js",
                  "sri": "sha256-k2WSCIexGzOj3Euiig+TlR8gA0EmPjuc79OEeY5L45g="
              }
          ]
      }

  Modern browsers will only execute the stylesheet or JavaScript if the SRI hash
  matches the content served. You can generate hashes using https://www.srihash.org/

- Auto-link column values that look like URLs (`#153 <https://github.com/simonw/datasette/issues/153>`_)

- CSS styling hooks as classes on the body (`#153 <https://github.com/simonw/datasette/issues/153>`_)

  Every template now gets CSS classes in the body designed to support custom
  styling.

  The index template (the top level page at ``/``) gets this::

      <body class="index">

  The database template (``/dbname/``) gets this::

      <body class="db db-dbname">

  The table template (``/dbname/tablename``) gets::

      <body class="table db-dbname table-tablename">

  The row template (``/dbname/tablename/rowid``) gets::

      <body class="row db-dbname table-tablename">

  The ``db-x`` and ``table-x`` classes use the database or table names themselves IF
  they are valid CSS identifiers. If they aren't, we strip any invalid
  characters out and append a 6 character md5 digest of the original name, in
  order to ensure that multiple tables which resolve to the same stripped
  character version still have different CSS classes.

  Some examples (extracted from the unit tests)::

      "simple" => "simple"
      "MixedCase" => "MixedCase"
      "-no-leading-hyphens" => "no-leading-hyphens-65bea6"
      "_no-leading-underscores" => "no-leading-underscores-b921bc"
      "no spaces" => "no-spaces-7088d7"
      "-" => "336d5e"
      "no $ characters" => "no--characters-59e024"

- ``datasette --template-dir=mytemplates/`` argument

  You can now pass an additional argument specifying a directory to look for
  custom templates in.

  Datasette will fall back on the default templates if a template is not
  found in that directory.

- Ability to over-ride templates for individual tables/databases.

  It is now possible to over-ride templates on a per-database / per-row or per-
  table basis.

  When you access e.g. ``/mydatabase/mytable`` Datasette will look for the following::

      - table-mydatabase-mytable.html
      - table.html

  If you provided a ``--template-dir`` argument to datasette serve it will look in
  that directory first.

  The lookup rules are as follows::

      Index page (/):
          index.html

      Database page (/mydatabase):
          database-mydatabase.html
          database.html

      Table page (/mydatabase/mytable):
          table-mydatabase-mytable.html
          table.html

      Row page (/mydatabase/mytable/id):
          row-mydatabase-mytable.html
          row.html

  If a table name has spaces or other unexpected characters in it, the template
  filename will follow the same rules as our custom ``<body>`` CSS classes
  - for example, a table called "Food Trucks"
  will attempt to load the following templates::

      table-mydatabase-Food-Trucks-399138.html
      table.html

  It is possible to extend the default templates using Jinja template
  inheritance. If you want to customize EVERY row template with some additional
  content you can do so by creating a row.html template like this::

      {% extends "default:row.html" %}

      {% block content %}
      <h1>EXTRA HTML AT THE TOP OF THE CONTENT BLOCK</h1>
      <p>This line renders the original block:</p>
      {{ super() }}
      {% endblock %}

- ``--static`` option for datasette serve (`#160 <https://github.com/simonw/datasette/issues/160>`_)

  You can now tell Datasette to serve static files from a specific location at a
  specific mountpoint.

  For example::

    datasette serve mydb.db --static extra-css:/tmp/static/css

  Now if you visit this URL::

    http://localhost:8001/extra-css/blah.css

  The following file will be served::

    /tmp/static/css/blah.css

- Canned query support.

  Named canned queries can now be defined in ``metadata.json`` like this::

      {
          "databases": {
              "timezones": {
                  "queries": {
                      "timezone_for_point": "select tzid from timezones ..."
                  }
              }
          }
      }

  These will be shown in a new "Queries" section beneath "Views" on the database page.

- New ``datasette skeleton`` command for generating ``metadata.json`` (`#164 <https://github.com/simonw/datasette/issues/164>`_)

- ``metadata.json`` support for per-table/per-database metadata (`#165 <https://github.com/simonw/datasette/issues/165>`_)

  Also added support for descriptions and HTML descriptions.

  Here's an example metadata.json file illustrating custom per-database and per-
  table metadata::

      {
          "title": "Overall datasette title",
          "description_html": "This is a <em>description with HTML</em>.",
          "databases": {
              "db1": {
                  "title": "First database",
                  "description": "This is a string description & has no HTML",
                  "license_url": "http://example.com/",
              "license": "The example license",
                  "queries": {
                    "canned_query": "select * from table1 limit 3;"
                  },
                  "tables": {
                      "table1": {
                          "title": "Custom title for table1",
                          "description": "Tables can have descriptions too",
                          "source": "This has a custom source",
                          "source_url": "http://example.com/"
                      }
                  }
              }
          }
      }

- Renamed ``datasette build`` command to ``datasette inspect`` (`#130 <https://github.com/simonw/datasette/issues/130>`_)

- Upgrade to Sanic 0.7.0 (`#168 <https://github.com/simonw/datasette/issues/168>`_)

  https://github.com/channelcat/sanic/releases/tag/0.7.0

- Package and publish commands now accept ``--static`` and ``--template-dir``

  Example usage::

      datasette package --static css:extra-css/ --static js:extra-js/ \
        sf-trees.db --template-dir templates/ --tag sf-trees --branch master

  This creates a local Docker image that includes copies of the templates/,
  extra-css/ and extra-js/ directories. You can then run it like this::

    docker run -p 8001:8001 sf-trees

  For publishing to Zeit now::

    datasette publish now --static css:extra-css/ --static js:extra-js/ \
      sf-trees.db --template-dir templates/ --name sf-trees --branch master

- HTML comment showing which templates were considered for a page (`#171 <https://github.com/simonw/datasette/issues/171>`_)

0.13 (2017-11-24)
-----------------
- Search now applies to current filters.

  Combined search into the same form as filters.

  Closes `#133`_

- Much tidier design for table view header.

  Closes `#147`_

- Added ``?column__not=blah`` filter.

  Closes `#148`_

- Row page now resolves foreign keys.

  Closes `#132`_

- Further tweaks to select/input filter styling.

  Refs `#86`_ - thanks for the help, @natbat!

- Show linked foreign key in table cells.

- Added UI for editing table filters.

  Refs `#86`_

- Hide FTS-created tables on index pages.

  Closes `#129`_

- Add publish to heroku support [Jacob Kaplan-Moss]

  ``datasette publish heroku mydb.db``

  Pull request `#104`_

- Initial implementation of ``?_group_count=column``.

  URL shortcut for counting rows grouped by one or more columns.

  ``?_group_count=column1&_group_count=column2`` works as well.

  SQL generated looks like this::

      select "qSpecies", count(*) as "count"
      from Street_Tree_List
      group by "qSpecies"
      order by "count" desc limit 100

  Or for two columns like this::

      select "qSpecies", "qSiteInfo", count(*) as "count"
      from Street_Tree_List
      group by "qSpecies", "qSiteInfo"
      order by "count" desc limit 100

  Refs `#44`_

- Added ``--build=master`` option to datasette publish and package.

  The ``datasette publish`` and ``datasette package`` commands both now accept an
  optional ``--build`` argument. If provided, this can be used to specify a branch
  published to GitHub that should be built into the container.

  This makes it easier to test code that has not yet been officially released to
  PyPI, e.g.::

      datasette publish now mydb.db --branch=master

- Implemented ``?_search=XXX`` + UI if a FTS table is detected.

  Closes `#131`_

- Added ``datasette --version`` support.

- Table views now show expanded foreign key references, if possible.

  If a table has foreign key columns, and those foreign key tables have
  ``label_columns``, the TableView will now query those other tables for the
  corresponding values and display those values as links in the corresponding
  table cells.

  label_columns are currently detected by the ``inspect()`` function, which looks
  for any table that has just two columns - an ID column and one other - and
  sets the ``label_column`` to be that second non-ID column.

- Don't prevent tabbing to "Run SQL" button (`#117`_) [Robert Gieseke]

  See comment in `#115`_

- Add keyboard shortcut to execute SQL query (`#115`_) [Robert Gieseke]

- Allow ``--load-extension`` to be set via environment variable.

- Add support for ``?field__isnull=1`` (`#107`_) [Ray N]

- Add spatialite, switch to debian and local build (`#114`_) [Ariel Núñez]

- Added ``--load-extension`` argument to datasette serve.

  Allows loading of SQLite extensions. Refs `#110`_.

.. _#133: https://github.com/simonw/datasette/issues/133
.. _#147: https://github.com/simonw/datasette/issues/147
.. _#148: https://github.com/simonw/datasette/issues/148
.. _#132: https://github.com/simonw/datasette/issues/132
.. _#86: https://github.com/simonw/datasette/issues/86
.. _#129: https://github.com/simonw/datasette/issues/129
.. _#104: https://github.com/simonw/datasette/issues/104
.. _#44: https://github.com/simonw/datasette/issues/44
.. _#131: https://github.com/simonw/datasette/issues/131
.. _#115: https://github.com/simonw/datasette/issues/115
.. _#117: https://github.com/simonw/datasette/issues/117
.. _#107: https://github.com/simonw/datasette/issues/107
.. _#114: https://github.com/simonw/datasette/issues/114
.. _#110: https://github.com/simonw/datasette/issues/110

0.12 (2017-11-16)
-----------------
- Added ``__version__``, now displayed as tooltip in page footer (`#108`_).
- Added initial docs, including a changelog (`#99`_).
- Turned on auto-escaping in Jinja.
- Added a UI for editing named parameters (`#96`_).

  You can now construct a custom SQL statement using SQLite named
  parameters (e.g. ``:name``) and datasette will display form fields for
  editing those parameters. `Here’s an example`_ which lets you see the
  most popular names for dogs of different species registered through
  various dog registration schemes in Australia.

.. _Here’s an example: https://australian-dogs.now.sh/australian-dogs-3ba9628?sql=select+name%2C+count%28*%29+as+n+from+%28%0D%0A%0D%0Aselect+upper%28%22Animal+name%22%29+as+name+from+%5BAdelaide-City-Council-dog-registrations-2013%5D+where+Breed+like+%3Abreed%0D%0A%0D%0Aunion+all%0D%0A%0D%0Aselect+upper%28Animal_Name%29+as+name+from+%5BAdelaide-City-Council-dog-registrations-2014%5D+where+Breed_Description+like+%3Abreed%0D%0A%0D%0Aunion+all+%0D%0A%0D%0Aselect+upper%28Animal_Name%29+as+name+from+%5BAdelaide-City-Council-dog-registrations-2015%5D+where+Breed_Description+like+%3Abreed%0D%0A%0D%0Aunion+all%0D%0A%0D%0Aselect+upper%28%22AnimalName%22%29+as+name+from+%5BCity-of-Port-Adelaide-Enfield-Dog_Registrations_2016%5D+where+AnimalBreed+like+%3Abreed%0D%0A%0D%0Aunion+all%0D%0A%0D%0Aselect+upper%28%22Animal+Name%22%29+as+name+from+%5BMitcham-dog-registrations-2015%5D+where+Breed+like+%3Abreed%0D%0A%0D%0Aunion+all%0D%0A%0D%0Aselect+upper%28%22DOG_NAME%22%29+as+name+from+%5Bburnside-dog-registrations-2015%5D+where+DOG_BREED+like+%3Abreed%0D%0A%0D%0Aunion+all+%0D%0A%0D%0Aselect+upper%28%22Animal_Name%22%29+as+name+from+%5Bcity-of-playford-2015-dog-registration%5D+where+Breed_Description+like+%3Abreed%0D%0A%0D%0Aunion+all%0D%0A%0D%0Aselect+upper%28%22Animal+Name%22%29+as+name+from+%5Bcity-of-prospect-dog-registration-details-2016%5D+where%22Breed+Description%22+like+%3Abreed%0D%0A%0D%0A%29+group+by+name+order+by+n+desc%3B&breed=pug

- Pin to specific Jinja version. (`#100`_).
- Default to 127.0.0.1 not 0.0.0.0. (`#98`_).
- Added extra metadata options to publish and package commands. (`#92`_).

  You can now run these commands like so::

      datasette now publish mydb.db \
          --title="My Title" \
          --source="Source" \
          --source_url="http://www.example.com/" \
          --license="CC0" \
          --license_url="https://creativecommons.org/publicdomain/zero/1.0/"

  This will write those values into the metadata.json that is packaged with the
  app. If you also pass ``--metadata=metadata.json`` that file will be updated with the extra
  values before being written into the Docker image.
- Added simple production-ready Dockerfile (`#94`_) [Andrew
  Cutler]
- New ``?_sql_time_limit_ms=10`` argument to database and table page (`#95`_)
- SQL syntax highlighting with Codemirror (`#89`_) [Tom Dyson]

.. _#89: https://github.com/simonw/datasette/issues/89
.. _#92: https://github.com/simonw/datasette/issues/92
.. _#94: https://github.com/simonw/datasette/issues/94
.. _#95: https://github.com/simonw/datasette/issues/95
.. _#96: https://github.com/simonw/datasette/issues/96
.. _#98: https://github.com/simonw/datasette/issues/98
.. _#99: https://github.com/simonw/datasette/issues/99
.. _#100: https://github.com/simonw/datasette/issues/100
.. _#108: https://github.com/simonw/datasette/issues/108

0.11 (2017-11-14)
-----------------
- Added ``datasette publish now --force`` option.

  This calls ``now`` with ``--force`` - useful as it means you get a fresh copy of datasette even if Now has already cached that docker layer.
- Enable ``--cors`` by default when running in a container.

0.10 (2017-11-14)
-----------------
- Fixed `#83`_ - 500 error on individual row pages.
- Stop using sqlite WITH RECURSIVE in our tests.

  The version of Python 3 running in Travis CI doesn't support this.

.. _#83: https://github.com/simonw/datasette/issues/83

0.9 (2017-11-13)
----------------
- Added ``--sql_time_limit_ms`` and ``--extra-options``.

  The serve command now accepts ``--sql_time_limit_ms`` for customizing the SQL time
  limit.

  The publish and package commands now accept ``--extra-options`` which can be used
  to specify additional options to be passed to the datasite serve command when
  it executes inside the resulting Docker containers.

0.8 (2017-11-13)
----------------
- V0.8 - added PyPI metadata, ready to ship.
- Implemented offset/limit pagination for views (`#70`_).
- Improved pagination. (`#78`_)
- Limit on max rows returned, controlled by ``--max_returned_rows`` option. (`#69`_)

  If someone executes 'select * from table' against a table with a million rows
  in it, we could run into problems: just serializing that much data as JSON is
  likely to lock up the server.

  Solution: we now have a hard limit on the maximum number of rows that can be
  returned by a query. If that limit is exceeded, the server will return a
  ``"truncated": true`` field in the JSON.

  This limit can be optionally controlled by the new ``--max_returned_rows``
  option. Setting that option to 0 disables the limit entirely.

.. _#70: https://github.com/simonw/datasette/issues/70
.. _#78: https://github.com/simonw/datasette/issues/78
.. _#69: https://github.com/simonw/datasette/issues/69
