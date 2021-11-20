#!/bin/bash
# https://til.simonwillison.net/cloudrun/using-build-args-with-cloud-run

if [[ -z "$DATASETTE_REF" ]]; then
    echo "Must provide DATASETTE_REF environment variable" 1>&2
    exit 1
fi

NAME="datasette-apache-proxy-demo"
PROJECT=$(gcloud config get-value project)
IMAGE="gcr.io/$PROJECT/$NAME"

# Need YAML so we can set --build-arg
echo "
steps:
- name: 'gcr.io/cloud-builders/docker'
  args: ['build', '-t', '$IMAGE', '.', '--build-arg', 'DATASETTE_REF=$DATASETTE_REF']
- name: 'gcr.io/cloud-builders/docker'
  args: ['push', '$IMAGE']
" > /tmp/cloudbuild.yml

gcloud builds submit --config /tmp/cloudbuild.yml

rm /tmp/cloudbuild.yml

gcloud run deploy $NAME \
  --allow-unauthenticated \
  --platform=managed \
  --image $IMAGE \
  --port 80
