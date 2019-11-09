.. _publishing:

=================
 Publishing data
=================

Datasette includes tools for publishing and deploying your data to the internet. The ``datasette publish`` command will deploy a new Datasette instance containing your databases directly to a Heroku, Google Cloud or Zeit Now hosting account. You can also use ``datasette package`` to create a Docker image that bundles your databases together with the datasette application that is used to serve them.

.. _cli_publish:

datasette publish
=================

Once you have created a SQLite database (e.g. using `csvs-to-sqlite <https://github.com/simonw/csvs-to-sqlite/>`_) you can deploy it to a hosting account using a single command.

You will need a hosting account with `Heroku <https://www.heroku.com/>`__ or `Google Cloud <https://cloud.google.com/>`__. Once you have created your account you will need to install and configure the ``heroku`` or ``gcloud`` command-line tools.

Publishing to Heroku
--------------------

To publish your data using Heroku, first create an account there and install and configure the `Heroku CLI tool <https://devcenter.heroku.com/articles/heroku-cli>`_.

You can publish a database to Heroku using the following command::

    datasette publish heroku mydatabase.db

This will output some details about the new deployment, including a URL like this one::

    https://limitless-reef-88278.herokuapp.com/ deployed to Heroku

You can specify a custom app name by passing ``-n my-app-name`` to the publish command. This will also allow you to overwrite an existing app.

.. literalinclude:: datasette-publish-heroku-help.txt

.. _publish_cloud_run:

Publishing to Google Cloud Run
------------------------------

`Google Cloud Run <https://cloud.google.com/run/>`__ launched as a beta in in April 2019. It allows you to publish data in a scale-to-zero environment, so your application will start running when the first request is received and will shut down again when traffic ceases. This means you only pay for time spent serving traffic.

You will first need to install and configure the Google Cloud CLI tools by following `these instructions <https://cloud.google.com/sdk/>`__.

You can then publish a database to Google Cloud Run using the following command::

    datasette publish cloudrun mydatabase.db --service=my-database

A Cloud Run **service** is a single hosted application. The service name you specify will be used as part of the Cloud Run URL. If you deploy to a service name that you have used in the past your new deployment will replace the previous one.

If you omit the ``--service`` option you will be asked to pick a service name interactively during the deploy.

You may need to interact with prompts from the tool. Once it has finished it will output a URL like this one::

    Service [my-service] revision [my-service-00001] has been deployed
    and is serving traffic at https://my-service-j7hipcg4aq-uc.a.run.app

.. literalinclude:: datasette-publish-cloudrun-help.txt

Publishing to Zeit Now v1
-------------------------

Datasette can be deployed to Zeit Now's older v1 hosting platform. They no longer accept new signups for this service, so this option is currently only available if you created an account before January 2019.

To publish your database(s) to a new instance hosted by Zeit Now v1, install the `now cli tool <https://zeit.co/download>`__ and then run the following command::

    datasette publish nowv1 mydatabase.db

This will upload your database to Zeit Now, assign you a new URL and install and start a new instance of Datasette to serve your database.

The command will output a URL that looks something like this::

    https://datasette-elkksjmyfj.now.sh

You can navigate to this URL to see live logs of the deployment process. Your new Datasette instance will be available at that URL.

Once the deployment has completed, you can assign a custom URL to your instance using the ``now alias`` command::

    now alias https://datasette-elkksjmyfj.now.sh datasette-publish-demo.now.sh

You can use ``anything-you-like.now.sh``, provided no one else has already registered that alias.

You can also use custom domains, if you `first register them with Zeit Now <https://zeit.co/docs/features/aliases>`_.

.. literalinclude:: datasette-publish-nowv1-help.txt

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

datasette package
=================

If you have docker installed (e.g. using `Docker for Mac <https://www.docker.com/docker-mac>`_) you can use the ``datasette package`` command to create a new Docker image in your local repository containing the datasette app bundled together with your selected SQLite databases::

    datasette package mydatabase.db

Here's example output for the package command::

    $ datasette package parlgov.db --extra-options="--config sql_time_limit_ms:2500"
    Sending build context to Docker daemon  4.459MB
    Step 1/7 : FROM python:3
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
    Step 7/7 : CMD datasette serve parlgov.db --port 8001 --inspect-file inspect-data.json --config sql_time_limit_ms:2500
     ---> Using cache
     ---> 1bd380ea8af3
    Successfully built 1bd380ea8af3

You can now run the resulting container like so::

    docker run -p 8081:8001 1bd380ea8af3

This exposes port 8001 inside the container as port 8081 on your host machine, so you can access the application at ``http://localhost:8081/``

A full list of options can be seen by running ``datasette package --help``:

.. literalinclude:: datasette-package-help.txt
