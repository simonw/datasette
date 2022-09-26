.. _plugins:

Plugins
=======

Datasette's plugin system allows additional features to be implemented as Python
code (or front-end JavaScript) which can be wrapped up in a separate Python
package. The underlying mechanism uses `pluggy <https://pluggy.readthedocs.io/>`_.

See the `Datasette plugins directory <https://datasette.io/plugins>`__ for a list of existing plugins, or take a look at the
`datasette-plugin <https://github.com/topics/datasette-plugin>`__ topic on GitHub.

Things you can do with plugins include:

* Add visualizations to Datasette, for example
  `datasette-cluster-map <https://github.com/simonw/datasette-cluster-map>`__ and
  `datasette-vega <https://github.com/simonw/datasette-vega>`__.
* Make new custom SQL functions available for use within Datasette, for example
  `datasette-haversine <https://github.com/simonw/datasette-haversine>`__ and
  `datasette-jellyfish <https://github.com/simonw/datasette-jellyfish>`__.
* Define custom output formats with custom extensions, for example `datasette-atom <https://github.com/simonw/datasette-atom>`__ and
  `datasette-ics <https://github.com/simonw/datasette-ics>`__.
* Add template functions that can be called within your Jinja custom templates,
  for example `datasette-render-markdown <https://github.com/simonw/datasette-render-markdown#markdown-in-templates>`__.
* Customize how database values are rendered in the Datasette interface, for example
  `datasette-render-binary <https://github.com/simonw/datasette-render-binary>`__ and
  `datasette-pretty-json <https://github.com/simonw/datasette-pretty-json>`__.
* Customize how Datasette's authentication and permissions systems work, for example `datasette-auth-tokens <https://github.com/simonw/datasette-auth-tokens>`__ and
  `datasette-permissions-sql <https://github.com/simonw/datasette-permissions-sql>`__.

.. _plugins_installing:

Installing plugins
------------------

If a plugin has been packaged for distribution using setuptools you can use the plugin by installing it alongside Datasette in the same virtual environment or Docker container.

You can install plugins using the ``datasette install`` command::

    datasette install datasette-vega

You can uninstall plugins with ``datasette uninstall``::

    datasette uninstall datasette-vega

You can upgrade plugins with ``datasette install --upgrade`` or ``datasette install -U``::

    datasette install -U datasette-vega

This command can also be used to upgrade Datasette itself to the latest released version::

    datasette install -U datasette

These commands are thin wrappers around ``pip install`` and ``pip uninstall``, which ensure they run ``pip`` in the same virtual environment as Datasette itself.

One-off plugins using --plugins-dir
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You can also define one-off per-project plugins by saving them as ``plugin_name.py`` functions in a ``plugins/`` folder and then passing that folder to ``datasette`` using the ``--plugins-dir`` option::

    datasette mydb.db --plugins-dir=plugins/

Deploying plugins using datasette publish
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``datasette publish`` and ``datasette package`` commands both take an optional ``--install`` argument. You can use this one or more times to tell Datasette to ``pip install`` specific plugins as part of the process::

    datasette publish cloudrun mydb.db --install=datasette-vega

You can use the name of a package on PyPI or any of the other valid arguments to ``pip install`` such as a URL to a ``.zip`` file::

    datasette publish cloudrun mydb.db \
        --install=https://url-to-my-package.zip

.. _plugins_installed:

Seeing what plugins are installed
---------------------------------

You can see a list of installed plugins by navigating to the ``/-/plugins`` page of your Datasette instance - for example: https://fivethirtyeight.datasettes.com/-/plugins

You can also use the ``datasette plugins`` command::

    $ datasette plugins
    [
        {
            "name": "datasette_json_html",
            "static": false,
            "templates": false,
            "version": "0.4.0"
        }
    ]

.. [[[cog
    from datasette import cli
    from click.testing import CliRunner
    import textwrap, json
    cog.out("\n")
    result = CliRunner().invoke(cli.cli, ["plugins", "--all"])
    # cog.out() with text containing newlines was unindenting for some reason
    cog.outl("If you run ``datasette plugins --all`` it will include default plugins that ship as part of Datasette::\n")
    plugins = [p for p in json.loads(result.output) if p["name"].startswith("datasette.")]
    indented = textwrap.indent(json.dumps(plugins, indent=4), "    ")
    for line in indented.split("\n"):
        cog.outl(line)
    cog.out("\n\n")
.. ]]]

