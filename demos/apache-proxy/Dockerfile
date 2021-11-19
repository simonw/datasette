FROM python:3-alpine

RUN apk add --no-cache \
	apache2 \
	apache2-proxy \
	bash

RUN pip install datasette

ENV TINI_VERSION v0.18.0
ADD https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini-static /tini
RUN chmod +x /tini

# Append this to the end of the default httpd.conf file
RUN echo $'ServerName localhost\n\
\n\
<Proxy *>\n\
  Order deny,allow\n\
  Allow from all\n\
</Proxy>\n\
\n\
ProxyPass        /prefix/ http://localhost:8001/\n\
Header add X-Proxied-By "Apache2"' >> /etc/apache2/httpd.conf

RUN echo $'<a href="/prefix/">Datasette</a>' > /var/www/localhost/htdocs/index.html

WORKDIR /app

ADD https://latest.datasette.io/fixtures.db /app/fixtures.db

RUN echo $'#!/usr/bin/env bash\n\
set -e\n\
\n\
httpd -D FOREGROUND &\n\
datasette fixtures.db --setting base_url "/prefix/" -h 0.0.0.0 -p 8001 &\n\
\n\
wait -n' > /app/start.sh

RUN chmod +x /app/start.sh

EXPOSE 80
ENTRYPOINT ["/tini", "--", "/app/start.sh"]
