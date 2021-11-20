FROM python:3-alpine

RUN apk add --no-cache \
	apache2 \
	apache2-proxy \
	bash

ARG DATASETTE_REF

RUN pip install https://github.com/simonw/datasette/archive/${DATASETTE_REF}.zip

ENV TINI_VERSION v0.18.0
ADD https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini-static /tini
RUN chmod +x /tini

# Append this to the end of the default httpd.conf file
RUN echo -e 'ServerName localhost\n\
\n\
<Proxy *>\n\
  Order deny,allow\n\
  Allow from all\n\
</Proxy>\n\
\n\
ProxyPreserveHost On\n\
ProxyPass /prefix/ http://localhost:8001/\n\
Header add X-Proxied-By "Apache2"' >> /etc/apache2/httpd.conf

RUN echo '<a href="/prefix/">Datasette</a>' > /var/www/localhost/htdocs/index.html

WORKDIR /app

ADD https://latest.datasette.io/fixtures.db /app/fixtures.db

RUN echo -e "#!/usr/bin/env bash\n\
datasette /app/fixtures.db --setting base_url '/prefix/' --version-note '${DATASETTE_REF}' -h 0.0.0.0 -p 8001 &\n\
\n\
httpd -D FOREGROUND & \n\
\n\
wait -n\n\
exit $?" > /app/start.sh

RUN chmod +x /app/start.sh

EXPOSE 80

CMD /tini -- /app/start.sh
