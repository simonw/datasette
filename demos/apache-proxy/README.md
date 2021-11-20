# Datasette running behind an Apache proxy

See also [Running Datasette behind a proxy](https://docs.datasette.io/en/latest/deploying.html#running-datasette-behind-a-proxy)

This live demo is running at https://datasette-apache-proxy-demo.fly.dev/prefix/

To build locally, passing in a Datasette commit hash (or `main` for the main branch):

    docker build -t datasette-apache-proxy-demo . \
      --build-arg DATASETTE_REF=c617e1769ea27e045b0f2907ef49a9a1244e577d

Then run it like this:

    docker run -p 5000:80 datasette-apache-proxy-demo

And visit `http://localhost:5000/` or `http://localhost:5000/prefix/`

## Deployment to Fly

To deploy to [Fly](https://fly.io/) first create an application there by running:

    flyctl apps create --name datasette-apache-proxy-demo

You will need a different name, since I have already taken that one.

Then run this command to deploy:

    flyctl deploy --build-arg DATASETTE_REF=main

This uses `fly.toml` in this directory, which hard-codes the `datasette-apache-proxy-demo` name - so you would need to edit that file to match your application name before running this.

## Deployment to Cloud Run

Deployments to Cloud Run currently result in intermittent 503 errors and I'm not sure why, see [issue #1522](https://github.com/simonw/datasette/issues/1522).

You can deploy like this:

    DATASETTE_REF=main ./deploy-to-cloud-run.sh
