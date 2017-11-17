FROM python:3.6-slim-stretch as build

# Setup build dependencies
RUN apt update
RUN apt install -y python-dev gcc libsqlite3-mod-spatialite
# Add local code to the image instead of fetching from pypi.
ADD . /datasette

RUN pip install /datasette

FROM python:3.6-slim-stretch

# Copy python dependencies
COPY --from=build /usr/local/lib/python3.6/site-packages /usr/local/lib/python3.6/site-packages
# Copy executables
COPY --from=build /usr/local/bin /usr/local/bin
# Copy spatial extensions
COPY --from=build /usr/lib/x86_64-linux-gnu /usr/lib/x86_64-linux-gnu

EXPOSE 8001
CMD ["datasette"]
