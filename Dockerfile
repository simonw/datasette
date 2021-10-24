FROM ubuntu:20.10

# Version of Datasette to install, e.g. 0.55
#   docker build . -t datasette --build-arg VERSION=0.55
ARG VERSION

RUN apt-get update && \
    apt-get -y --no-install-recommends install python3 python3-pip libsqlite3-mod-spatialite && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN pip install https://github.com/simonw/datasette/archive/refs/tags/${VERSION}.zip && \
    rm -rf /root/.cache/pip

EXPOSE 8001
CMD ["datasette"]
