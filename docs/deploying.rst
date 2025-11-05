.. _deploying:

=====================
 Deploying Datasette
=====================

The quickest way to deploy a Datasette instance on the internet is to use the ``datasette publish`` command, described in :ref:`publishing`. This can be used to quickly deploy Datasette to a number of hosting providers including Heroku, Google Cloud Run and Vercel.

You can deploy Datasette to other hosting providers using the instructions on this page.

.. _deploying_fundamentals:

Deployment fundamentals
=======================

Datasette can be deployed as a single ``datasette`` process that listens on a port. Datasette is not designed to be run as root, so that process should listen on a higher port such as port 8000.

If you want to serve Datasette on port 80 (the HTTP default port) or port 443 (for HTTPS) you should run it behind a proxy server, such as nginx, Apache or HAProxy. The proxy server can listen on port 80/443 and forward traffic on to Datasette.

.. _deploying_systemd:

Running Datasette using systemd
===============================

You can run Datasette on Ubuntu or Debian systems using ``systemd``.

First, ensure you have Python 3 and ``pip`` installed. On Ubuntu you can use ``sudo apt-get install python3 python3-pip``.

You can install Datasette into a virtual environment, or you can install it system-wide. To install system-wide, use ``sudo pip3 install datasette``.

Now create a folder for your Datasette databases, for example using ``mkdir /home/ubuntu/datasette-root``.

You can copy a test database into that folder like so::

    cd /home/ubuntu/datasette-root
    curl -O https://latest.datasette.io/fixtures.db

Create a file at ``/etc/systemd/system/datasette.service`` with the following contents:

.. code-block:: ini

    [Unit]
    Description=Datasette
    After=network.target

    [Service]
    Type=simple
    User=ubuntu
    Environment=DATASETTE_SECRET=
    WorkingDirectory=/home/ubuntu/datasette-root
    ExecStart=datasette serve . -h 127.0.0.1 -p 8000
    Restart=on-failure

    [Install]
    WantedBy=multi-user.target

Add a random value for the ``DATASETTE_SECRET`` - this will be used to sign Datasette cookies such as the CSRF token cookie. You can generate a suitable value like so::

    $ python3 -c 'import secrets; print(secrets.token_hex(32))'

This configuration will run Datasette against all database files contained in the ``/home/ubuntu/datasette-root`` directory. If that directory contains a ``metadata.yml`` (or ``.json``) file or a ``templates/`` or ``plugins/`` sub-directory those will automatically be loaded by Datasette - see :ref:`config_dir` for details.

You can start the Datasette process running using the following::

    sudo systemctl daemon-reload
    sudo systemctl start datasette.service

You will need to restart the Datasette service after making changes to its ``metadata.json`` configuration or adding a new database file to that directory. You can do that using::

    sudo systemctl restart datasette.service

Once the service has started you can confirm that Datasette is running on port 8000 like so::

    curl 127.0.0.1:8000/-/versions.json
    # Should output JSON showing the installed version

Datasette will not be accessible from outside the server because it is listening on ``127.0.0.1``. You can expose it by instead listening on ``0.0.0.0``, but a better way is to set up a proxy such as ``nginx`` - see :ref:`deploying_proxy`.

.. _deploying_openrc:

Running Datasette using OpenRC
===============================
OpenRC is the service manager on non-systemd Linux distributions like `Alpine Linux <https://www.alpinelinux.org/>`__ and `Gentoo <https://www.gentoo.org/>`__.

Create an init script at ``/etc/init.d/datasette`` with the following contents:

.. code-block:: sh

    #!/sbin/openrc-run

    name="datasette"
    command="datasette"
    command_args="serve -h 0.0.0.0 /path/to/db.db"
    command_background=true
    pidfile="/run/${RC_SVCNAME}.pid"

You then need to configure the service to run at boot and start it::

    rc-update add datasette
    rc-service datasette start

.. _deploying_buildpacks:

Deploying using buildpacks
==========================

Some hosting providers such as `Heroku <https://www.heroku.com/>`__, `DigitalOcean App Platform <https://www.digitalocean.com/docs/app-platform/>`__ and `Scalingo <https://scalingo.com/>`__ support the `Buildpacks standard <https://buildpacks.io/>`__ for deploying Python web applications.

Deploying Datasette on these platforms requires two files: ``requirements.txt`` and ``Procfile``.

