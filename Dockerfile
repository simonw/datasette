FROM python:3

RUN apt-get update && apt-get install -y --no-install-recommends \
        libmagic-dev \
    && rm -rf /var/lib/apt/lists/*

COPY . /app
WORKDIR /app
RUN pip install -r requirements.txt
RUN python app.py --build
EXPOSE 8006
CMD ["python", "app.py"]
