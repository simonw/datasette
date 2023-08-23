.. _configuration:

Configuration
========

Datasette offers many way to configure your Datasette instances: server settings, plugin configuration, authentication, and more.

To facilitate this, You can provide a `datasette.yaml` configuration file to datasette with the ``--config``/ ``-c`` flag:

    datasette mydatabase.db --config datasette.yaml
