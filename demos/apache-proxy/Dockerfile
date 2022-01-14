FROM python:3.9.7-slim-bullseye

RUN apt-get update && \
    apt-get install -y apache2 supervisor && \
    apt clean && \
    rm -rf /var/lib/apt && \
    rm -rf /var/lib/dpkg/info/*

# Apache environment, copied from
# https://github.com/ijklim/laravel-benfords-law-app/blob/e9bf385dcaddb62ea466a7b245ab6e4ef708c313/docker/os/Dockerfile
ENV APACHE_DOCUMENT_ROOT=/var/www/html/public
ENV APACHE_RUN_USER www-data
ENV APACHE_RUN_GROUP www-data
ENV APACHE_PID_FILE /var/run/apache2.pid
ENV APACHE_RUN_DIR /var/run/apache2
ENV APACHE_LOCK_DIR /var/lock/apache2
ENV APACHE_LOG_DIR /var/log
RUN ln -sf /dev/stdout /var/log/apache2-access.log
RUN ln -sf /dev/stderr /var/log/apache2-error.log
RUN mkdir -p $APACHE_RUN_DIR $APACHE_LOCK_DIR

RUN a2enmod proxy
RUN a2enmod proxy_http
RUN a2enmod headers

ARG DATASETTE_REF

RUN pip install \
    https://github.com/simonw/datasette/archive/${DATASETTE_REF}.zip \
    datasette-redirect-to-https datasette-debug-asgi

ADD 000-default.conf /etc/apache2/sites-enabled/000-default.conf

WORKDIR /app
RUN mkdir -p /app/html
RUN echo '<h1><a href="/prefix/">Demo is at /prefix/</a></h1>' > /app/html/index.html

ADD https://latest.datasette.io/fixtures.db /app/fixtures.db

EXPOSE 80

# Dynamically build supervisord config since it includes $DATASETTE_REF:
RUN echo "[supervisord]" >> /app/supervisord.conf
RUN echo "nodaemon=true" >> /app/supervisord.conf
RUN echo "" >> /app/supervisord.conf
RUN echo "[program:apache2]" >> /app/supervisord.conf
RUN echo "command=apache2 -D FOREGROUND" >> /app/supervisord.conf
RUN echo "stdout_logfile=/dev/stdout" >> /app/supervisord.conf
RUN echo "stdout_logfile_maxbytes=0" >> /app/supervisord.conf
RUN echo "" >> /app/supervisord.conf
RUN echo "[program:datasette]" >> /app/supervisord.conf
RUN echo "command=datasette /app/fixtures.db --setting base_url '/prefix/' --version-note '${DATASETTE_REF}' -h 0.0.0.0 -p 8001" >> /app/supervisord.conf
RUN echo "stdout_logfile=/dev/stdout" >> /app/supervisord.conf
RUN echo "stdout_logfile_maxbytes=0" >> /app/supervisord.conf

CMD ["/usr/bin/supervisord", "-c", "/app/supervisord.conf"]
