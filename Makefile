DOCKER_BUILDKIT=1
IMAGE_NAME="heussd/rpi-datasette:latest"

all: setup build clean
.PHONY: all

setup:
	docker buildx create --name "nubuilder" --use
build:
	docker buildx build --platform linux/amd64,linux/arm/v7,linux/arm/v6 -t $(IMAGE_NAME) --push .
clean:
	docker buildx rm nubuilder