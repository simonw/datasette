.. _plugins:

Plugins
=======

Datasette's plugin system allows additional features to be implemented as Python
code (or front-end JavaScript) which can be wrapped up in a separate Python
package. The underlying mechanism uses `pluggy <https://pluggy.readthedocs.io/>`_.

See :ref:`ecosystem_plugins` for a list of existing plugins, or take a look at the
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

If a plugin has been packaged for distribution using setuptools you can use
the plugin by installing it alongside Datasette in the same virtual
environment or Docker container.

You can also define one-off per-project plugins by saving them as
``plugin_name.py`` functions in a ``plugins/`` folder and then passing that
folder to ``datasette serve``.

The ``datasette publish`` and ``datasette package`` commands both take an
optional ``--install`` argument. You can use this one or more times to tell
Datasette to ``pip install`` specific plugins as part of the process. You can
use the name of a package on PyPI or any of the other valid arguments to ``pip
install`` such as a URL to a ``.zip`` file::

    datasette publish cloudrun mydb.db \
        --install=datasette-plugin-demos \
        --install=https://url-to-my-package.zip

.. _plugins_writing_one_off:

Writing one-off plugins
-----------------------

The easiest way to write a plugin is to create a ``my_plugin.py`` file and
drop it into your ``plugins/`` directory. Here is an example plugin, which
adds a new custom SQL function called ``hello_world()`` which takes no
arguments and returns the string ``Hello world!``.

.. code-block:: python

    from datasette import hookimpl

    @hookimpl
    def prepare_connection(conn):
        conn.create_function('hello_world', 0, lambda: 'Hello world!')

If you save this in ``plugins/my_plugin.py`` you can then start Datasette like
this::

    datasette serve mydb.db --plugins-dir=plugins/

Now you can navigate to http://localhost:8001/mydb and run this SQL::

    select hello_world();

To see the output of your plugin.

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

If you run ``datasette plugins --all`` it will include default plugins that ship as part of Datasette::

    $ datasette plugins --all
    [
        {
            "name": "datasette.sql_functions",
            "static": false,
            "templates": false,
            "version": null
        },
        {
            "name": "datasette.publish.cloudrun",
            "static": false,
            "templates": false,
            "version": null
        },
        {
            "name": "datasette.facets",
            "static": false,
            "templates": false,
            "version": null
        },
        {
            "name": "datasette.publish.heroku",
            "static": false,
            "templates": false,
            "version": null
        }
    ]

You can add the ``--plugins-dir=`` option to include any plugins found in that directory.

Packaging a plugin
------------------

Plugins can be packaged using Python setuptools. You can see an example of a
packaged plugin at https://github.com/simonw/datasette-plugin-demos

The example consists of two files: a ``setup.py`` file that defines the plugin:

.. code-block:: python

    from setuptools import setup

    VERSION = '0.1'

    setup(
        name='datasette-plugin-demos',
        description='Examples of plugins for Datasette',
        author='Simon Willison',
        url='https://github.com/simonw/datasette-plugin-demos',
        license='Apache License, Version 2.0',
        version=VERSION,
        py_modules=['datasette_plugin_demos'],
        entry_points={
            'datasette': [
                'plugin_demos = datasette_plugin_demos'
            ]
        },
        install_requires=['datasette']
    )

And a Python module file, ``datasette_plugin_demos.py``, that implements the
plugin:

.. code-block:: python

    from datasette import hookimpl
    import random


    @hookimpl
    def prepare_jinja2_environment(env):
        env.filters['uppercase'] = lambda u: u.upper()


    @hookimpl
    def prepare_connection(conn):
        conn.create_function('random_integer', 2, random.randint)


Having built a plugin in this way you can turn it into an installable package
using the following command::

    python3 setup.py sdist

This will create a ``.tar.gz`` file in the ``dist/`` directory.

You can then install your new plugin into a Datasette virtual environment or
Docker container using ``pip``::

    pip install datasette-plugin-demos-0.1.tar.gz

To learn how to upload your plugin to `PyPI <https://pypi.org/>`_ for use by
other people, read the PyPA guide to `Packaging and distributing projects
<https://packaging.python.org/tutorials/distributing-packages/>`_.

Static assets
-------------

If your plugin has a ``static/`` directory, Datasette will automatically
configure itself to serve those static assets from the following path::

    /-/static-plugins/NAME_OF_PLUGIN_PACKAGE/yourfile.js

See `the datasette-plugin-demos repository <https://github.com/simonw/datasette-plugin-demos/tree/0ccf9e6189e923046047acd7878d1d19a2cccbb1>`_
for an example of how to create a package that includes a static folder.

Custom templates
----------------

If your plugin has a ``templates/`` directory, Datasette will attempt to load
templates from that directory before it uses its own default templates.

The priority order for template loading is:

* templates from the ``--template-dir`` argument, if specified
* templates from the ``templates/`` directory in any installed plugins
* default templates that ship with Datasette

See :ref:`customization` for more details on how to write custom templates,
including which filenames to use to customize which parts of the Datasette UI.

.. _plugins_configuration:

Plugin configuration
--------------------

Plugins can have their own configuration, embedded in a :ref:`metadata` file. Configuration options for plugins live within a ``"plugins"`` key in that file, which can be included at the root, database or table level.

Here is an example of some plugin configuration for a specific table::

    {
        "databases: {
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

**As environment variables**. If your secret lives in an environment variable that is available to the Datasette process, you can indicate that the configuration value should be read from that environment variable like so::

    {
        "plugins": {
            "datasette-auth-github": {
                "client_secret": {
                    "$env": "GITHUB_CLIENT_SECRET"
                }
            }
        }
    }

**As values in separate files**. Your secrets can also live in files on disk. To specify a secret should be read from a file, provide the full file path like this::

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

.. _plugins_plugin_config:

Writing plugins that accept configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When you are writing plugins, you can access plugin configuration like this using the ``datasette.plugin_config()`` method. If you know you need plugin configuration for a specific table, you can access it like this::

    plugin_config = datasette.plugin_config(
        "datasette-cluster-map", database="sf-trees", table="Street_Tree_List"
    )

This will return the ``{"latitude_column": "lat", "longitude_column": "lng"}`` in the above example.

If it cannot find the requested configuration at the table layer, it will fall back to the database layer and then the root layer. For example, a user may have set the plugin configuration option like so::

    {
        "databases: {
            "sf-trees": {
                "plugins": {
                    "datasette-cluster-map": {
                        "latitude_column": "xlat",
                        "longitude_column": "xlng"
                    }
                }
            }
        }
    }

In this case, the above code would return that configuration for ANY table within the ``sf-trees`` database.

The plugin configuration could also be set at the top level of ``metadata.json``::

    {
        "title": "This is the top-level title in metadata.json",
        "plugins": {
            "datasette-cluster-map": {
                "latitude_column": "xlat",
                "longitude_column": "xlng"
            }
        }
    }

Now that ``datasette-cluster-map`` plugin configuration will apply to every table in every database.
