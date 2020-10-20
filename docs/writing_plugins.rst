.. _writing_plugins:

Writing plugins
===============

You can write one-off plugins that apply to just one Datasette instance, or you can write plugins which can be installed using ``pip`` and can be shipped to the Python Package Index (`PyPI <https://pypi.org/>`__) for other people to install.

.. _writing_plugins_one_off:

Writing one-off plugins
-----------------------

The easiest way to write a plugin is to create a ``my_plugin.py`` file and drop it into your ``plugins/`` directory. Here is an example plugin, which adds a new custom SQL function called ``hello_world()`` which takes no arguments and returns the string ``Hello world!``.

.. code-block:: python

    from datasette import hookimpl

    @hookimpl
    def prepare_connection(conn):
        conn.create_function('hello_world', 0, lambda: 'Hello world!')

If you save this in ``plugins/my_plugin.py`` you can then start Datasette like this::

    datasette serve mydb.db --plugins-dir=plugins/

Now you can navigate to http://localhost:8001/mydb and run this SQL::

    select hello_world();

To see the output of your plugin.

.. _writing_plugins_cookiecutter:

Starting an installable plugin using cookiecutter
-------------------------------------------------

Plugins that can be installed should be written as Python packages using a ``setup.py`` file.

The easiest way to start writing one an installable plugin is to use the `datasette-plugin <https://github.com/simonw/datasette-plugin>`__ cookiecutter template. This creates a new plugin structure for you complete with an example test and GitHub Actions workflows for testing and publishing your plugin.

`Install cookiecutter <https://cookiecutter.readthedocs.io/en/1.7.2/installation.html>`__ and then run this command to start building a plugin using the template::

    cookiecutter gh:simonw/datasette-plugin

Read `a cookiecutter template for writing Datasette plugins <https://simonwillison.net/2020/Jun/20/cookiecutter-plugins/>`__ for more information about this template.

.. _writing_plugins_packaging:

Packaging a plugin
------------------

Plugins can be packaged using Python setuptools. You can see an example of a packaged plugin at https://github.com/simonw/datasette-plugin-demos

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

And a Python module file, ``datasette_plugin_demos.py``, that implements the plugin:

.. code-block:: python

    from datasette import hookimpl
    import random


    @hookimpl
    def prepare_jinja2_environment(env):
        env.filters['uppercase'] = lambda u: u.upper()


    @hookimpl
    def prepare_connection(conn):
        conn.create_function('random_integer', 2, random.randint)


Having built a plugin in this way you can turn it into an installable package using the following command::

    python3 setup.py sdist

This will create a ``.tar.gz`` file in the ``dist/`` directory.

You can then install your new plugin into a Datasette virtual environment or Docker container using ``pip``::

    pip install datasette-plugin-demos-0.1.tar.gz

To learn how to upload your plugin to `PyPI <https://pypi.org/>`_ for use by other people, read the PyPA guide to `Packaging and distributing projects <https://packaging.python.org/tutorials/distributing-packages/>`_.

.. _writing_plugins_static_assets:

Static assets
-------------

If your plugin has a ``static/`` directory, Datasette will automatically configure itself to serve those static assets from the following path::

    /-/static-plugins/NAME_OF_PLUGIN_PACKAGE/yourfile.js

See `the datasette-plugin-demos repository <https://github.com/simonw/datasette-plugin-demos/tree/0ccf9e6189e923046047acd7878d1d19a2cccbb1>`_ for an example of how to create a package that includes a static folder.

.. _writing_plugins_custom_templates:

Custom templates
----------------

If your plugin has a ``templates/`` directory, Datasette will attempt to load templates from that directory before it uses its own default templates.

The priority order for template loading is:

* templates from the ``--template-dir`` argument, if specified
* templates from the ``templates/`` directory in any installed plugins
* default templates that ship with Datasette

See :ref:`customization` for more details on how to write custom templates, including which filenames to use to customize which parts of the Datasette UI.

.. _writing_plugins_configuration:

Writing plugins that accept configuration
-----------------------------------------

When you are writing plugins, you can access plugin configuration like this using the ``datasette plugin_config()`` method. If you know you need plugin configuration for a specific table, you can access it like this::

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

.. _writing_plugins_building_urls:

Building URLs within plugins
----------------------------

Plugins that define their own custom user interface elements may need to link to other pages within Datasette.

This can be a bit tricky if the Datasette instance is using the :ref:`config_base_url` configuration setting to run behind a proxy, since that can cause Datasette's URLs to include an additional prefix.

The ``datasette.urls`` object provides internal methods for correctly generating URLs to different pages within Datasette, taking any ``base_url`` configuration into account.

This object is exposed in templates as the ``urls`` variable, which can be used like this:

.. code-block:: jinja

    Back to the <a href="{{ urls.instance() }}">Homepage</a>

See :ref:`internals_datasette_urls` for full details on this object.
