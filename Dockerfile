FROM python:3.6 as build

ARG VERSION=0.11
RUN pip install datasette==$VERSION

FROM python:3.6-slim

COPY --from=build /usr/local/lib/python3.6/site-packages /usr/local/lib/python3.6/site-packages
COPY --from=build /usr/local/bin /usr/local/bin

EXPOSE 8001
CMD ["datasette"]