If you run ``datasette plugins --all`` it will include default plugins that ship as part of Datasette::

    [
        {
            "name": "datasette.actor_auth_cookie",
            "static": false,
            "templates": false,
            "version": null,
            "hooks": [
                "actor_from_request"
            ]
        },
        {
            "name": "datasette.blob_renderer",
            "static": false,
            "templates": false,
            "version": null,
            "hooks": [
                "register_output_renderer"
            ]
        },
        {
            "name": "datasette.default_magic_parameters",
            "static": false,
            "templates": false,
            "version": null,
            "hooks": [
                "register_magic_parameters"
            ]
        },
        {
            "name": "datasette.default_menu_links",
            "static": false,
            "templates": false,
            "version": null,
            "hooks": [
                "menu_links"
            ]
        },
        {
            "name": "datasette.default_permissions",
            "static": false,
            "templates": false,
            "version": null,
            "hooks": [
                "permission_allowed"
            ]
        },
        {
            "name": "datasette.facets",
            "static": false,
            "templates": false,
            "version": null,
            "hooks": [
                "register_facet_classes"
            ]
        },
        {
            "name": "datasette.filters",
            "static": false,
            "templates": false,
            "version": null,
            "hooks": [
                "filters_from_request"
            ]
        },
        {
            "name": "datasette.forbidden",
            "static": false,
            "templates": false,
            "version": null,
            "hooks": [
                "forbidden"
            ]
        },
        {
            "name": "datasette.handle_exception",
            "static": false,
            "templates": false,
            "version": null,
            "hooks": [
                "handle_exception"
            ]
        },
        {
            "name": "datasette.publish.cloudrun",
            "static": false,
            "templates": false,
            "version": null,
            "hooks": [
                "publish_subcommand"
            ]
        },
        {
            "name": "datasette.publish.heroku",
            "static": false,
            "templates": false,
            "version": null,
            "hooks": [
                "publish_subcommand"
            ]
        },
        {
            "name": "datasette.sql_functions",
            "static": false,
            "templates": false,
            "version": null,
            "hooks": [
                "prepare_connection"
            ]
        }
    ]


.. [[[end]]]

You can add the ``--plugins-dir=`` option to include any plugins found in that directory.

.. _plugins_configuration:

Plugin configuration
--------------------

Plugins can have their own configuration, embedded in a :ref:`metadata` file. Configuration options for plugins live within a ``"plugins"`` key in that file, which can be included at the root, database or table level.

Here is an example of some plugin configuration for a specific table:

.. code-block:: json

    {
        "databases": {
            "sf-trees": {
                "tables": {
                    "Street_Tree_List": {
                        "plugins": {
                            "datasette-cluster-map": {
                                "latitude_column": "lat",
                                "longitude_column": "lng"
                            }
                        }
                    }
                }
            }
        }
    }

This tells the ``datasette-cluster-map`` column which latitude and longitude columns should be used for a table called ``Street_Tree_List`` inside a database file called ``sf-trees.db``.

.. _plugins_configuration_secret:

Secret configuration values
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Any values embedded in ``metadata.json`` will be visible to anyone who views the ``/-/metadata`` page of your Datasette instance. Some plugins may need configuration that should stay secret - API keys for example. There are two ways in which you can store secret configuration values.

**As environment variables**. If your secret lives in an environment variable that is available to the Datasette process, you can indicate that the configuration value should be read from that environment variable like so:

.. code-block:: json

    {
        "plugins": {
            "datasette-auth-github": {
                "client_secret": {
                    "$env": "GITHUB_CLIENT_SECRET"
                }
            }
        }
    }

**As values in separate files**. Your secrets can also live in files on disk. To specify a secret should be read from a file, provide the full file path like this:

.. code-block:: json

    {
        "plugins": {
            "datasette-auth-github": {
                "client_secret": {
                    "$file": "/secrets/client-secret"
                }
            }
        }
    }

If you are publishing your data using the :ref:`datasette publish <cli_publish>` family of commands, you can use the ``--plugin-secret`` option to set these secrets at publish time. For example, using Heroku you might run the following command::

    $ datasette publish heroku my_database.db \
        --name my-heroku-app-demo \
        --install=datasette-auth-github \
        --plugin-secret datasette-auth-github client_id your_client_id \
        --plugin-secret datasette-auth-github client_secret your_client_secret

This will set the necessary environment variables and add the following to the deployed ``metadata.json``:

.. code-block:: json

    {
        "plugins": {
            "datasette-auth-github": {
                "client_id": {
                    "$env": "DATASETTE_AUTH_GITHUB_CLIENT_ID"
                },
                "client_secret": {
                    "$env": "DATASETTE_AUTH_GITHUB_CLIENT_SECRET"
                }
            }
        }
    }
