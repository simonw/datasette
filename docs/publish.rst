.. _publishing:

=================
 Publishing data
=================

Datasette includes tools for publishing and deploying your data to the internet. The ``datasette publish`` command will deploy a new Datasette instance containing your databases directly to a Heroku or Google Cloud hosting account. You can also use ``datasette package`` to create a Docker image that bundles your databases together with the datasette application that is used to serve them.

.. _cli_publish:

datasette publish
=================

Once you have created a SQLite database (e.g. using `csvs-to-sqlite <https://github.com/simonw/csvs-to-sqlite/>`_) you can deploy it to a hosting account using a single command.

You will need a hosting account with `Heroku <https://www.heroku.com/>`__ or `Google Cloud <https://cloud.google.com/>`__. Once you have created your account you will need to install and configure the ``heroku`` or ``gcloud`` command-line tools.

.. _publish_cloud_run:

Publishing to Google Cloud Run
------------------------------

`Google Cloud Run <https://cloud.google.com/run/>`__ allows you to publish data in a scale-to-zero environment, so your application will start running when the first request is received and will shut down again when traffic ceases. This means you only pay for time spent serving traffic.

.. warning::
    Cloud Run is a great option for inexpensively hosting small, low traffic projects - but costs can add up for projects that serve a lot of requests.

    Be particularly careful if your project has tables with large numbers of rows. Search engine crawlers that index a page for every row could result in a high bill.

    The `datasette-block-robots <https://datasette.io/plugins/datasette-block-robots>`__ plugin can be used to request search engine crawlers omit crawling your site, which can help avoid this issue.

You will first need to install and configure the Google Cloud CLI tools by following `these instructions <https://cloud.google.com/sdk/>`__.

You can then publish one or more SQLite database files to Google Cloud Run using the following command::

    datasette publish cloudrun mydatabase.db --service=my-database

A Cloud Run **service** is a single hosted application. The service name you specify will be used as part of the Cloud Run URL. If you deploy to a service name that you have used in the past your new deployment will replace the previous one.

If you omit the ``--service`` option you will be asked to pick a service name interactively during the deploy.

You may need to interact with prompts from the tool. Many of the prompts ask for values that can be `set as properties for the Google Cloud SDK <https://cloud.google.com/sdk/docs/properties>`_ if you want to avoid the prompts. 

For example, the default region for the deployed instance can be set using the command::

    gcloud config set run/region us-central1
    
You should replace ``us-central1`` with your desired `region <https://cloud.google.com/about/locations>`_. Alternately, you can specify the region by setting the ``CLOUDSDK_RUN_REGION`` environment variable. 

Once it has finished it will output a URL like this one::

    Service [my-service] revision [my-service-00001] has been deployed
    and is serving traffic at https://my-service-j7hipcg4aq-uc.a.run.app

Cloud Run provides a URL on the ``.run.app`` domain, but you can also point your own domain or subdomain at your Cloud Run service - see `mapping custom domains <https://cloud.google.com/run/docs/mapping-custom-domains>`__ in the Cloud Run documentation for details.

See :ref:`cli_help_publish_cloudrun___help` for the full list of options for this command.

.. _publish_heroku:

Publishing to Heroku
--------------------

To publish your data using `Heroku <https://www.heroku.com/>`__, first create an account there and install and configure the `Heroku CLI tool <https://devcenter.heroku.com/articles/heroku-cli>`_.

You can publish one or more databases to Heroku using the following command::

    datasette publish heroku mydatabase.db

This will output some details about the new deployment, including a URL like this one::

    https://limitless-reef-88278.herokuapp.com/ deployed to Heroku

You can specify a custom app name by passing ``-n my-app-name`` to the publish command. This will also allow you to overwrite an existing app.

Rather than deploying directly you can use the ``--generate-dir`` option to output the files that would be deployed to a directory::

    datasette publish heroku mydatabase.db --generate-dir=/tmp/deploy-this-to-heroku

See :ref:`cli_help_publish_heroku___help` for the full list of options for this command.

.. _publish_vercel:

Publishing to Vercel
--------------------

