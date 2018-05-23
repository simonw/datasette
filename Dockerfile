FROM python:3.6-alpine as base
FROM base as builder
# Setup build dependencies
RUN apk add --no-cache --virtual build-base python3-dev gcc libsqlite3-mod-spatialite
# Add local code to the image instead of fetching from pypi.
ADD . /datasette

RUN mkdir /install

RUN pip install --install-option="--prefix=/install" /datasette

WORKDIR /install

FROM base

# Copy python dependencies
COPY --from=builder /install /usr

ENV PYTHONPATH=/usr/local/lib/python3.6/:/usr/lib/python3.6/site-packages

EXPOSE 8001
CMD ["datasette"]
