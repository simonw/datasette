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
* Customize how Datasette's authentication and permissions systems work, for example `datasette-auth-passwords <https://github.com/simonw/datasette-auth-passwords>`__ and
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

You can install multiple plugins at once by listing them as lines in a ``requirements.txt`` file like this::

    datasette-vega
    datasette-cluster-map

Then pass that file to ``datasette install -r``::

    datasette install -r requirements.txt

The ``install`` and ``uninstall`` commands are thin wrappers around ``pip install`` and ``pip uninstall``, which ensure that they run ``pip`` in the same virtual environment as Datasette itself.

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


.. _plugins_datasette_load_plugins:

Controlling which plugins are loaded
------------------------------------

Datasette defaults to loading every plugin that is installed in the same virtual environment as Datasette itself.

You can set the ``DATASETTE_LOAD_PLUGINS`` environment variable to a comma-separated list of plugin names to load a controlled subset of plugins instead.

For example, to load just the ``datasette-vega`` and ``datasette-cluster-map`` plugins, set ``DATASETTE_LOAD_PLUGINS`` to ``datasette-vega,datasette-cluster-map``:

.. code-block:: bash

    export DATASETTE_LOAD_PLUGINS='datasette-vega,datasette-cluster-map'
    datasette mydb.db

Or:

.. code-block:: bash

    DATASETTE_LOAD_PLUGINS='datasette-vega,datasette-cluster-map' \
      datasette mydb.db

To disable the loading of all additional plugins, set ``DATASETTE_LOAD_PLUGINS`` to an empty string:

.. code-block:: bash

    export DATASETTE_LOAD_PLUGINS=''
    datasette mydb.db

A quick way to test this setting is to use it with the ``datasette plugins`` command:

.. code-block:: bash

    DATASETTE_LOAD_PLUGINS='datasette-vega' datasette plugins

This should output the following:

.. code-block:: json

    [
        {
            "name": "datasette-vega",
            "static": true,
            "templates": false,
            "version": "0.6.2",
            "hooks": [
                "extra_css_urls",
                "extra_js_urls"
            ]
        }
    ]

.. _plugins_installed:

Seeing what plugins are installed
---------------------------------

You can see a list of installed plugins by navigating to the ``/-/plugins`` page of your Datasette instance - for example: https://fivethirtyeight.datasettes.com/-/plugins

You can also use the ``datasette plugins`` command::

    datasette plugins

Which outputs:

.. code-block:: json

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
    cog.outl("If you run ``datasette plugins --all`` it will include default plugins that ship as part of Datasette:\n")
    cog.outl(".. code-block:: json\n")
    plugins = [p for p in json.loads(result.output) if p["name"].startswith("datasette.")]
    indented = textwrap.indent(json.dumps(plugins, indent=4), "    ")
    for line in indented.split("\n"):
        cog.outl(line)
    cog.out("\n\n")
.. ]]]

If you run ``datasette plugins --all`` it will include default plugins that ship as part of Datasette:

.. code-block:: json

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
            "name": "datasette.default_actions",
            "static": false,
            "templates": false,
            "version": null,
            "hooks": [
                "register_actions"
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
                "actor_from_request",
                "canned_queries",
                "permission_resources_sql",
                "skip_csrf"
            ]
        },
        {
            "name": "datasette.events",
            "static": false,
            "templates": false,
            "version": null,
            "hooks": [
                "register_events"
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

Add ``--requirements`` to output a list of installed plugins that can then be installed in another Datasette instance using ``datasette install -r requirements.txt``::

    datasette plugins --requirements

The output will look something like this::

    datasette-codespaces==0.1.1
    datasette-graphql==2.2
    datasette-json-html==1.0.1
    datasette-pretty-json==0.2.2
    datasette-x-forwarded-host==0.1

To write that to a ``requirements.txt`` file, run this::

    datasette plugins --requirements > requirements.txt

.. _plugins_configuration:

Plugin configuration
--------------------

Plugins can have their own configuration, embedded in a :ref:`configuration file <configuration>`. Configuration options for plugins live within a ``"plugins"`` key in that file, which can be included at the root, database or table level.

Here is an example of some plugin configuration for a specific table:

.. [[[cog
    from metadata_doc import config_example
    config_example(cog, {
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
    })
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        databases:
          sf-trees:
            tables:
              Street_Tree_List:
                plugins:
                  datasette-cluster-map:
                    latitude_column: lat
                    longitude_column: lng


.. tab:: datasette.json

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
.. [[[end]]]

This tells the ``datasette-cluster-map`` column which latitude and longitude columns should be used for a table called ``Street_Tree_List`` inside a database file called ``sf-trees.db``.

.. _plugins_configuration_secret:

Secret configuration values
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Some plugins may need configuration that should stay secret - API keys for example. There are two ways in which you can store secret configuration values.

**As environment variables**. If your secret lives in an environment variable that is available to the Datasette process, you can indicate that the configuration value should be read from that environment variable like so:

.. [[[cog
    config_example(cog, {
        "plugins": {
            "datasette-auth-github": {
                "client_secret": {
                    "$env": "GITHUB_CLIENT_SECRET"
                }
            }
        }
    })
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        plugins:
          datasette-auth-github:
            client_secret:
              $env: GITHUB_CLIENT_SECRET


.. tab:: datasette.json

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
.. [[[end]]]

**As values in separate files**. Your secrets can also live in files on disk. To specify a secret should be read from a file, provide the full file path like this:

.. [[[cog
    config_example(cog, {
        "plugins": {
            "datasette-auth-github": {
                "client_secret": {
                    "$file": "/secrets/client-secret"
                }
            }
        }
    })
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        plugins:
          datasette-auth-github:
            client_secret:
              $file: /secrets/client-secret


.. tab:: datasette.json

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
.. [[[end]]]

If you are publishing your data using the :ref:`datasette publish <cli_publish>` family of commands, you can use the ``--plugin-secret`` option to set these secrets at publish time. For example, using Heroku you might run the following command::

    datasette publish heroku my_database.db \
        --name my-heroku-app-demo \
        --install=datasette-auth-github \
        --plugin-secret datasette-auth-github client_id your_client_id \
        --plugin-secret datasette-auth-github client_secret your_client_secret

This will set the necessary environment variables and add the following to the deployed ``metadata.yaml``:

.. [[[cog
    config_example(cog, {
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
    })
.. ]]]

.. tab:: datasette.yaml

    .. code-block:: yaml

        plugins:
          datasette-auth-github:
            client_id:
              $env: DATASETTE_AUTH_GITHUB_CLIENT_ID
            client_secret:
              $env: DATASETTE_AUTH_GITHUB_CLIENT_SECRET


.. tab:: datasette.json

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
.. [[[end]]]