The ``requirements.txt`` file lets the platform know which Python packages should be installed. It should contain ``datasette`` at a minimum, but can also list any Datasette plugins you wish to install - for example::

    datasette
    datasette-vega

The ``Procfile`` lets the hosting platform know how to run the command that serves web traffic. It should look like this::

    web: datasette . -h 0.0.0.0 -p $PORT --cors

The ``$PORT`` environment variable is provided by the hosting platform. ``--cors`` enables CORS requests from JavaScript running on other websites to your domain - omit this if you don't want to allow CORS. You can add additional Datasette :ref:`settings` options here too.

These two files should be enough to deploy Datasette on any host that supports buildpacks. Datasette will serve any SQLite files that are included in the root directory of the application.

If you want to build SQLite files or download them as part of the deployment process you can do so using a ``bin/post_compile`` file. For example, the following ``bin/post_compile`` will download an example database that will then be served by Datasette::

    wget https://fivethirtyeight.datasettes.com/fivethirtyeight.db

`simonw/buildpack-datasette-demo <https://github.com/simonw/buildpack-datasette-demo>`__ is an example GitHub repository showing a Datasette configuration that can be deployed to a buildpack-supporting host.

.. _deploying_proxy:

Running Datasette behind a proxy
================================

You may wish to run Datasette behind an Apache or nginx proxy, using a path within your existing site.

You can use the :ref:`setting_base_url` configuration setting to tell Datasette to serve traffic with a specific URL prefix. For example, you could run Datasette like this::

    datasette my-database.db --setting base_url /my-datasette/ -p 8009

This will run Datasette with the following URLs:

- ``http://127.0.0.1:8009/my-datasette/`` - the Datasette homepage
- ``http://127.0.0.1:8009/my-datasette/my-database`` - the page for the ``my-database.db`` database
- ``http://127.0.0.1:8009/my-datasette/my-database/some_table`` - the page for the ``some_table`` table

You can now set your nginx or Apache server to proxy the ``/my-datasette/`` path to this Datasette instance.

Nginx proxy configuration
-------------------------

Here is an example of an `nginx <https://nginx.org/>`__ configuration file that will proxy traffic to Datasette::

    daemon off;

    events {
      worker_connections  1024;
    }
    http {
      server {
        listen 80;
        location /my-datasette {
          proxy_pass http://127.0.0.1:8009/my-datasette;
          proxy_set_header Host $host;
        }
      }
    }

You can also use the ``--uds`` option to Datasette to listen on a Unix domain socket instead of a port, configuring the nginx upstream proxy like this::

    daemon off;
    events {
      worker_connections  1024;
    }
    http {
      server {
        listen 80;
        location /my-datasette {
          proxy_pass http://datasette/my-datasette;
          proxy_set_header Host $host;
        }
      }
      upstream datasette {
        server unix:/tmp/datasette.sock;
      }
    }

Then run Datasette with ``datasette --uds /tmp/datasette.sock path/to/database.db --setting base_url /my-datasette/``.

Apache proxy configuration
--------------------------

For `Apache <https://httpd.apache.org/>`__, you can use the ``ProxyPass`` directive. First make sure the following lines are uncommented::

    LoadModule proxy_module lib/httpd/modules/mod_proxy.so
    LoadModule proxy_http_module lib/httpd/modules/mod_proxy_http.so

Then add these directives to proxy traffic::

    ProxyPass /my-datasette/ http://127.0.0.1:8009/my-datasette/
    ProxyPreserveHost On

A live demo of Datasette running behind Apache using this proxy setup can be seen at `datasette-apache-proxy-demo.datasette.io/prefix/ <https://datasette-apache-proxy-demo.datasette.io/prefix/>`__. The code for that demo can be found in the `demos/apache-proxy <https://github.com/simonw/datasette/tree/main/demos/apache-proxy>`__ directory.

Using ``--uds`` you can use Unix domain sockets similar to the nginx example::

    ProxyPass /my-datasette/ unix:/tmp/datasette.sock|http://localhost/my-datasette/

The `ProxyPreserveHost On <https://httpd.apache.org/docs/2.4/mod/mod_proxy.html#proxypreservehost>`__ directive ensures that the original ``Host:`` header from the incoming request is passed through to Datasette. Datasette needs this to correctly assemble links to other pages using the :ref:`datasette_absolute_url` method.
