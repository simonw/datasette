FROM python:3.6-slim-stretch as build

# Setup build dependencies
RUN apt update \
&& apt install -y python3-dev build-essential wget libxml2 libxml2-dev libproj-dev libgeos-dev libsqlite3-dev zlib1g-dev pkg-config unzip \
 && apt clean


RUN wget "https://www.sqlite.org/2018/sqlite-autoconf-3230100.tar.gz" && tar xzvf sqlite-autoconf-3230100.tar.gz \
    && cd sqlite-autoconf-3230100 && ./configure --disable-static --enable-fts5 --enable-json1 CFLAGS="-g -O2 -DSQLITE_ENABLE_FTS3=1 -DSQLITE_ENABLE_FTS4=1 -DSQLITE_ENABLE_RTREE=1" \
    && make && make install

RUN wget "https://www.gaia-gis.it/gaia-sins/freexl-1.0.5.tar.gz" && tar zxvf freexl-1.0.5.tar.gz && cd freexl-1.0.5 && ./configure && make && make install

RUN wget "https://www.gaia-gis.it/gaia-sins/libspatialite-4.4.0-RC0.tar.gz" && tar zxvf libspatialite-4.4.0-RC0.tar.gz
RUN cd libspatialite-4.4.0-RC0 && ./configure && make && make install

RUN wget "https://www.gaia-gis.it/gaia-sins/readosm-1.1.0.tar.gz" && tar zxvf readosm-1.1.0.tar.gz && cd readosm-1.1.0 && ./configure && make && make install

RUN wget "https://www.gaia-gis.it/gaia-sins/spatialite-tools-4.4.0-RC0.tar.gz" && tar zxvf spatialite-tools-4.4.0-RC0.tar.gz && cd spatialite-tools-4.4.0-RC0 && ./configure && make && make install


# Add local code to the image instead of fetching from pypi.
ADD . /datasette

RUN pip install /datasette


FROM python:3.6-slim-stretch

# Copy python dependencies
COPY --from=build /usr/local/lib/ /usr/local/lib/
# Copy executables
COPY --from=build /usr/local/bin /usr/local/bin
# Copy spatial extensions
COPY --from=build /usr/lib/x86_64-linux-gnu /usr/lib/x86_64-linux-gnu

ENV LD_LIBRARY_PATH=/usr/local/lib

EXPOSE 8001
CMD ["datasette"]
