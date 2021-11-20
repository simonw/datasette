# Datasette running behind an Apache proxy

See also [Running Datasette behind a proxy](https://docs.datasette.io/en/latest/deploying.html#running-datasette-behind-a-proxy)

This live demo is running at https://apache-proxy-demo.datasette.io/

To build locally, passing in a Datasette commit hash (or `main` for the main branch):

    docker build -t datasette-apache-proxy-demo . \
      --build-arg DATASETTE_REF=c617e1769ea27e045b0f2907ef49a9a1244e577d

Then run it like this:

    docker run -p 5000:80 datasette-apache-proxy-demo

And visit `http://localhost:5000/` or `http://localhost:5000/prefix/`
