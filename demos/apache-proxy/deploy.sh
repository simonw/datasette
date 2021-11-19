#!/bin/bash
# https://til.simonwillison.net/cloudrun/ship-dockerfile-to-cloud-run

NAME="datasette-apache-proxy-demo"
PROJECT=$(gcloud config get-value project)
IMAGE="gcr.io/$PROJECT/$NAME"

gcloud builds submit --tag $IMAGE
gcloud run deploy \
    --allow-unauthenticated \
    --platform=managed \
    --image $IMAGE $NAME \
    --port 80