`Vercel <https://vercel.com/>`__  - previously known as Zeit Now - provides a layer over AWS Lambda to allow for quick, scale-to-zero deployment. You can deploy Datasette instances to Vercel using the `datasette-publish-vercel <https://github.com/simonw/datasette-publish-vercel>`__ plugin.

::

    pip install datasette-publish-vercel
    datasette publish vercel mydatabase.db --project my-database-project

Not every feature is supported: consult the `datasette-publish-vercel README <https://github.com/simonw/datasette-publish-vercel/blob/main/README.md>`__ for more details.

.. _publish_fly:

Publishing to Fly
-----------------

`Fly <https://fly.io/>`__ is a `competitively priced <https://fly.io/docs/pricing/>`__ Docker-compatible hosting platform that supports running applications in globally distributed data centers close to your end users. You can deploy Datasette instances to Fly using the `datasette-publish-fly <https://github.com/simonw/datasette-publish-fly>`__ plugin.

::

    pip install datasette-publish-fly
    datasette publish fly mydatabase.db --app="my-app"

Consult the `datasette-publish-fly README <https://github.com/simonw/datasette-publish-fly/blob/main/README.md>`__ for more details.

.. _publish_custom_metadata_and_plugins:

Custom metadata and plugins
---------------------------

``datasette publish`` accepts a number of additional options which can be used to further customize your Datasette instance.

You can define your own :ref:`metadata` and deploy that with your instance like so::

    datasette publish cloudrun --service=my-service mydatabase.db -m metadata.json

If you just want to set the title, license or source information you can do that directly using extra options to ``datasette publish``::

    datasette publish cloudrun mydatabase.db --service=my-service \
        --title="Title of my database" \
        --source="Where the data originated" \
        --source_url="http://www.example.com/"

You can also specify plugins you would like to install. For example, if you want to include the `datasette-vega <https://github.com/simonw/datasette-vega>`_ visualization plugin you can use the following::

    datasette publish cloudrun mydatabase.db --service=my-service --install=datasette-vega

If a plugin has any :ref:`plugins_configuration_secret` you can use the ``--plugin-secret`` option to set those secrets at publish time. For example, using Heroku with `datasette-auth-github <https://github.com/simonw/datasette-auth-github>`__ you might run the following command::

    $ datasette publish heroku my_database.db \
        --name my-heroku-app-demo \
        --install=datasette-auth-github \
        --plugin-secret datasette-auth-github client_id your_client_id \
        --plugin-secret datasette-auth-github client_secret your_client_secret

.. _cli_package:

datasette package
=================

If you have docker installed (e.g. using `Docker for Mac <https://www.docker.com/docker-mac>`_) you can use the ``datasette package`` command to create a new Docker image in your local repository containing the datasette app bundled together with one or more SQLite databases::

    datasette package mydatabase.db

Here's example output for the package command::

    $ datasette package parlgov.db --extra-options="--setting sql_time_limit_ms 2500"
    Sending build context to Docker daemon  4.459MB
    Step 1/7 : FROM python:3.11.0-slim-bullseye
     ---> 79e1dc9af1c1
    Step 2/7 : COPY . /app
     ---> Using cache
     ---> cd4ec67de656
    Step 3/7 : WORKDIR /app
     ---> Using cache
     ---> 139699e91621
    Step 4/7 : RUN pip install datasette
     ---> Using cache
     ---> 340efa82bfd7
    Step 5/7 : RUN datasette inspect parlgov.db --inspect-file inspect-data.json
     ---> Using cache
     ---> 5fddbe990314
    Step 6/7 : EXPOSE 8001
     ---> Using cache
     ---> 8e83844b0fed
    Step 7/7 : CMD datasette serve parlgov.db --port 8001 --inspect-file inspect-data.json --setting sql_time_limit_ms 2500
     ---> Using cache
     ---> 1bd380ea8af3
    Successfully built 1bd380ea8af3

You can now run the resulting container like so::

    docker run -p 8081:8001 1bd380ea8af3

This exposes port 8001 inside the container as port 8081 on your host machine, so you can access the application at ``http://localhost:8081/``

You can customize the port that is exposed by the container using the ``--port`` option::

    datasette package mydatabase.db --port 8080

A full list of options can be seen by running ``datasette package --help``:

See :ref:`cli_help_package___help` for the full list of options for this command.
