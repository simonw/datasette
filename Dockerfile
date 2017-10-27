FROM python:3
COPY . /app
WORKDIR /app
RUN pip install .
RUN datasite build
EXPOSE 8006
CMD ["datasite", "serve", "--port", "8006"]
