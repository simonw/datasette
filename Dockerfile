FROM python:3.9.2-slim-buster as build

# Version of Datasette to install, e.g. 0.55
#   docker build . -t datasette --build-arg VERSION=0.55
ARG VERSION

# software-properties-common provides add-apt-repository
# which we need in order to install a more recent release
# of libsqlite3-mod-spatialite from the sid distribution
RUN apt-get update && \
    apt-get -y --no-install-recommends install software-properties-common && \
    add-apt-repository "deb http://httpredir.debian.org/debian sid main" && \
    apt-get update && \
    apt-get -t sid install -y --no-install-recommends libsqlite3-mod-spatialite && \
    apt-get remove -y software-properties-common && \
    apt clean && \
    rm -rf /var/lib/apt && \
    rm -rf /var/lib/dpkg/info/*

RUN pip install https://github.com/simonw/datasette/archive/refs/tags/${VERSION}.zip && \
    find /usr/local/lib -name '__pycache__' | xargs rm -r && \
    rm -rf /root/.cache/pip

EXPOSE 8001
CMD ["datasette"]
