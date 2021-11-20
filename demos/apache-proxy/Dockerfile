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

RUN pip install https://github.com/simonw/datasette/archive/${DATASETTE_REF}.zip

# Append this to the end of the default httpd.conf file
RUN echo '\n\
<Directory /app/html/>\n\
    Options Indexes FollowSymLinks\n\
    AllowOverride None\n\
    Require all granted\n\
</Directory>\n\
\n\
<VirtualHost *:80>\n\
    ServerName localhost\n\
    DocumentRoot /app/html\n\
    ProxyPreserveHost On\n\
    ProxyPass /prefix/ http://127.0.0.1:8001/\n\
    Header add X-Proxied-By "Apache2 Debian"\n\
</VirtualHost>\n\
' > /etc/apache2/sites-enabled/000-default.conf

WORKDIR /app
RUN mkdir -p /app/html
RUN echo '<a href="/prefix/">Datasette</a>' > /app/html/index.html

ADD https://latest.datasette.io/fixtures.db /app/fixtures.db

EXPOSE 80

RUN echo "[supervisord]" >> /app/supervisord.conf
RUN echo "nodaemon=true" >> /app/supervisord.conf
RUN echo "" >> /app/supervisord.conf
RUN echo "[program:apache2]" >> /app/supervisord.conf
RUN echo "command=apache2 -D FOREGROUND" >> /app/supervisord.conf
RUN echo "" >> /app/supervisord.conf
RUN echo "[program:datasette]" >> /app/supervisord.conf
RUN echo "command=datasette /app/fixtures.db --setting base_url '/prefix/' --version-note '${DATASETTE_REF}' -h 0.0.0.0 -p 8001" >> /app/supervisord.conf

CMD ["/usr/bin/supervisord", "-c", "/app/supervisord.conf"]
